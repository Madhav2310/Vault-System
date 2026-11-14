"""Calibrate scored abstention: `python -m eval.calibrate_abstain --split dev [--run FILE] [--write]`.

Offline and free: it reads a features file, calls no model and no database. Weights are fitted on the train
split, both thresholds are picked on dev from the coverage-accuracy curve, and the chosen config is scored on
`--split` (dev by default; test only to report a held-out number, never to choose anything). Targets: false
refusals <= 2% of answerable items and abstain recall >= 0.9 on the strata unanswerable, false_premise and
versioned. `--write` stores the fit in `backend/config/abstain@1.yaml` (dev only).

Input (`--run`, default: the newest `eval/reports/*.abstain.jsonl`), one JSON object per scored item:
  item_id, stratum, source_doc (the split is derived with `eval.splits_v2.split_of`; a `split` field wins),
  top, margin (retrieval features, margin to rank `--margin-rank`), not_found (plan elements without a valid
  handle), unsupported (share of unsupported segments before removal); null/absent = not computed.
A v2 run writes it next to its report as `<run_id>.abstain.jsonl`.

Run from backend/ with PYTHONPATH=..:
    python -m eval.calibrate_abstain --split dev --run eval/reports/<run_id>.abstain.jsonl
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import yaml

from app.chat import abstain as A
from eval.splits_v2 import SPLITS_V2, split_of

ABSTAIN_STRATA = ("unanswerable", "false_premise", "versioned")
MAX_FALSE_REFUSAL = 0.02
MIN_RECALL = 0.9
MIN_FINAL_ROWS = 40
REPORTS = pathlib.Path(__file__).resolve().parent / "reports"
CONFIG = A.CONFIG_PATH


@dataclass(frozen=True)
class Row:
    item_id: str
    stratum: str
    split: str
    x: dict[str, float | None]

    @property
    def should_abstain(self) -> bool:
        return self.stratum in ABSTAIN_STRATA


def _num(v: object) -> float | None:
    return None if v is None else float(v)  # type: ignore[arg-type]


def load_rows(path: pathlib.Path) -> list[Row]:
    rows: list[Row] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        d = json.loads(line)
        split = d.get("split") or (split_of(d["source_doc"]) if d.get("source_doc") else None)
        if split not in SPLITS_V2:
            raise SystemExit(f"{path.name}:{n}: no split (need `split` or `source_doc`)")
        rows.append(Row(str(d["item_id"]), str(d["stratum"]), split, {f: _num(d.get(f)) for f in A.FEATURES}))
    return rows


def _complete(rows: Sequence[Row], features: Sequence[str]) -> list[Row]:
    return [r for r in rows if all(r.x[f] is not None for f in features)]


def _fit(
    train: Sequence[Row], features: tuple[str, ...]
) -> tuple[float, dict[str, float], dict[str, A.Norm]]:
    data = [A.FitRow(r.x, not r.should_abstain) for r in _complete(train, features)]
    bias, w, norm = A.fit_logistic(data, features)
    return bias, w, {f: A.Norm(mean=m, std=s) for f, (m, s) in norm.items()}


def _model(
    features: tuple[str, ...], fit: tuple[float, dict[str, float], dict[str, A.Norm]], dev: Sequence[Row],
    *, partial: bool,
) -> tuple[A.ModelCfg, list[A.CurvePoint]] | None:  # fmt: skip
    bias, w, norm = fit
    draft = A.ModelCfg(features=list(features), bias=bias, weights=w, norm=norm, t_abstain=0.0, t_answer=1.0)
    scored = []
    for r in _complete(dev, features):
        p = A.probability(draft, r.x)
        assert p is not None
        scored.append((p, r.should_abstain))
    got = A.pick_thresholds(scored, max_false_refusal=MAX_FALSE_REFUSAL)
    if got is None:
        return None
    t_abs, t_ans, _ = got
    m = draft.model_copy(update={"t_abstain": t_abs, "t_answer": t_ans if partial else t_abs})
    return m, A.curve(scored)


@dataclass(frozen=True)
class Report:
    n_answerable: int
    n_abstain: int
    false_refusal: float
    recall: float
    partial_share: float  # of answerable rows
    answered_error: float | None  # share of should-abstain rows among rows answered outright
    recall_by_stratum: dict[str, float]

    @property
    def passes(self) -> bool:
        return self.false_refusal <= MAX_FALSE_REFUSAL and self.recall >= MIN_RECALL


def score_split(cfg: A.AbstainConfig, rows: Sequence[Row]) -> Report:
    """Outcome per row at the last stage that has its features (final, else gate, else answer)."""
    stage = "final" if cfg.final is not None else "gate"
    out = [(r, A.decide(cfg, r.x, stage=stage).outcome) for r in rows]  # type: ignore[arg-type]
    ans = [(r, o) for r, o in out if not r.should_abstain]
    abst = [(r, o) for r, o in out if r.should_abstain]
    answered = [(r, o) for r, o in out if o == "answer"]
    by: dict[str, float] = {}
    for s in ABSTAIN_STRATA:
        sub = [o for r, o in abst if r.stratum == s]
        if sub:
            by[s] = sum(o == "abstain" for o in sub) / len(sub)
    return Report(
        len(ans),
        len(abst),
        sum(o == "abstain" for _, o in ans) / len(ans) if ans else 0.0,
        sum(o == "abstain" for _, o in abst) / len(abst) if abst else 0.0,
        sum(o == "partial" for _, o in ans) / len(ans) if ans else 0.0,
        sum(r.should_abstain for r, _ in answered) / len(answered) if answered else None,
        by,
    )


def calibrate(rows: Sequence[Row], margin_rank: int) -> A.AbstainConfig | None:
    train = [r for r in rows if r.split == "train"]
    dev = [r for r in rows if r.split == "dev"]
    gate = _model(A.GATE_FEATURES, _fit(train, A.GATE_FEATURES), dev, partial=False)
    if gate is None:
        return None
    final = None
    full = _complete(train, A.FEATURES)
    if len(full) >= MIN_FINAL_ROWS and len({r.should_abstain for r in full}) == 2:
        final = _model(A.FEATURES, _fit(train, A.FEATURES), dev, partial=True)
    return A.AbstainConfig(
        fitted=True, margin_rank=margin_rank, gate=gate[0], final=final[0] if final else None
    )


def render_curve(rows: Sequence[Row], model: A.ModelCfg, title: str) -> str:
    pts = A.curve(
        [
            (p, r.should_abstain)
            for r in _complete(rows, model.features)
            if (p := A.probability(model, r.x)) is not None
        ]
    )
    lines = [title, f"{'t':>8} {'false_refusal':>14} {'recall':>8} {'answered_error':>15}"]
    for p in pts:
        err = "n/a" if p.answered_error is None else f"{p.answered_error:.4f}"
        lines.append(f"{p.t:>8.4f} {p.false_refusal:>14.4f} {p.recall:>8.4f} {err:>15}")
    return "\n".join(lines)


def render_report(split: str, rep: Report) -> str:
    err = "n/a" if rep.answered_error is None else f"{rep.answered_error:.4f}"
    by = " ".join(f"{k}={v:.3f}" for k, v in rep.recall_by_stratum.items())
    return "\n".join(
        [
            f"split={split} answerable={rep.n_answerable} should_abstain={rep.n_abstain}",
            f"false_refusals={rep.false_refusal:.4f} (target <= {MAX_FALSE_REFUSAL})",
            f"abstain_recall={rep.recall:.4f} (target >= {MIN_RECALL}) by stratum: {by}",
            f"partial_share_of_answerable={rep.partial_share:.4f} answered_outright_error={err}",
            "PASS" if rep.passes else "FAIL",
        ]
    )


def write_config(path: pathlib.Path, cfg: A.AbstainConfig) -> None:
    head = (
        "# Scored abstention (S22.07), written by `python -m eval.calibrate_abstain --write`: weights fitted on\n"
        "# train, thresholds picked on dev. Do not edit by hand; re-run the calibration instead.\n"
    )
    path.write_text(head + yaml.safe_dump(cfg.model_dump(), sort_keys=False), encoding="utf-8")


def newest_run() -> pathlib.Path | None:
    found = sorted(REPORTS.glob("*.abstain.jsonl"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.calibrate_abstain", description=__doc__.split("\n\n")[0])
    ap.add_argument("--split", default="dev", choices=["dev", "test"], help="split to report on")
    ap.add_argument(
        "--run", type=pathlib.Path, help="features jsonl (default: newest eval/reports/*.abstain.jsonl)"
    )
    ap.add_argument("--margin-rank", type=int, default=3, help="rank N of the margin feature in the file")
    ap.add_argument("--write", action="store_true", help="write backend/config/abstain@1.yaml (dev only)")
    args = ap.parse_args(argv)
    if args.write and args.split != "dev":
        print("--write is only allowed with --split dev (never tune on test content)", file=sys.stderr)
        return 2
    path = args.run or newest_run()
    if path is None or not path.exists():
        print("no features file: pass --run <file>.abstain.jsonl (see the module docstring)", file=sys.stderr)
        return 2
    rows = load_rows(path)
    cfg = calibrate(rows, args.margin_rank)
    if cfg is None or cfg.gate is None:
        print("no threshold keeps false refusals within the cap (or no usable rows)", file=sys.stderr)
        return 1
    dev = [r for r in rows if r.split == "dev"]
    print(render_curve(dev, cfg.gate, "gate model (top, margin), dev curve"))
    if cfg.final is not None:
        print(render_curve(dev, cfg.final, "final model (all features), dev curve"))
    for name, m in (("gate", cfg.gate), ("final", cfg.final)):
        if m is not None:
            print(f"chosen {name}: t_abstain={m.t_abstain:.6g} t_answer={m.t_answer:.6g}")
    rep = score_split(cfg, [r for r in rows if r.split == args.split])
    print(render_report(args.split, rep))
    if args.write:
        write_config(CONFIG, cfg)
        print(f"wrote {CONFIG.name}")
    return 0 if rep.passes else 1


if __name__ == "__main__":
    sys.exit(main())
