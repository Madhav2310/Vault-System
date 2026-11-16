"""Evidence set, handles, answer schema and manifest."""

import re
import uuid

import pytest
from pydantic import ValidationError

from app.chat.evidence import (
    MAX_ENUM_HANDLES,
    EvidenceChunk,
    answer_schema,
    build_evidence,
    cache_mode_for,
)
from app.ingestion.sentences import split_sentences
from app.llm.providers.openai_chat import strict_schema


def _items(model: type) -> dict:  # type: ignore[type-arg]
    """The strict schema of a segment's `evidence` items (what OpenAI would receive)."""
    schema = strict_schema(model.model_json_schema())  # type: ignore[attr-defined]
    return schema["$defs"]["Segment"]["properties"]["evidence"]["items"]  # type: ignore[no-any-return]


def _chunk(
    text: str,
    *,
    pages: tuple[int, int] = (4, 4),
    section: tuple[str, ...] = ("Term",),
    title: str = "Services Agreement",
    ver: int = 2,
) -> EvidenceChunk:
    sents = tuple(split_sentences(text))
    return EvidenceChunk(uuid.uuid4(), uuid.uuid4(), title, ver, pages[0], pages[1], section, text, sents)


TEXT = (
    "The Consultant shall provide the Services for twelve months. "
    "Either party may terminate on 30 days notice.\nNotice must be written."
)


def test_one_handle_per_sentence() -> None:
    chunks = [_chunk(TEXT), _chunk("One sentence only."), _chunk("A. Bee is here. Cee too.")]
    ev = build_evidence(chunks)
    expected = [f"E{k}.{i}" for k, c in enumerate(chunks, 1) for i in range(len(c.sentences))]
    assert list(ev.handles) == expected
    lines = [ln for ln in ev.text.splitlines() if re.match(r"^E\d+\.\d+ ", ln)]
    assert [ln.split(" ", 1)[0] for ln in lines] == expected  # exactly one line, one handle, per sentence
    assert len(set(ev.handles)) == len(ev.handles)


def test_blocks_reconstruct_text() -> None:
    c = _chunk(TEXT)
    ev = build_evidence([c])
    body = [ln.split(" ", 1)[1] for ln in ev.text.splitlines() if re.match(r"^E1\.\d+ ", ln)]
    assert " ".join(body) == " ".join(c.original_text.split())  # modulo inter-sentence whitespace


def test_title_format() -> None:
    pdf = _chunk("A sentence here.", pages=(4, 5), section=("Services", "Term"))
    one_page = _chunk("Another sentence.", pages=(7, 7), section=())
    docx = _chunk("Word text here.", pages=(0, 0), section=("Heading",), title="Memo", ver=1)
    ev = build_evidence([pdf, one_page, docx])
    heads = [ln for ln in ev.text.splitlines() if ln.startswith("[E")]
    assert heads == [
        "[E1] Services Agreement v2 · p.4-5 · Services > Term",
        "[E2] Services Agreement v2 · p.7",
        "[E3] Memo v1 · Heading",
    ]


def test_no_prefix_in_evidence() -> None:
    c = _chunk(TEXT)
    ev = build_evidence([c])
    assert "Document:" not in ev.text and "| Section:" not in ev.text and "| Page:" not in ev.text
    assert ev.text.startswith("<evidence>\n[E1]") and ev.text.endswith("</evidence>")


def test_manifest_complete() -> None:
    chunks = [_chunk(TEXT), _chunk("Second chunk. With two sentences.")]
    ev = build_evidence(chunks)
    assert set(ev.manifest) == set(ev.handles)
    for h, m in ev.manifest.items():
        k, i = map(int, h[1:].split("."))
        c = chunks[k - 1]
        assert (m.chunk_id, m.version_id, m.sentence_index) == (c.chunk_id, c.version_id, i)
        assert (m.start, m.end) == c.sentences[i] and ev.chunk_handles[c.chunk_id] == f"E{k}"


def test_schema_enum_vs_string() -> None:
    ev = build_evidence([_chunk(TEXT)])
    rag = answer_schema(ev, "rag")
    ok = {"status": "answer", "segments": [{"text": "x", "evidence": ["E1.0", "E1.2"]}]}
    assert rag.model_validate(ok).segments[0].evidence == ["E1.0", "E1.2"]  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):  # RAG mode: a handle that is not in the request is rejected
        rag.model_validate({"status": "answer", "segments": [{"text": "x", "evidence": ["E9.9"]}]})
    assert sorted(_items(rag)["enum"]) == sorted(ev.handles)  # the enum equals the handles present
    full = answer_schema(ev, "full_context")
    assert full.model_validate({"status": "answer", "segments": [{"text": "x", "evidence": ["E9.9"]}]})
    assert _items(full) == {"type": "string"}  # plain strings
    abstain = rag.model_validate({"status": "abstain", "segments": []})
    assert abstain.status == "abstain"  # type: ignore[attr-defined]
    assert cache_mode_for("rag") == "explicit" and cache_mode_for("full_context") is None


def test_enum_limit_fallback() -> None:
    big = _chunk(" ".join(f"Sentence number {i} is here." for i in range(MAX_ENUM_HANDLES + 5)))
    ev = build_evidence([big])
    assert len(ev.handles) > MAX_ENUM_HANDLES and not ev.enum_ok
    assert _items(answer_schema(ev, "rag")) == {"type": "string"}  # never an oversized enum
    just_under = build_evidence(
        [_chunk(" ".join(f"Sentence number {i} is here." for i in range(MAX_ENUM_HANDLES)))]
    )
    assert just_under.enum_ok
    assert len(_items(answer_schema(just_under, "rag"))["enum"]) == MAX_ENUM_HANDLES
