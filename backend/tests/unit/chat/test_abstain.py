"""Scored abstention: combiner math, thresholds, missing features, the gate node switch."""

import dataclasses
import math
import pathlib
import re
from types import SimpleNamespace

import pytest
import yaml

from app.chat import abstain as A
from app.chat.citations import Resolution, ResolvedSegment
from app.chat.nodes.gate import gate
from app.explain import templates as T


def model(
    t_abstain: float = 0.3, t_answer: float = 0.7, feats: tuple[str, ...] = A.GATE_FEATURES
) -> A.ModelCfg:
    w = {"top": 2.0, "margin": 1.0, "unsupported": -2.0}
    return A.ModelCfg(
        features=list(feats),
        bias=0.0,
        weights={f: w[f] for f in feats},
        norm={f: A.Norm(mean=1.0, std=2.0) for f in feats},
        t_abstain=t_abstain,
        t_answer=t_answer,
    )


def cfg(gate_model: A.ModelCfg | None = None, final: A.ModelCfg | None = None) -> A.AbstainConfig:
    return A.AbstainConfig(fitted=True, gate=gate_model or model(), final=final)


def test_combiner() -> None:
    # hand values: top=3 -> (3-1)/2=1 -> 2.0; margin=-1 -> (-1-1)/2=-1 -> -1.0; z=1.0; p=1/(1+e^-1)
    p = A.probability(model(), {"top": 3.0, "margin": -1.0})
    assert p == pytest.approx(0.7310585786)
    assert A.sigmoid(0.0) == 0.5 and A.sigmoid(-800.0) == 0.0 and A.sigmoid(800.0) == 1.0
    # three outcomes by two thresholds, boundaries pass up
    assert A.cut(0.29, 0.3, 0.7) == "abstain"
    assert A.cut(0.3, 0.3, 0.7) == "partial"
    assert A.cut(0.7, 0.3, 0.7) == "answer"
    # threshold selection on a fixture: (p, should_abstain); 5 answerable, 4 should-abstain
    rows = [
        *[(0.9, False), (0.8, False), (0.7, False), (0.6, False), (0.2, False)],
        *[(0.1, True), (0.15, True), (0.55, True), (0.65, True)],
    ]
    pts = {p.t: p for p in A.curve(rows)}
    assert pts[0.2].false_refusal == 0.0 and pts[0.2].recall == 0.5  # 0.1 and 0.15 are below 0.2
    assert pts[0.6].false_refusal == pytest.approx(0.2) and pts[0.6].recall == 0.75
    got = A.pick_thresholds(rows, max_false_refusal=0.0, max_answered_error=0.25)
    assert got is not None
    t_abs, t_ans, pt = got
    assert t_abs == 0.2 and pt.recall == 0.5  # the largest t with no false refusal (0.2 is answerable)
    # at or above 0.55: 2 of 6 should abstain (0.33); at or above 0.6: 1 of 5 (0.2): the first t within 0.25
    assert pts[0.55].answered_error == pytest.approx(2 / 6)
    assert pts[0.6].answered_error == pytest.approx(0.2) and t_ans == 0.6
    assert A.pick_thresholds([], max_false_refusal=0.02) is None


def test_curve_ties_move_together() -> None:
    pts = A.curve([(0.5, False), (0.5, True), (0.9, False)])
    assert [p.t for p in pts] == [0.5, 0.9]  # one candidate for the tied pair
    assert pts[0].false_refusal == 0.0  # a row exactly at t is not refused
    assert pts[1].false_refusal == 0.5 and pts[1].recall == 1.0  # both 0.5 rows fall below 0.9


def test_partial_band_empty_when_answer_side_is_pure() -> None:
    got = A.pick_thresholds([(0.9, False), (0.8, False), (0.1, True)], max_false_refusal=0.0)
    assert got is not None and got[0] == 0.8 and got[1] == 0.8


def test_missing_features_are_never_guessed() -> None:
    m = model()
    assert A.probability(m, {"top": 1.0, "margin": None}) is None
    assert A.probability(m, {"top": 1.0}) is None
    c = cfg(final=model(feats=A.FEATURES))
    # final needs all three: with `unsupported` absent it falls back to the gate model, explicitly
    x: dict[str, float | None] = {"top": 3.0, "margin": -1.0, "unsupported": None}
    d = A.decide(c, x, stage="final")
    assert d.model == "gate" and d.p == pytest.approx(0.7310585786)
    x["unsupported"] = 0.0
    assert A.decide(c, x, stage="final").model == "final"
    # nothing computable: the old behaviour (answer) and the missing names
    d = A.decide(c, {"top": None, "margin": None}, stage="gate")
    assert (d.outcome, d.p, d.model, d.missing) == ("answer", None, None, ("top", "margin"))


def test_gate_stage_never_returns_partial() -> None:
    c = cfg(model(t_abstain=0.3, t_answer=0.99))
    assert A.decide(c, {"top": 3.0, "margin": -1.0}, stage="gate").outcome == "answer"
    assert A.decide(c, {"top": 3.0, "margin": -1.0}, stage="final").outcome == "partial"


def test_monotonic() -> None:
    m = model(feats=A.FEATURES)
    base = {"top": 1.0, "margin": 1.0, "unsupported": 0.0}

    def p(**kw: float) -> float:
        out = A.probability(m, {**base, **kw})
        assert out is not None
        return out

    assert p(top=2.0) > p(top=1.0) > p(top=0.0)
    assert p(margin=2.0) > p(margin=0.0)
    assert p(unsupported=0.5) < p(unsupported=0.0)


def test_fit_is_deterministic_and_directional() -> None:
    rows = [A.FitRow({"top": 1.0 + 0.1 * i, "margin": 0.5}, True) for i in range(8)] + [
        A.FitRow({"top": -1.0 - 0.1 * i, "margin": 0.5}, False) for i in range(8)
    ]
    a = A.fit_logistic(rows, ("top", "margin"))
    assert a == A.fit_logistic(rows, ("top", "margin"))
    bias, w, norm = a
    assert w["top"] > 0 and norm["margin"][1] == 1.0  # constant feature: std guard
    m = A.ModelCfg(
        features=["top", "margin"], bias=bias, weights=w,
        norm={k: A.Norm(mean=v[0], std=v[1]) for k, v in norm.items()}, t_abstain=0.5, t_answer=0.5,
    )  # fmt: skip
    hi, lo = A.probability(m, {"top": 1.5, "margin": 0.5}), A.probability(m, {"top": -1.5, "margin": 0.5})
    assert hi is not None and lo is not None and hi > 0.9 > 0.1 > lo
    assert not math.isnan(bias)
    with pytest.raises(ValueError):
        A.fit_logistic([A.FitRow({"top": 1.0, "margin": 1.0}, True)], ("top", "margin"))


@dataclasses.dataclass
class C:
    rerank_score: float | None = None
    vector_distance: float | None = None
    id_match: bool = False


def test_retrieval_features() -> None:
    cs = [C(rerank_score=s) for s in (1.0, 4.0, 2.0, 0.5)]
    assert A.retrieval_features(cs, 3) == {"top": 4.0, "margin": 3.0}  # 4.0 - third best (1.0)
    assert A.retrieval_features(cs[:2], 3) == {"top": 4.0, "margin": None}
    assert A.retrieval_features([C(vector_distance=0.25)], 2) == {"top": 0.75, "margin": None}
    assert A.retrieval_features([], 3) == {"top": None, "margin": None}


def test_generation_features() -> None:
    assert A.with_generation_features({"top": 1.0}, None) == {"top": 1.0, "unsupported": None}
    assert A.with_generation_features({"top": 1.0}, 1.5) == {"top": 1.0, "unsupported": 1.0}
    assert A.unsupported_share(["supported", "unsupported", "unchecked", None]) == 0.5
    assert A.unsupported_share(["unchecked"]) is None


def test_apply_partial_adds_uncited_statement() -> None:
    r = Resolution((ResolvedSegment(0, "x", "cited", ()),))
    out, reasons = A.apply_partial(r)
    assert reasons == (A.PARTIAL_ANSWER,) and len(out.segments) == 2
    last = out.segments[-1]
    assert last.text == T.PARTIAL_ANSWER_TEXT and last.support == "none" and last.references == ()


def test_new_codes_templated_and_warm() -> None:
    for code in (A.ABSTAIN_SCORED, A.PARTIAL_ANSWER):
        line = T.REASON_TEMPLATES[code]
        assert line and "!" not in line and re.search(r"\b(try|name|ask)\b", line, re.I)
    assert "!" not in T.PARTIAL_ANSWER_TEXT and re.search(r"\b(check|name)\b", T.PARTIAL_ANSWER_TEXT)


def test_config_loader(tmp_path: pathlib.Path) -> None:
    with pytest.raises(A.AbstainConfigError, match="not calibrated"):
        A.load_config()  # the shipped file is unfitted
    good = tmp_path / "ok.yaml"
    good.write_text(yaml.safe_dump({"version": 1, "fitted": True, "gate": model().model_dump()}))
    assert A.load_config(good).gate is not None
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"version": 1, "fitted": True, "gate": model().model_dump(), "extra": 1}))
    with pytest.raises(A.AbstainConfigError):
        A.load_config(bad)
    swap = tmp_path / "swap.yaml"
    swapped = model().model_dump() | {"t_abstain": 0.8, "t_answer": 0.2}
    swap.write_text(yaml.safe_dump({"version": 1, "fitted": True, "gate": swapped}))
    with pytest.raises(A.AbstainConfigError):
        A.load_config(swap)


# --- the gate node: flag off is the old gate, byte for byte ---------------------------------------------


def _ctx(scored: bool, floor_enabled: bool = True) -> SimpleNamespace:
    gate_cfg = SimpleNamespace(floor=1.5, enabled=floor_enabled, high=2.0)
    retriever = SimpleNamespace(config=SimpleNamespace(gate=gate_cfg))
    settings = SimpleNamespace(chat_abstain_scored=scored)
    return SimpleNamespace(
        deps=SimpleNamespace(retriever=retriever, gateway=SimpleNamespace(settings=settings))
    )


def _state(score: float, rest: tuple[float, float] = (0.0, -1.0)) -> SimpleNamespace:
    cands = [C(rerank_score=score), C(rerank_score=rest[0]), C(rerank_score=rest[1])]
    return SimpleNamespace(
        retrieval=SimpleNamespace(candidates=cands, degraded=[]),
        mode="rag",
        gate_abstain=False,
        last_decision=None,
    )


async def test_flag_off_is_old_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object) -> None:
        pytest.fail("config must not be read with the flag off")

    monkeypatch.setattr("app.chat.nodes.gate.load_config", boom)
    below, above = _state(1.0), _state(2.0)
    await gate(below, _ctx(False))  # type: ignore[arg-type]
    await gate(above, _ctx(False))  # type: ignore[arg-type]
    assert below.gate_abstain
    assert below.last_decision == {"passed": False, "top_score": 1.0, "floor": 1.5, "bypass": None}
    assert not above.gate_abstain and above.last_decision["passed"] is True  # type: ignore[index]


async def test_flag_on_scored_gate_and_uncalibrated_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _state(1.0)
    await gate(s, _ctx(True))  # type: ignore[arg-type]  # shipped config is unfitted -> the old floor decides
    assert s.gate_abstain and "bypass" in s.last_decision  # type: ignore[operator]
    c = cfg(model(t_abstain=0.5, t_answer=0.5))
    monkeypatch.setattr("app.chat.nodes.gate.load_config", lambda: c)
    lo, hi = _state(-3.0, (-4.0, -5.0)), _state(5.0)
    await gate(lo, _ctx(True, floor_enabled=False))  # type: ignore[arg-type]
    await gate(hi, _ctx(True, floor_enabled=False))  # type: ignore[arg-type]
    assert lo.gate_abstain and lo.last_decision["scored"] == "gate"  # type: ignore[index]
    assert not hi.gate_abstain
    full = _state(-3.0, (-4.0, -5.0))
    full.mode = "full_context"
    await gate(full, _ctx(True, floor_enabled=False))  # type: ignore[arg-type]
    assert not full.gate_abstain  # full-context turns always generate
