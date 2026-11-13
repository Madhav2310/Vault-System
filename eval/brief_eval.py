"""Brief quality evaluation (plan S12.07, design section 18).

For the train documents of Vault-Eval (holdout documents never), the brief is generated in memory through the
production code path (`app.insights.stage.generate_brief` over chunks cut by the production extractor and
chunker), then scored:

- fact faithfulness: the `eval_judge_faithfulness@1` judge over each fact vs its cited text (a stable
  hash-selected sample of at most `--facts-per-doc` facts per document keeps the spend inside the cap;
  0 = every fact). Headline = mean verdict score (pass 1, partial 0.5, fail 0); the pass rate is printed too.
- field completeness: for CUAD documents, the share of answerable gold items of the clause types parties,
  dates, governing law and termination whose longest gold quote is found (>= 80% of its words) in the
  citations or values of the brief facts of the mapped fields.
- verified rate: the share of facts the application marked `verified`.

CLI (from backend/, PYTHONPATH=..):
    python -m eval.brief_eval --docs 10 --max-usd 1 [--freeze]    # live: the cap is mandatory
    python -m eval.brief_eval --docs 2 --dry-run                  # scripted provider, no network
Writes `eval/reports/brief_<run>.md` (and, with `--freeze`, `eval/baselines/brief.json`). Exit codes: 0 targets
met (always for a dry run), 1 a target missed, 2 usage error, 3 spend cap reached (partial results are
printed, nothing is frozen). Every model call, brief or judge, is checked against the cap before it is sent.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

from app.chat.evidence import EvidenceChunk
from app.core.ids import uuid7
from app.insights.brief import Brief
from app.insights.stage import ChunkRef, generate_brief, plan_groups
from app.llm.errors import LlmError
from app.llm.gateway import LlmGateway, MemoryLedger, render_inputs
from app.llm.routing import Route
from eval.judge import (
    JudgeCase,
    JudgeOutputError,
    SegmentView,
    SpendCapReached,
    SpendGuard,
    check_tiers,
    run_judge,
)
from eval.loader import Dataset, load_dataset
from eval.report import REPORTS_DIR, ROOT

BASELINES_DIR = ROOT / "eval" / "baselines"
BASELINE_NAME = "brief"
FAITHFULNESS_TARGET = 0.90
VERIFIED_TARGET = 0.80
DEFAULT_DOCS = 10
DEFAULT_FACTS_PER_DOC = 10
COVERAGE_MIN = 0.8
BRIEF_PROMPTS = {"brief_extract": ("brief_extract", 2), "brief_merge": ("brief_merge", 2)}

# CUAD clause type -> brief fact fields in which it may appear
FIELD_MAP: dict[str, tuple[str, ...]] = {
    "Parties": ("party",),
    "Agreement Date": ("effective_date", "key_point", "other"),
    "Effective Date": ("effective_date",),
    "Expiration Date": ("expiry_date", "deadline"),
    "Renewal Term": ("expiry_date", "termination", "deadline", "key_point"),
    "Governing Law": ("governing_law",),
    "Termination For Convenience": ("termination",),
    "Notice Period To Terminate Renewal": ("termination", "deadline"),
}
FIELD_LABEL = {
    "party": "Party",
    "effective_date": "Effective date",
    "expiry_date": "Expiry date",
    "deadline": "Deadline",
    "amount": "Amount",
    "obligation": "Obligation",
    "termination": "Termination",
    "governing_law": "Governing law",
    "key_point": "Key point",
    "risk": "Risk",
    "other": "Other",
}

ChunkSource = Callable[[str, Mapping[str, Any]], list[EvidenceChunk]]


# --- corpus: documents and chunks ---------------------------------------------------------------------------


def train_documents(ds: Dataset, n: int) -> list[str]:
    """The first `n` train document keys in key order (documents referenced by train items; holdout excluded)."""
    by_id = ds.by_id()
    keys = sorted({d for i in ds.splits["train"] for d in by_id[i].referenced_docs()} - ds.holdout_docs)
    return keys[:n]


def source_roots() -> list[pathlib.Path]:
    roots = [ROOT]
    if env := os.environ.get("VAULT_CORPUS_ROOT"):
        roots.append(pathlib.Path(env))
    parts = ROOT.parts
    if ".claude" in parts:  # a worktree: .../<repo>/.claude/worktrees/<name>
        roots.append(pathlib.Path(*parts[: parts.index(".claude")]))
    return roots


_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


def corpus_chunks(key: str, meta: Mapping[str, Any]) -> list[EvidenceChunk]:
    """The document's chunks as the ingestion pipeline would store them (production extractor + chunker)."""
    from app.ingestion.chunk import chunk_document
    from app.ingestion.extract.worker import extractor_for

    rel = meta["file"]
    path = next((r / rel for r in source_roots() if (r / rel).is_file()), None)
    if path is None:
        raise FileNotFoundError(f"{rel} not found under {source_roots()}; run `make eval-fetch`")
    doc = extractor_for(_MIME[path.suffix.lower()]).extract(str(path))
    title = meta.get("title", key)
    version_id = uuid.uuid4()
    return [
        EvidenceChunk(
            uuid.uuid4(),
            version_id,
            title,
            1,
            d.page_start,
            d.page_end,
            tuple(d.section_path),
            d.original_text,
            tuple(d.sentences),
        )
        for d in chunk_document(doc, title)
    ]


# --- completeness ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    item_id: str
    clause: str
    fields: tuple[str, ...]
    quote: str  # the longest gold quote


def completeness_targets(ds: Dataset, doc_key: str) -> list[Target]:
    """Answerable CUAD train items of one document whose clause type maps to brief fields."""
    out: list[Target] = []
    for item in ds.select("train"):
        if item.source != "CUAD" or item.doc_ids != [doc_key]:
            continue
        fields = FIELD_MAP.get(item.cuad_category or "")
        if fields is None or item.expected_coverage != "supported" or not item.gold:
            continue
        quote = max((g.quote for g in item.gold), key=len)
        out.append(Target(item.id, item.cuad_category or "", fields, quote))
    return out


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.casefold()))


def target_present(t: Target, brief: Brief) -> bool:
    """>= 80% of the quote's words appear in the cited text or value of the brief facts of the mapped fields."""
    pool: set[str] = set()
    for f in brief.facts:
        if f.field in t.fields:
            pool |= _words(f.value)
            for e in f.evidence:
                pool |= _words(e.cited_text)
    want = _words(t.quote)
    return bool(want) and len(want & pool) / len(want) >= COVERAGE_MIN


# --- judging --------------------------------------------------------------------------------------------------


def fact_case(doc_key: str, i: int, field_: str, value: str, passages: Sequence[str]) -> JudgeCase:
    label = FIELD_LABEL.get(field_, field_)
    return JudgeCase(
        item_id=f"{doc_key}#fact{i}",
        question=f"What does the document state as: {label}?",
        segments=(SegmentView(f"{label}: {value}", tuple(passages)),),
        reference_quotes=(),
        expected_answer=None,
        criteria=(),
    )


def sample_facts(doc_key: str, brief: Brief, k: int) -> list[int]:
    """Indexes of the facts to judge: a stable sample (sha256 order), all when `k` is 0 or >= the count."""
    import hashlib

    idx = list(range(len(brief.facts)))
    if k <= 0 or len(idx) <= k:
        return idx
    key = lambda i: hashlib.sha256(
        f"{doc_key}:{i}:{brief.facts[i].field}:{brief.facts[i].value}".encode()
    ).digest()
    return sorted(sorted(idx, key=key)[:k])


def worst_case(gw: LlmGateway, node: str, prompt: tuple[str, int], content_chars: int) -> Decimal:
    p = gw.prompts.get(*prompt)
    res = gw.router.resolve(node, None, default=Route(p.role, p.effort))
    price = gw.prices.price(res.model)
    in_tok = (len(p.body) + content_chars) // 4 + 1
    return (Decimal(in_tok) * price.input + Decimal(p.max_tokens) * price.output) / Decimal(1_000_000)


class GuardedGateway:
    """Checks `spent + worst case of the next call <= cap` before every `call`, then delegates."""

    def __init__(self, gw: LlmGateway, guard: SpendGuard) -> None:
        self._gw, self._guard = gw, guard

    async def call(self, **kw: Any) -> Any:
        if kw.get("inputs") is not None:
            chars = len(render_inputs(kw["inputs"]))
        else:
            chars = sum(len(str(m["content"])) for m in kw.get("messages") or ())
        self._guard.check(worst_case(self._gw, kw["purpose"], kw["prompt"], chars))
        return await self._gw.call(**kw)


# --- results --------------------------------------------------------------------------------------------------


@dataclass
class DocResult:
    key: str
    category: str | None = None
    facts: int = 0
    verified: int = 0
    judged: int = 0
    judge_errors: int = 0
    score_sum: float = 0.0
    passes: int = 0
    targets: int = 0
    present: int = 0
    cost_usd: Decimal = Decimal(0)
    error: str | None = None
    partial: bool = False
    failures: list[str] = field(
        default_factory=list
    )  # judged facts that did not pass: "field: value [verdict]"

    @property
    def faithfulness(self) -> float | None:
        return self.score_sum / self.judged if self.judged else None

    @property
    def verified_rate(self) -> float | None:
        return self.verified / self.facts if self.facts else None

    @property
    def completeness(self) -> float | None:
        return self.present / self.targets if self.targets else None


@dataclass
class BriefEvalResult:
    run_id: uuid.UUID
    mode: str  # live | dry-run
    started_at: dt.datetime
    git_sha: str
    facts_per_doc: int
    docs: list[DocResult] = field(default_factory=list)
    spent: Decimal = Decimal(0)
    cap: Decimal | None = None
    stopped: str | None = None  # set when the spend cap ended the run early
    finished_at: dt.datetime | None = None

    def totals(self) -> dict[str, float | int | None]:
        facts = sum(d.facts for d in self.docs)
        judged = sum(d.judged for d in self.docs)
        targets = sum(d.targets for d in self.docs)
        return {
            "documents": len(self.docs),
            "documents_failed": sum(1 for d in self.docs if d.error),
            "facts": facts,
            "judged": judged,
            "judge_errors": sum(d.judge_errors for d in self.docs),
            "faithfulness": sum(d.score_sum for d in self.docs) / judged if judged else None,
            "faithfulness_pass_rate": sum(d.passes for d in self.docs) / judged if judged else None,
            "completeness": sum(d.present for d in self.docs) / targets if targets else None,
            "completeness_targets": targets,
            "verified_rate": sum(d.verified for d in self.docs) / facts if facts else None,
        }

    def targets_met(self) -> bool:
        t = self.totals()
        f, v = t["faithfulness"], t["verified_rate"]
        return (
            self.stopped is None
            and f is not None
            and v is not None
            and f >= FAITHFULNESS_TARGET
            and v >= VERIFIED_TARGET
        )


async def evaluate_document(
    key: str,
    meta: Mapping[str, Any],
    chunks: list[EvidenceChunk],
    ds: Dataset,
    gw: LlmGateway,
    guard: SpendGuard,
    *,
    facts_per_doc: int,
    brief_max_usd: Decimal,
    brief_max_tokens: int,
    owner_id: uuid.UUID,
) -> DocResult:
    res = DocResult(key)
    by_id = {c.chunk_id: c for c in chunks}
    refs = [ChunkRef(c.chunk_id, len(c.original_text) // 4, c.section_path) for c in chunks]
    groups = plan_groups(refs, brief_max_tokens)

    async def load(ids: Sequence[uuid.UUID]) -> list[EvidenceChunk]:
        return [by_id[i] for i in ids]

    before = guard.spent
    try:
        gen = await generate_brief(
            cast(LlmGateway, GuardedGateway(gw, guard)),
            load,
            owner_id=owner_id,
            version_id=chunks[0].version_id,
            job_id=None,
            groups=groups,
            max_usd=brief_max_usd,
        )
    except (
        LlmError,
        ValueError,
    ) as e:  # a failed brief is a result (and counts against the rates by omission)
        res.error = type(e).__name__
        res.cost_usd = guard.spent - before
        return res
    brief = gen.brief
    res.category, res.partial = brief.category, brief.partial
    res.facts = len(brief.facts)
    res.verified = sum(f.status == "verified" for f in brief.facts)
    targets = completeness_targets(ds, key)
    res.targets = len(targets)
    res.present = sum(target_present(t, brief) for t in targets)
    for i in sample_facts(key, brief, facts_per_doc):
        f = brief.facts[i]
        case = fact_case(key, i, f.field, f.value, [e.cited_text for e in f.evidence])
        try:
            v = await run_judge(gw, case, "faithfulness", guard=guard)
        except (LlmError, JudgeOutputError):
            res.judge_errors += 1
            continue
        res.judged += 1
        res.score_sum += v.score
        res.passes += v.verdict == "pass"
        if v.verdict != "pass":
            res.failures.append(f"{f.field}: {f.value} [{v.verdict}]")
    res.cost_usd = guard.spent - before
    return res


async def run_brief_eval(
    keys: Sequence[str],
    ds: Dataset,
    chunk_source: ChunkSource,
    gw: LlmGateway,
    guard: SpendGuard,
    *,
    mode: str,
    facts_per_doc: int = DEFAULT_FACTS_PER_DOC,
    git_sha: str = "unknown",
) -> BriefEvalResult:
    from app.core.settings import get_settings

    s = get_settings()
    out = BriefEvalResult(uuid7(), mode, dt.datetime.now(dt.UTC), git_sha, facts_per_doc, cap=guard.cap)
    check_tiers(gw.router)
    owner = uuid.uuid4()
    for key in keys:
        meta = ds.documents[key]
        try:
            chunks = await asyncio.to_thread(chunk_source, key, meta)
        except (FileNotFoundError, KeyError) as e:
            out.docs.append(DocResult(key, error=type(e).__name__))
            continue
        if not chunks:
            out.docs.append(DocResult(key, error="no_chunks"))
            continue
        try:
            out.docs.append(
                await evaluate_document(
                    key,
                    meta,
                    chunks,
                    ds,
                    gw,
                    guard,
                    facts_per_doc=facts_per_doc,
                    brief_max_usd=Decimal(str(s.brief_max_usd)),
                    brief_max_tokens=s.brief_max_evidence_tokens,
                    owner_id=owner,
                )
            )
        except SpendCapReached as e:
            out.stopped = str(e)
            break
    out.spent = guard.spent
    out.finished_at = dt.datetime.now(dt.UTC)
    return out


# --- report and baseline --------------------------------------------------------------------------------------


def _f(v: float | None) -> str:
    return "-" if v is None else f"{v:.3f}"


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_report(r: BriefEvalResult) -> str:
    t = r.totals()
    lines = [
        f"# Brief eval run {r.run_id}",
        "",
        f"- mode: `{r.mode}`  git: `{r.git_sha}`  started: {r.started_at.isoformat()}",
        f"- documents: {t['documents']} (failed: {t['documents_failed']})  facts: {t['facts']}  "
        f"judged: {t['judged']} (judge errors: {t['judge_errors']}; at most {r.facts_per_doc or 'all'} "
        "facts per document, stable hash sample)",
        f"- spend: ${r.spent}" + (f" of cap ${r.cap}" if r.cap is not None else ""),
    ]
    if r.mode == "dry-run":
        lines.append("- DRY RUN (scripted provider): the numbers below are not measurements")
    if r.stopped:
        lines.append(f"- STOPPED early by the spend cap: {r.stopped}")
    lines += [
        "",
        "## Totals",
        "",
        "| metric | n | value | target |",
        "|---|---:|---:|---:|",
        f"| faithfulness (mean verdict score) | {t['judged']} | {_f(t['faithfulness'])} | >= {FAITHFULNESS_TARGET} |",
        f"| faithfulness (pass rate) | {t['judged']} | {_f(t['faithfulness_pass_rate'])} | |",
        f"| completeness | {t['completeness_targets']} | {_f(t['completeness'])} | |",
        f"| verified rate | {t['facts']} | {_f(t['verified_rate'])} | >= {VERIFIED_TARGET} |",
        "",
        "## Per document",
        "",
        "| document | category | facts | verified | verified rate | judged | faithfulness | completeness | "
        "cost usd | error |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for d in r.docs:
        comp = f"{d.present}/{d.targets} = {_f(d.completeness)}" if d.targets else "-"
        lines.append(
            f"| {_cell(d.key)} | {d.category or '-'} | {d.facts} | {d.verified} | {_f(d.verified_rate)} | "
            f"{d.judged} | {_f(d.faithfulness)} | {comp} | {d.cost_usd} | {_cell(d.error or '')} |"
        )
    bad = [(d.key, x) for d in r.docs for x in d.failures]
    if bad:
        lines += ["", "## Judged facts that did not pass", ""]
        lines += [f"- `{k}`: {_cell(x)}" for k, x in bad]
    return "\n".join(lines) + "\n"


def write_report(r: BriefEvalResult, directory: pathlib.Path = REPORTS_DIR) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"brief_{r.run_id}.md"
    path.write_text(render_report(r))
    return path


def freeze_baseline(r: BriefEvalResult, directory: pathlib.Path = BASELINES_DIR) -> pathlib.Path:
    t = r.totals()
    data = {
        "name": BASELINE_NAME,
        "suite": "brief",
        "split": "train",
        "git_sha": r.git_sha,
        "run_id": str(r.run_id),
        "facts_per_doc": r.facts_per_doc,
        "spent_usd": str(r.spent),
        "metrics": {
            "faithfulness": {"mean": t["faithfulness"], "n": t["judged"]},
            "faithfulness_pass_rate": {"mean": t["faithfulness_pass_rate"], "n": t["judged"]},
            "completeness": {"mean": t["completeness"], "n": t["completeness_targets"]},
            "verified_rate": {"mean": t["verified_rate"], "n": t["facts"]},
        },
        "per_doc": {
            d.key: {
                "facts": d.facts,
                "verified": d.verified,
                "judged": d.judged,
                "faithfulness": d.faithfulness,
                "completeness": d.completeness,
            }
            for d in r.docs
        },
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{BASELINE_NAME}.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


# --- dry run provider -----------------------------------------------------------------------------------------


def _dry_gateway(ledger: MemoryLedger) -> LlmGateway:
    from app.core.settings import Settings
    from app.llm.pricing import DEFAULT_PRICES, PriceTable
    from app.llm.prompts import default_registry
    from app.llm.providers.fake import FakeProvider
    from app.llm.types import ChatRequest, ChatResponse, TextPart, Usage

    class DryProvider(FakeProvider):
        """Valid replies for the brief, merge and faithfulness schemas; brief facts cite real handles."""

        async def complete(self, req: ChatRequest) -> ChatResponse:
            self.requests.append(req)
            props = (req.output_schema or {}).get("properties", {})
            if "verdict" in props:
                body: dict[str, Any] = {
                    "reason": "dry run",
                    "verdict": "pass",
                    "unsupported_claims": [],
                    "confidence": 0.9,
                }
            else:
                content = str(req.messages[-1]["content"])
                lines = re.findall(r"^(E\d+\.\d+) (.+)$", content, re.M)[:4]
                facts = [
                    {"field": "key_point", "value": " ".join(txt.split()[:6]), "evidence": [h]}
                    for h, txt in lines
                ]
                body = {
                    "document_type": "agreement",
                    "summary": "A dry-run summary.",
                    "category": "contract",
                    "tags": ["dry run"],
                    "suggested_questions": ["Who are the parties?", "What is the term?", "What is the fee?"],
                }
                if "facts" in props:
                    body["facts"] = facts
            return ChatResponse((TextPart(json.dumps(body)),), "end_turn", Usage(1500, 300), None, req.model)

    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    return LlmGateway(
        chat=DryProvider(),
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=ledger,
        settings=settings,
    )


def _live_gateway(ledger: MemoryLedger) -> LlmGateway:
    from app.core.settings import get_settings
    from app.llm.pricing import price_table_from_settings
    from app.llm.prompts import default_registry
    from app.llm.providers.openai_chat import OpenAIResponsesProvider

    s = get_settings()
    key = s.openai_api_key.get_secret_value() if s.openai_api_key else None
    if not key:
        raise SystemExit("OPENAI_API_KEY is not set (gitignored .env)")
    return LlmGateway(
        chat=OpenAIResponsesProvider(key, s.llm_timeout_s),
        prompts=default_registry(),
        prices=price_table_from_settings(s),
        ledger=ledger,
        settings=s,
    )


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _print_summary(r: BriefEvalResult, path: pathlib.Path) -> None:
    t = r.totals()
    print(
        f"documents {t['documents']} (failed {t['documents_failed']}) facts {t['facts']} judged {t['judged']}"
    )
    print(
        f"faithfulness {_f(t['faithfulness'])} (pass rate {_f(t['faithfulness_pass_rate'])}) target >= {FAITHFULNESS_TARGET}"
    )
    print(f"completeness {_f(t['completeness'])} over {t['completeness_targets']} gold clauses")
    print(f"verified {_f(t['verified_rate'])} target >= {VERIFIED_TARGET}")
    cap = f" of cap ${r.cap}" if r.cap is not None else ""
    print(f"spend ${r.spent}{cap}")
    print(f"report {path}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.brief_eval")
    ap.add_argument("--docs", type=int, default=DEFAULT_DOCS, help="train documents to evaluate")
    ap.add_argument(
        "--facts-per-doc", type=int, default=DEFAULT_FACTS_PER_DOC, help="judged facts per document (0 = all)"
    )
    ap.add_argument("--max-usd", type=Decimal, default=None, help="spend cap (mandatory for live calls)")
    ap.add_argument("--dry-run", action="store_true", help="scripted FakeProvider replies, no network")
    ap.add_argument(
        "--freeze", action="store_true", help="write eval/baselines/brief.json (live, complete runs only)"
    )
    ap.add_argument("--report-dir", type=pathlib.Path, default=REPORTS_DIR)
    ap.add_argument("--baseline-dir", type=pathlib.Path, default=BASELINES_DIR)
    a = ap.parse_args(argv)
    if a.docs < 1 or a.facts_per_doc < 0:
        ap.error("--docs must be >= 1 and --facts-per-doc >= 0")
    if not a.dry_run and a.max_usd is None:
        print("refusing live calls without an explicit --max-usd cap", file=sys.stderr)
        return 2
    if a.freeze and a.dry_run:
        ap.error("--freeze needs a live run")
    ds = load_dataset()
    keys = train_documents(ds, a.docs)
    if len(keys) < a.docs:
        ap.error(f"only {len(keys)} train documents exist")
    ledger = MemoryLedger()
    gw = _dry_gateway(ledger) if a.dry_run else _live_gateway(ledger)
    guard = SpendGuard(ledger, a.max_usd if a.max_usd is not None else Decimal(1000))
    result = asyncio.run(
        run_brief_eval(
            keys,
            ds,
            corpus_chunks,
            gw,
            guard,
            mode="dry-run" if a.dry_run else "live",
            facts_per_doc=a.facts_per_doc,
            git_sha=_git_sha(),
        )
    )
    path = write_report(result, a.report_dir)
    _print_summary(result, path)
    if result.stopped:
        print(f"stopped: {result.stopped}", file=sys.stderr)
        return 3
    if a.freeze and result.totals()["documents_failed"]:
        print("not freezing: some documents failed", file=sys.stderr)
        return 1
    if a.freeze:
        print(f"baseline {freeze_baseline(result, a.baseline_dir)}")
    return 0 if a.dry_run or result.targets_met() else 1


if __name__ == "__main__":
    raise SystemExit(main())
