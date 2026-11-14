"""Scored abstention (design §8.4, S22.07): a logistic combiner replaces the binary evidence floor.

Features (all numbers, computed by code): `top` (best retrieval score, the gate's own `top_score`), `margin`
(top minus the score at rank `margin_rank`), `not_found` (plan elements without a valid handle, S22.04, capped
at `MAX_NOT_FOUND`) and `unsupported` (share of cited segments the support check labelled unsupported,
S22.06, measured before removal). The probability that the question is answerable from the documents is
`sigmoid(bias + sum_i w_i * (x_i - mean_i) / std_i)`. Two thresholds cut it into three outcomes:
`p < t_abstain` abstain, `t_abstain <= p < t_answer` partial (answer what is supported and say the rest
was not found), `p >= t_answer` answer. Both thresholds are fitted offline (`eval/calibrate_abstain.py`)
and loaded from `config/abstain@1.yaml`; the combiner never calls a model and decides nothing beyond
these cuts.

Two models share the math. `gate` (top, margin) runs before generation and can only abstain; `final` (all four
features) runs after generation. A feature that was not computed is `None`: the model that needs it returns no
score (never an imputed value) and the caller falls back (final -> gate -> the old behaviour).
"""

import dataclasses
import math
import pathlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.chat.citations import Resolution, ResolvedSegment
from app.chat.gate import Scored
from app.explain.templates import PARTIAL_ANSWER_TEXT

ABSTAIN_SCORED = "ABSTAIN_SCORED"
PARTIAL_ANSWER = "PARTIAL_ANSWER"

FEATURES = ("top", "margin", "not_found", "unsupported")
GATE_FEATURES = ("top", "margin")
MAX_NOT_FOUND = 4
CONFIG_PATH = pathlib.Path(__file__).resolve().parents[2] / "config" / "abstain@1.yaml"

Outcome = Literal["answer", "partial", "abstain"]
Features = Mapping[str, float | None]


class AbstainConfigError(ValueError):
    """The abstain config is missing, malformed or not calibrated."""


class Norm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mean: float
    std: float = Field(gt=0)


class ModelCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    features: list[str]
    bias: float
    weights: dict[str, float]
    norm: dict[str, Norm]
    t_abstain: float = Field(ge=0, le=1)
    t_answer: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _consistent(self) -> "ModelCfg":
        if not self.features or len(set(self.features)) != len(self.features):
            raise ValueError("features must be non-empty and unique")
        if unknown := set(self.features) - set(FEATURES):
            raise ValueError(f"unknown features {sorted(unknown)}")
        if set(self.weights) != set(self.features) or set(self.norm) != set(self.features):
            raise ValueError("weights and norm need exactly one entry per feature")
        if self.t_abstain > self.t_answer:
            raise ValueError("t_abstain must not exceed t_answer")
        return self


class AbstainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    fitted: bool
    margin_rank: int = Field(default=3, ge=2, le=20)
    gate: ModelCfg | None = None
    final: ModelCfg | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "AbstainConfig":
        if self.fitted and self.gate is None:
            raise ValueError("a fitted config needs a gate model")
        if self.gate is not None and tuple(self.gate.features) != GATE_FEATURES:
            raise ValueError(f"the gate model uses exactly {GATE_FEATURES}")
        return self


def load_config(path: pathlib.Path | None = None) -> AbstainConfig:
    """Load and validate; raises `AbstainConfigError` for a bad file or one that was never calibrated."""
    return _load(path or CONFIG_PATH)


@lru_cache(maxsize=4)
def _load(path: pathlib.Path) -> AbstainConfig:
    try:
        cfg = AbstainConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as e:
        raise AbstainConfigError(f"{path.name}: {e}") from e
    if not cfg.fitted:
        raise AbstainConfigError(
            f"{path.name} is not calibrated: run `python -m eval.calibrate_abstain --write`"
        )
    return cfg


# --- features -------------------------------------------------------------------------------------------


def score_list(candidates: Iterable[Scored]) -> list[float]:
    """The gate signal per candidate, best first: rerank scores when any exist, else `1 - vector_distance`."""
    cands = list(candidates)
    rerank = [c.rerank_score for c in cands if c.rerank_score is not None]
    if rerank:
        return sorted(rerank, reverse=True)
    return sorted((1.0 - c.vector_distance for c in cands if c.vector_distance is not None), reverse=True)


def retrieval_features(candidates: Iterable[Scored], margin_rank: int) -> dict[str, float | None]:
    """`top` and `margin`; `margin` is None when fewer than `margin_rank` candidates carry a score."""
    s = score_list(candidates)
    if not s:
        return {"top": None, "margin": None}
    return {"top": s[0], "margin": s[0] - s[margin_rank - 1] if len(s) >= margin_rank else None}


def with_generation_features(
    base: Features, not_found: int | None, unsupported_share: float | None
) -> dict[str, float | None]:
    """Add the post-generation features; `None` stays `None` (not computed is not zero)."""
    out = dict(base)
    out["not_found"] = None if not_found is None else float(min(max(not_found, 0), MAX_NOT_FOUND))
    out["unsupported"] = None if unsupported_share is None else min(max(unsupported_share, 0.0), 1.0)
    return out


def unsupported_share(verdicts: Iterable[str | None]) -> float | None:
    """Share of segments labelled `unsupported` among those the check labelled; None when none were."""
    labelled = [v for v in verdicts if v in ("supported", "partial", "unsupported")]
    return None if not labelled else sum(v == "unsupported" for v in labelled) / len(labelled)


# --- the combiner ---------------------------------------------------------------------------------------


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _z(
    features: Sequence[str],
    bias: float,
    weights: Mapping[str, float],
    norm: Mapping[str, tuple[float, float]],
    x: Features,
) -> float | None:
    z = bias
    for f in features:
        v = x.get(f)
        if v is None:
            return None
        mean, std = norm[f]
        z += weights[f] * (v - mean) / std
    return z


def probability(model: ModelCfg, x: Features) -> float | None:
    """P(answerable), or None when a feature the model needs was not computed."""
    z = _z(model.features, model.bias, model.weights, {k: (n.mean, n.std) for k, n in model.norm.items()}, x)
    return None if z is None else sigmoid(z)


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    p: float | None
    model: str | None  # "final", "gate" or None (no score: the caller keeps the old behaviour)
    missing: tuple[str, ...] = ()


def cut(p: float, t_abstain: float, t_answer: float) -> Outcome:
    """Boundaries pass up: a score at a threshold gets the more permissive outcome."""
    if p < t_abstain:
        return "abstain"
    return "partial" if p < t_answer else "answer"


def decide(cfg: AbstainConfig, x: Features, *, stage: Literal["gate", "final"]) -> Decision:
    """Outcome at `stage`. At the gate only abstain applies (partial needs an answer: it reads `answer`)."""
    models: list[tuple[str, ModelCfg | None]] = [("final", cfg.final)] if stage == "final" else []
    models.append(("gate", cfg.gate))
    for name, m in models:
        if m is None:
            continue
        p = probability(m, x)
        if p is None:
            continue
        out = cut(p, m.t_abstain, m.t_answer)
        if stage == "gate" and out == "partial":
            out = "answer"
        return Decision(out, p, name)
    missing = tuple(f for f in (cfg.gate.features if cfg.gate else GATE_FEATURES) if x.get(f) is None)
    return Decision("answer", None, None, missing)


def apply_partial(resolution: Resolution) -> tuple[Resolution, tuple[str, ...]]:
    """Partial outcome: keep the supported segments and end with one uncited template segment that says the
    rest was not found (the S22.04 pattern, so no new response type or UI change)."""
    segments = [
        *resolution.segments,
        ResolvedSegment(len(resolution.segments), PARTIAL_ANSWER_TEXT, "none", ()),
    ]
    return dataclasses.replace(resolution, segments=tuple(segments)), (PARTIAL_ANSWER,)


# --- fitting (offline; pure, deterministic, no dependency) ------------------------------------------------


@dataclass(frozen=True)
class FitRow:
    x: Mapping[str, float | None]
    answerable: bool


def standardise(rows: Sequence[FitRow], features: Sequence[str]) -> dict[str, tuple[float, float]]:
    """Population mean and std per feature (std 1.0 when the feature is constant)."""
    out: dict[str, tuple[float, float]] = {}
    for f in features:
        vals = [v for r in rows if (v := r.x.get(f)) is not None]
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        out[f] = (mean, math.sqrt(var) if var > 0 else 1.0)
    return out


def fit_logistic(
    rows: Sequence[FitRow],
    features: Sequence[str],
    *,
    l2: float = 0.01,
    lr: float = 0.5,
    steps: int = 3000,
) -> tuple[float, dict[str, float], dict[str, tuple[float, float]]]:
    """Batch gradient descent on the L2-penalised log loss over standardised features, from zero weights, a
    fixed number of steps: same input, same output. Returns (bias, weights, norm). Rows with a missing feature
    are not used (the caller filters); needs both classes."""
    data = [r for r in rows if all(r.x.get(f) is not None for f in features)]
    if not data or all(r.answerable for r in data) or not any(r.answerable for r in data):
        raise ValueError("fit needs rows of both classes with every feature present")
    norm = standardise(data, features)
    xs = [[(float(r.x[f]) - norm[f][0]) / norm[f][1] for f in features] for r in data]  # type: ignore[arg-type]
    ys = [1.0 if r.answerable else 0.0 for r in data]
    n = len(data)
    b, w = 0.0, [0.0] * len(features)
    for _ in range(steps):
        gb, gw = 0.0, [0.0] * len(features)
        for x, y in zip(xs, ys, strict=True):
            err = sigmoid(b + sum(wi * xi for wi, xi in zip(w, x, strict=True))) - y
            gb += err
            for j, xj in enumerate(x):
                gw[j] += err * xj
        b -= lr * gb / n
        w = [wi - lr * (g / n + l2 * wi) for wi, g in zip(w, gw, strict=True)]
    return b, dict(zip(features, w, strict=True)), norm


@dataclass(frozen=True)
class CurvePoint:
    t: float
    false_refusal: float  # share of answerable rows with p < t
    recall: float  # share of should-abstain rows with p < t
    answered_error: float | None  # share of should-abstain rows among rows with p >= t (None: no rows)


def curve(scored: Sequence[tuple[float, bool]]) -> list[CurvePoint]:
    """One point per distinct observed p, ascending. Rows are (p, should_abstain); a row abstains when
    p < t, so tied rows always move together."""
    n_ans = sum(1 for _, a in scored if not a)
    n_abs = sum(1 for _, a in scored if a)
    out: list[CurvePoint] = []
    for t in sorted({p for p, _ in scored}):
        below = [a for p, a in scored if p < t]
        above = [a for p, a in scored if p >= t]
        out.append(
            CurvePoint(
                t,
                sum(1 for a in below if not a) / n_ans if n_ans else 0.0,
                sum(1 for a in below if a) / n_abs if n_abs else 0.0,
                sum(1 for a in above if a) / len(above) if above else None,
            )
        )
    return out


def pick_thresholds(
    scored: Sequence[tuple[float, bool]], *, max_false_refusal: float = 0.02, max_answered_error: float = 0.05
) -> tuple[float, float, CurvePoint] | None:
    """(t_abstain, t_answer, point at t_abstain), or None without rows.

    `t_abstain` is the largest observed p whose false refusals stay within `max_false_refusal` (most recall
    for the allowed refusals). `t_answer` is the lowest observed p at or above it where the rows that would be
    answered outright are at most `max_answered_error` should-abstain; when `t_abstain` already meets that,
    the partial band is empty (`t_answer == t_abstain`)."""
    pts = curve(scored)
    ok = [p for p in pts if p.false_refusal <= max_false_refusal]
    if not ok:
        return None
    chosen = ok[-1]
    t_answer = chosen.t
    for p in pts:
        if p.t >= chosen.t and p.answered_error is not None and p.answered_error <= max_answered_error:
            t_answer = p.t
            break
    return chosen.t, t_answer, chosen
