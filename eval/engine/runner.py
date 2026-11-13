"""Eval engine runner (plan S2.14). Runs a `Suite` (items x reps) through ordered `Stage`s with a stage cache,
sync or Batch mode, a spend ceiling and resumable on-disk state.

    stages = [Stage("retrieval", key_fn, run_fn),
              Stage("generation", key_fn, run_fn, deps=("retrieval",), batch=BatchSpec(...)),
              Stage("judge", key_fn, run_fn, deps=("generation",))]
    result = await Engine(stages, ledger=SpendLedger(DbCostSource(sm), budget)).run(suite, RunConfig(...))

Stage contract
- `key_fn() -> StageKey`: the stage's config, `prompt@version` and resolved model. Changing any of them
  changes the stage's cache key and, through `deps`, the key of every downstream stage, so only stages at or
  after the change re-run (retrieval stays cached when only the answer prompt version changes).
- `run_fn(ctx) -> StageOutput`: `value` must be JSON-serialisable (it is cached and written to
  results.jsonl); `call_ids` are the `llm_calls` ids the call created (CallResult.call_ids), which is what the
  spend ledger sums. Exceptions (not BaseException) mark the unit errored; errors are never cached.
- `per_rep=False` (default) shares one result across repetitions (deterministic stages such as retrieval).
- `batch=BatchSpec(request_fn, parse_fn)`: used instead of `run_fn` in `mode="batch"`; parse_fn must return a
  value of the same shape as run_fn does (parity).

Stages must be listed in dependency order. Sync mode runs unit by unit (a unit's record is appended to
results.jsonl as soon as it finishes); batch mode runs stage by stage, one batch per batchable stage.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import pathlib
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from eval.engine.batch import BatchBackend, BatchRequest, BatchResponse, run_batch
from eval.engine.cache import StageCache, StageKey, cache_key, digest
from eval.engine.ledger import SpendLedger
from eval.engine.state import RUNS_DIR, RunDir, RunState

__all__ = [
    "BatchSpec",
    "Engine",
    "Item",
    "RunConfig",
    "RunResult",
    "Stage",
    "StageCtx",
    "StageOutput",
    "Suite",
]

Mode = Literal["sync", "batch"]


@dataclass(frozen=True)
class Item:
    id: str
    payload: Any = None  # JSON-able; hashed into every cache key so a changed item is a miss


@dataclass(frozen=True)
class Suite:
    name: str
    items: Sequence[Item]


@dataclass(frozen=True)
class StageOutput:
    value: Any
    call_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class StageCtx:
    unit: str  # "<item>#r<rep>"
    item: Item
    rep: int
    upstream: Mapping[str, Any]  # values of the stages this one depends on (and earlier ones)


@dataclass(frozen=True)
class BatchSpec:
    request_fn: Callable[[StageCtx], BatchRequest]
    parse_fn: Callable[[StageCtx, BatchResponse], Awaitable[StageOutput]]


@dataclass(frozen=True)
class Stage:
    name: str
    key_fn: Callable[[], StageKey]
    run_fn: Callable[[StageCtx], Awaitable[StageOutput]]
    deps: tuple[str, ...] = ()
    per_rep: bool = False
    batch: BatchSpec | None = None


@dataclass
class RunConfig:
    name: str
    run_id: str | None = None  # reuse an id to resume that run
    mode: Mode = "sync"
    reps: int = 1
    runs_dir: pathlib.Path = RUNS_DIR
    batch_poll_s: float = 30.0


@dataclass
class RunResult:
    run_id: str
    run_dir: pathlib.Path
    status: str  # completed | stopped
    stopped: str | None
    spent_usd: Decimal
    units: dict[str, dict[str, Any]]  # unit -> {"unit","item","rep","stages":{name: value},"error"}
    executed: Counter[str] = field(default_factory=Counter)  # stage -> times run_fn / batch response parsed
    cached: Counter[str] = field(default_factory=Counter)  # stage -> served from cache or memo
    resumed_units: int = 0

    @property
    def errors(self) -> dict[str, str]:
        return {u: r["error"] for u, r in self.units.items() if r.get("error")}


class _Stop(Exception):
    """Internal: the budget ceiling was reached before the next call."""


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


class Engine:
    def __init__(
        self,
        stages: Sequence[Stage],
        *,
        ledger: SpendLedger,
        cache: StageCache | None = None,
        backend: BatchBackend | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        names = [s.name for s in stages]
        if len(set(names)) != len(names):
            raise ValueError("duplicate stage names")
        for i, s in enumerate(stages):
            bad = [d for d in s.deps if d not in names[:i]]
            if bad:
                raise ValueError(f"stage {s.name!r} depends on {bad}, which must be listed before it")
        self.stages, self.ledger, self.cache, self.backend, self._sleep = (
            stages,
            ledger,
            cache,
            backend,
            sleep,
        )
        self._by_name = {s.name: s for s in stages}

    # --- keys -------------------------------------------------------------------------------------

    def _stage_keys(self, item: Item, rep: int) -> dict[str, str]:
        keys: dict[str, str] = {}
        item_digest = digest(item.payload)
        for s in self.stages:
            keys[s.name] = cache_key(
                item_id=item.id,
                item_digest=item_digest,
                stage=s.name,
                key=s.key_fn(),
                rep=rep if s.per_rep else None,
                upstream={d: keys[d] for d in s.deps},
            )
        return keys

    def _config_hash(self, suite: Suite, reps: int) -> str:
        return digest(
            {
                "suite": suite.name,
                "reps": reps,
                "items": [(i.id, digest(i.payload)) for i in suite.items],
                "stages": [(s.name, s.key_fn().as_json(), s.deps, s.per_rep) for s in self.stages],
            }
        )

    # --- run --------------------------------------------------------------------------------------

    async def run(self, suite: Suite, config: RunConfig) -> RunResult:
        if config.mode == "batch" and self.backend is None:
            raise ValueError("mode='batch' needs a batch backend")
        if config.reps < 1:
            raise ValueError("reps must be >= 1")
        run_id = config.run_id or str(uuid.uuid4())
        rd = RunDir(config.runs_dir / run_id)
        chash = self._config_hash(suite, config.reps)
        units = [(f"{i.id}#r{r}", i, r) for i in suite.items for r in range(config.reps)]
        state = rd.load_state()
        if state is None:
            state = RunState(
                run_id=run_id,
                name=config.name,
                config_hash=chash,
                mode=config.mode,
                n_units=len(units),
                started_at=_now(),
                budget_usd=float(self.ledger.budget_usd) if self.ledger.budget_usd is not None else None,
            )
        elif state.config_hash != chash:
            raise ValueError(f"run {run_id} exists with a different suite/stage config; use a new run id")
        else:
            state.status, state.stopped, state.mode = "running", None, config.mode
        rd.repair_results()
        await self.ledger.restore(uuid.UUID(c) for c in state.call_ids)
        done = set(state.completed) & set(rd.read_results())
        state.completed = [u for u in state.completed if u in done]
        res = RunResult(run_id, rd.root, "running", None, self.ledger.spent, {}, resumed_units=len(done))
        memo: dict[str, Any] = {}
        todo = [u for u in units if u[0] not in done]

        async def checkpoint() -> None:
            state.spent_usd = float(self.ledger.spent)
            state.call_ids = [str(c) for c in self.ledger.call_ids]
            state.updated_at = _now()
            rd.save_state(state)

        try:
            await checkpoint()
            if config.mode == "sync":
                await self._run_sync(todo, rd, state, res, memo, checkpoint)
            else:
                await self._run_batch(todo, rd, state, res, memo, checkpoint, config)
            state.status = "completed"
        except _Stop:
            state.status, state.stopped = "stopped", "budget"
        except BaseException:
            state.status = "interrupted"
            await checkpoint()
            raise
        await checkpoint()
        res.status, res.stopped, res.spent_usd = state.status, state.stopped, self.ledger.spent
        res.units = rd.read_results()
        return res

    def _guard(self) -> None:
        if self.ledger.exceeded():
            raise _Stop

    async def _finish(
        self,
        unit: str,
        item: Item,
        rep: int,
        values: dict[str, Any],
        error: str | None,
        rd: RunDir,
        state: RunState,
        checkpoint: Callable[[], Awaitable[None]],
    ) -> None:
        rd.append_result({"unit": unit, "item": item.id, "rep": rep, "stages": values, "error": error})
        if error is None and unit not in state.completed:
            state.completed.append(unit)
        await checkpoint()

    def _lookup(self, key: str, stage: str, memo: dict[str, Any], res: RunResult) -> tuple[bool, Any]:
        if key in memo:
            res.cached[stage] += 1
            return True, memo[key]
        if self.cache is not None:
            hit = self.cache.get(key, stage)
            if hit is not None:
                memo[key] = hit.value
                res.cached[stage] += 1
                return True, hit.value
        return False, None

    def _store(self, key: str, stage: str, value: Any, memo: dict[str, Any]) -> None:
        memo[key] = value
        if self.cache is not None:
            self.cache.put(key, stage, value)

    # --- sync mode --------------------------------------------------------------------------------

    async def _run_sync(
        self,
        todo: list[tuple[str, Item, int]],
        rd: RunDir,
        state: RunState,
        res: RunResult,
        memo: dict[str, Any],
        checkpoint: Callable[[], Awaitable[None]],
    ) -> None:
        for unit, item, rep in todo:
            keys = self._stage_keys(item, rep)
            values: dict[str, Any] = {}
            error: str | None = None
            for s in self.stages:
                hit, val = self._lookup(keys[s.name], s.name, memo, res)
                if not hit:
                    self._guard()  # BEFORE the next call
                    ctx = StageCtx(unit, item, rep, dict(values))
                    try:
                        out = await s.run_fn(ctx)
                    except Exception as e:
                        error = f"{s.name}: {type(e).__name__}: {e}"
                        break
                    await self.ledger.record(out.call_ids)
                    res.executed[s.name] += 1
                    self._store(keys[s.name], s.name, out.value, memo)
                    val = out.value
                values[s.name] = val
            await self._finish(unit, item, rep, values, error, rd, state, checkpoint)

    # --- batch mode -------------------------------------------------------------------------------

    async def _run_batch(
        self,
        todo: list[tuple[str, Item, int]],
        rd: RunDir,
        state: RunState,
        res: RunResult,
        memo: dict[str, Any],
        checkpoint: Callable[[], Awaitable[None]],
        config: RunConfig,
    ) -> None:
        assert self.backend is not None
        keys = {u: self._stage_keys(item, rep) for u, item, rep in todo}
        values: dict[str, dict[str, Any]] = {u: {} for u, _, _ in todo}
        errors: dict[str, str] = {}

        async def finish_all() -> None:
            for unit, item, rep in todo:
                await self._finish(unit, item, rep, values[unit], errors.get(unit), rd, state, checkpoint)

        for s in self.stages:
            misses: list[tuple[str, Item, int, StageCtx]] = []
            for unit, item, rep in todo:
                if unit in errors:
                    continue
                hit, val = self._lookup(keys[unit][s.name], s.name, memo, res)
                if hit:
                    values[unit][s.name] = val
                else:
                    misses.append((unit, item, rep, StageCtx(unit, item, rep, dict(values[unit]))))
            if s.batch is not None and misses:
                await self._batch_stage(
                    s, s.batch, misses, keys, values, errors, memo, res, state, checkpoint, config
                )
                continue
            for unit, _item, _rep, ctx in misses:
                self._guard()
                try:
                    out = await s.run_fn(ctx)
                except Exception as e:
                    errors[unit] = f"{s.name}: {type(e).__name__}: {e}"
                    continue
                await self.ledger.record(out.call_ids)
                res.executed[s.name] += 1
                self._store(keys[unit][s.name], s.name, out.value, memo)
                values[unit][s.name] = out.value
                await checkpoint()
        await finish_all()

    async def _batch_stage(
        self,
        s: Stage,
        spec: BatchSpec,
        misses: list[tuple[str, Item, int, StageCtx]],
        keys: dict[str, dict[str, str]],
        values: dict[str, dict[str, Any]],
        errors: dict[str, str],
        memo: dict[str, Any],
        res: RunResult,
        state: RunState,
        checkpoint: Callable[[], Awaitable[None]],
        config: RunConfig,
    ) -> None:
        assert self.backend is not None
        self._guard()  # BEFORE the batch is submitted
        requests = [spec.request_fn(ctx) for _, _, _, ctx in misses]
        sig = digest(sorted(r.custom_id for r in requests))
        prior = state.batches.get(s.name)
        batch_id = prior["batch_id"] if prior and prior.get("sig") == sig else None

        async def remember(bid: str) -> None:
            state.batches[s.name] = {"batch_id": bid, "sig": sig, "n": len(requests)}
            await (
                checkpoint()
            )  # persisted before the long wait, so a kill re-attaches instead of resubmitting

        responses = await run_batch(
            self.backend,
            requests,
            batch_id=batch_id,
            on_submitted=remember,
            poll_s=config.batch_poll_s,
            sleep=self._sleep,
        )
        for (unit, _item, _rep, ctx), req in zip(misses, requests, strict=True):
            r = responses.get(req.custom_id)
            if r is None or not r.ok:
                errors[unit] = f"{s.name}: batch {(r.error if r else 'missing_response')}"
                continue
            try:
                out = await spec.parse_fn(ctx, r)
            except Exception as e:
                errors[unit] = f"{s.name}: {type(e).__name__}: {e}"
                continue
            await self.ledger.record(out.call_ids)
            res.executed[s.name] += 1
            self._store(keys[unit][s.name], s.name, out.value, memo)
            values[unit][s.name] = out.value
        await checkpoint()
