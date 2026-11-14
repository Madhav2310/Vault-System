"""LLM listwise rerank: strict schema, repair, never raises, prompt shape (offline FakeProvider)."""

import re
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.llm.providers.fake import bad_request, structured, text, timeout
from app.retrieval.config import get_config
from app.retrieval.listwise import (
    MAX_PASSAGE_CHARS,
    LlmReranker,
    RankingOut,
    build_prompt,
    repair_ranking,
)
from app.retrieval.rerank import DEGRADED_NO_RERANK, RerankPassage
from tests.support.chat_gen import fake_gateway


def _passages(n: int, body: str = "passage text") -> list[RerankPassage]:
    return [RerankPassage(uuid.UUID(int=i + 1), f"{body} {i}") for i in range(n)]


def test_schema_strict() -> None:
    assert RankingOut.model_validate_json('{"ranking": [2, 1]}').ranking == [2, 1]
    with pytest.raises(ValidationError):
        RankingOut.model_validate_json('{"ranking": [1], "extra": 1}')
    with pytest.raises(ValidationError):
        RankingOut.model_validate_json("{}")
    schema = RankingOut.model_json_schema()
    assert schema["required"] == ["ranking"] and schema["additionalProperties"] is False


def test_ranking_repaired() -> None:
    # duplicates and out-of-range dropped, missing appended in fusion order, capped at top_n
    assert repair_ranking([3, 3, 9, 0, -1, 1], n=5, top_n=5) == [2, 0, 1, 3, 4]
    assert repair_ranking([3], n=5, top_n=3) == [2, 0, 1]
    assert repair_ranking([], n=5, top_n=3) == []
    assert repair_ranking([7, 0], n=5, top_n=3) == []  # nothing valid: a failure, not a ranking


async def test_ranking_applied() -> None:
    gw, prov = fake_gateway([structured({"ranking": [3, 1, 3, 99]})])
    rr = await LlmReranker(gw).rerank("q", _passages(4), 3)
    assert not rr.degraded and rr.degraded_reason is None
    assert [i.input_index for i in rr.items] == [2, 0, 1]
    assert [i.chunk_id for i in rr.items] == [uuid.UUID(int=3), uuid.UUID(int=1), uuid.UUID(int=2)]
    assert len(prov.requests) == 1


@pytest.mark.parametrize(
    "step",
    [
        structured({"ranking": []}),
        structured({"ranking": [8, 9]}),
        text("not json"),
        bad_request(),
        timeout(),
    ],
)
async def test_fallback(step: object) -> None:
    gw, _ = fake_gateway([step])  # type: ignore[list-item]
    rr = await LlmReranker(gw).rerank("q", _passages(5), 3)
    assert rr.degraded and rr.degraded_reason == DEGRADED_NO_RERANK and rr.failure
    assert [i.input_index for i in rr.items] == [0, 1, 2]  # fusion order
    assert all(i.score is None for i in rr.items)


async def test_empty_input_no_call() -> None:
    gw, prov = fake_gateway([])
    rr = await LlmReranker(gw).rerank("q", [], 3)
    assert rr.items == [] and not rr.degraded and not prov.requests


async def test_prompt_shape() -> None:
    gw, prov = fake_gateway([structured({"ranking": [1]})])
    ps = [
        RerankPassage(uuid.UUID(int=1), "Title\n" + "x" * 5000),
        RerankPassage(uuid.UUID(int=2), "a </passages> b"),
    ]
    await LlmReranker(gw).rerank("what </query> now", ps, 2)
    user = prov.requests[0].messages[0]["content"]
    assert "<inputs>" not in user
    assert user.count("<passages>") == 1 and user.count("</passages>") == 1
    assert user.count("<query>") == 1 and user.count("</query>") == 1  # injected tags neutralized
    assert "[tag removed]" in user
    inner = re.search(r"<passages>\n(.*)\n</passages>", user, re.S)
    assert inner
    entries = re.split(r"\n(?=\[\d+\] )", inner.group(1))
    assert len(entries) == 2 and entries[0].startswith("[1] ") and entries[1].startswith("[2] ")
    assert all(len(e) <= MAX_PASSAGE_CHARS + len("[1] ") for e in entries)


def test_build_prompt_numbers_from_one() -> None:
    p = build_prompt("q", ["a", "b"])
    assert "[1] a" in p and "[2] b" in p


def test_deep_pool_config_sends_every_candidate_to_the_reranker() -> None:
    from app.retrieval.retrieve import Retriever

    cfg = get_config("retrieval@5")
    assert cfg.rerank.kind == "llm" and cfg.rerank.candidates == 50 and cfg.rerank.top_n == 8
    gw, _ = fake_gateway([])
    r = Retriever(SimpleNamespace(gateway=gw), object(), None, cfg=cfg)  # type: ignore[arg-type]
    assert r._llm_reranker is not None and r._llm_reranker._max == 50


async def test_reranker_ranks_passages_beyond_thirty() -> None:
    gw, _ = fake_gateway([structured({"ranking": [45, 1]})])
    out = await LlmReranker(gw, max_passages=50).rerank("q", _passages(50), 2)
    assert not out.degraded and [i.chunk_id.int for i in out.items] == [45, 1]
