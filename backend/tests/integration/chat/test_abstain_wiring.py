"""Scored abstention over the real turn graph (flag `chat_abstain_scored`): after verify the `final`
combiner keeps the answer, appends the partial-answer segment or replaces it with the warm abstain text,
with no extra model call; an unfitted config, a missing feature or the flag off keeps the old behaviour."""

import pathlib
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import abstain
from app.chat.gate import abstain_text
from app.explain.templates import PARTIAL_ANSWER_TEXT
from app.llm.providers.fake import Step, structured
from app.retrieval.config import get_config
from tests.integration.chat.conftest import owners
from tests.support.ledger_env import ledger_owners
from tests.support.turn_env import (
    EMBED_SLOT,
    answer,
    install_runner,
    new_session,
    parse_sse,
    post,
    seed_chunk,
)

__all__ = ["ledger_owners", "owners"]  # fixture imports

Q = "Services twelve months"
BODY = (
    "The Consultant shall provide the Services for twelve months. "
    "Either party may terminate on 30 days notice. "
    "Fees are due monthly."
)
TWO = answer(("Twelve months.", ["E1.0"]), ("Ninety days notice.", ["E1.1"]))


def verdicts(*labels: str) -> Step:
    return structured({"verdicts": [{"index": i, "verdict": v, "reason": "r"} for i, v in enumerate(labels)]})


def _norm(*features: str) -> dict[str, dict[str, float]]:
    return {f: {"mean": 0.0, "std": 1.0} for f in features}


def write_cfg(path: pathlib.Path, final_features: tuple[str, ...] = ("unsupported",), **gate: float) -> None:
    """Fitted fixture: p(final) = sigmoid(3 - 6 * unsupported) over one feature, so 0 unsupported answers
    (0.95), half partial (0.5) and all abstain (0.05) with thresholds 0.3 / 0.7."""
    cfg = {
        "version": 1,
        "fitted": True,
        "margin_rank": 3,
        "gate": {
            "features": ["top", "margin"],
            "bias": gate.get("bias", 3.0),
            "weights": {"top": 0.0, "margin": 0.0},
            "norm": _norm("top", "margin"),
            "t_abstain": 0.5,
            "t_answer": 0.5,
        },
        "final": {
            "features": list(final_features),
            "bias": 3.0,
            "weights": {f: -6.0 if f == "unsupported" else 0.0 for f in final_features},
            "norm": _norm(*final_features),
            "t_abstain": 0.3,
            "t_answer": 0.7,
        },
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    abstain._load.cache_clear()


@pytest.fixture
def fitted(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    p = tmp_path / "abstain.yaml"
    write_cfg(p)
    monkeypatch.setattr(abstain, "CONFIG_PATH", p)
    yield p  # type: ignore[misc]
    abstain._load.cache_clear()


async def turn(
    sm: async_sessionmaker[AsyncSession],
    owners_: Callable[[], Any],
    api: httpx.AsyncClient,
    script: list[Step],
    *,
    sse: bool = False,
    **settings: Any,
) -> tuple[Any, Any, uuid.UUID, httpx.Response]:
    o = await owners_()
    await seed_chunk(sm, o.id, body=BODY)
    prov = install_runner(api, sm, [EMBED_SLOT, *script], fast_path=True, **settings)
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1", sse=sse)
    assert r.status_code == 200, r.text
    return prov, o, sid, r


async def stored(api: httpx.AsyncClient, sid: uuid.UUID, o: Any) -> dict[str, Any]:
    h = (await api.get(f"/v1/chat/sessions/{sid}/messages", headers=o.auth)).json()["items"]
    out: dict[str, Any] = h[1]
    return out


async def run_row(sm: async_sessionmaker[AsyncSession], o: Any) -> tuple[list[str], Any]:
    async with sm() as s:
        row = (
            await s.execute(
                text("SELECT reason_codes, analysis FROM rag_runs WHERE owner_id = :o"), {"o": o.id}
            )
        ).one()
    return list(row[0]), row[1]


async def test_answer_when_supported(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    fitted: pathlib.Path,
) -> None:
    prov, o, sid, _ = await turn(
        sm, ledger_owners, api, [TWO, verdicts("supported", "supported")], chat_abstain_scored=True
    )
    msg = await stored(api, sid, o)
    assert msg["response_type"] == "answer"
    assert [s["text"] for s in msg["segments"]] == ["Twelve months.", "Ninety days notice."]
    codes, analysis = await run_row(sm, o)
    assert "PARTIAL_ANSWER" not in codes and "ABSTAIN_SCORED" not in codes
    assert analysis["abstain"]["unsupported"] == 0.0 and analysis["abstain"]["outcome"] == "answer"
    assert len(prov.requests) == 2  # generate + one support call: the decision costs no model call


async def test_partial_appends_template_and_streams(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    fitted: pathlib.Path,
) -> None:
    prov, o, sid, r = await turn(
        sm,
        ledger_owners,
        api,
        [TWO, verdicts("supported", "unsupported")],
        sse=True,
        chat_abstain_scored=True,
    )
    msg = await stored(api, sid, o)
    texts = [s["text"] for s in msg["segments"]]
    assert msg["response_type"] == "answer"
    assert texts == ["Twelve months.", "Ninety days notice.", PARTIAL_ANSWER_TEXT]
    assert msg["segments"][-1]["evidence"] == []  # uncited template, never a model quote
    codes, analysis = await run_row(sm, o)
    assert "PARTIAL_ANSWER" in codes and analysis["abstain"]["outcome"] == "partial"
    events = parse_sse(r.text)
    streamed = {e["index"]: e["text"] for e in events if e["type"] == "segment"}
    assert [streamed[i] for i in sorted(streamed)] == texts  # persisted text equals what streamed
    done = next(e for e in events if e["type"] == "done")
    assert done["content"] == msg["content"] == " ".join(texts)
    assert len(prov.requests) == 2


async def test_partial_after_removal_keeps_indexes_unique(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    fitted: pathlib.Path,
) -> None:
    """support_check@2 removes the unsupported segment; the partial segment must not reuse its index."""
    _, o, sid, r = await turn(
        sm,
        ledger_owners,
        api,
        [
            TWO,
            structured(
                {
                    "verdicts": [
                        {
                            "index": 0,
                            "verdict": "supported",
                            "contradicts": False,
                            "needed": [],
                            "reason": "r",
                        },
                        {
                            "index": 1,
                            "verdict": "unsupported",
                            "contradicts": False,
                            "needed": [],
                            "reason": "r",
                        },
                    ]
                }
            ),
        ],
        sse=True,
        chat_abstain_scored=True,
        chat_support_version=2,
    )
    msg = await stored(api, sid, o)
    assert [s["text"] for s in msg["segments"]] == ["Twelve months.", PARTIAL_ANSWER_TEXT]
    events = parse_sse(r.text)
    idx = [e["index"] for e in events if e["type"] == "segment"]
    assert len(idx) == len(set(idx)) == 3  # two model segments, then the partial one
    ver = next(e for e in events if e["type"] == "verification")
    assert {v["index"]: v["verdict"] for v in ver["verdicts"]}[1] == "removed"


async def test_abstain_replaces_answer(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    fitted: pathlib.Path,
) -> None:
    prov, o, sid, r = await turn(
        sm,
        ledger_owners,
        api,
        [TWO, verdicts("unsupported", "unsupported")],
        sse=True,
        chat_abstain_scored=True,
    )
    msg = await stored(api, sid, o)
    assert msg["response_type"] == "abstain"
    assert msg["content"] == abstain_text(1)
    codes, analysis = await run_row(sm, o)
    assert "ABSTAIN_SCORED" in codes and analysis["abstain"]["outcome"] == "abstain"
    events = parse_sse(r.text)
    ver = next(e for e in events if e["type"] == "verification")
    assert {v["verdict"] for v in ver["verdicts"]} == {"removed"}  # the streamed segments are withdrawn
    assert next(e for e in events if e["type"] == "done")["content"] == msg["content"]
    assert len(prov.requests) == 2


async def test_unfitted_config_keeps_old_behaviour(
    sm: async_sessionmaker[AsyncSession], ledger_owners: Callable[[], Any], api: httpx.AsyncClient
) -> None:
    """The committed config is `fitted: false`: the flag on changes nothing but records the features."""
    abstain._load.cache_clear()
    _, o, sid, _ = await turn(
        sm, ledger_owners, api, [TWO, verdicts("unsupported", "unsupported")], chat_abstain_scored=True
    )
    msg = await stored(api, sid, o)
    assert msg["response_type"] == "answer"
    assert [s["text"] for s in msg["segments"]] == ["Twelve months.", "Ninety days notice."]
    codes, analysis = await run_row(sm, o)
    assert "ABSTAIN_SCORED" not in codes and "PARTIAL_ANSWER" not in codes
    assert analysis["abstain"] == {"not_found": None, "unsupported": 1.0}


async def test_flag_off_is_unchanged(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    fitted: pathlib.Path,
) -> None:
    _, o, sid, _ = await turn(sm, ledger_owners, api, [TWO, verdicts("unsupported", "unsupported")])
    msg = await stored(api, sid, o)
    assert msg["response_type"] == "answer" and len(msg["segments"]) == 2
    codes, analysis = await run_row(sm, o)
    assert "ABSTAIN_SCORED" not in codes and "PARTIAL_ANSWER" not in codes
    assert "abstain" not in (analysis or {})  # nothing new is persisted with the switch off


async def test_missing_feature_keeps_old_behaviour(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    fitted: pathlib.Path,
) -> None:
    """A final model that needs `not_found` has no score without a plan: the answer stands."""
    write_cfg(fitted, ("not_found", "unsupported"))
    _, o, sid, _ = await turn(
        sm, ledger_owners, api, [TWO, verdicts("unsupported", "unsupported")], chat_abstain_scored=True
    )
    msg = await stored(api, sid, o)
    assert msg["response_type"] == "answer" and len(msg["segments"]) == 2
    codes, analysis = await run_row(sm, o)
    assert "ABSTAIN_SCORED" not in codes
    assert analysis["abstain"] == {"not_found": None, "unsupported": 1.0}


async def test_gate_abstain_records_scored_code(
    sm: async_sessionmaker[AsyncSession],
    ledger_owners: Callable[[], Any],
    api: httpx.AsyncClient,
    fitted: pathlib.Path,
) -> None:
    """The scored gate abstains before generation and the run says ABSTAIN_SCORED, not ABSTAIN_NO_EVIDENCE."""
    write_cfg(fitted, bias=-5.0)
    o = await ledger_owners()
    for i in range(3):  # margin needs a third candidate
        await seed_chunk(sm, o.id, f"Doc {i}", BODY)
    prov = install_runner(
        api, sm, [EMBED_SLOT], fast_path=True, cfg=get_config("retrieval@2-rerank"), chat_abstain_scored=True
    )
    sid = await new_session(api, o)
    r = await post(api, o, sid, Q, "m1")
    assert r.status_code == 200, r.text
    msg = await stored(api, sid, o)
    assert msg["response_type"] == "abstain"
    codes, _ = await run_row(sm, o)
    assert "ABSTAIN_SCORED" in codes and "ABSTAIN_NO_EVIDENCE" not in codes
    assert len(prov.requests) == 0  # no chat call at all: no generation (the embedding is not a chat request)
