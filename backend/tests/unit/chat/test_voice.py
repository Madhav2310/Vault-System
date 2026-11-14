"""The voice fragment is appended only when switched on and never touches the answer prompt (S23.01 AC2)."""

import hashlib
import uuid

from app.chat.voice import SEPARATOR, VOICE, fragment, generate_call, recorded
from app.core.settings import Settings
from app.llm.prompts import default_registry


def _settings(voice: bool = False) -> Settings:
    return Settings(
        _env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x", chat_voice=voice
    )  # type: ignore[call-arg]


ANSWER_PROMPTS = [("answer_rag", 3), ("answer_rag", 4), ("answer_compare", 1)]
FORBIDDEN = ("handle", "segment", "evidence", "citation", "schema", "json")
# sha256 of section 2 ("Citation rule") of answer_rag@3, stripped. Regenerate:
#   python3 -c "import hashlib;b=open('backend/prompts/answer_rag@3.md').read().split('---\n',2)[2].strip();
#   print(hashlib.sha256(b.split('## 2. Citation rule')[1].split('## 3.')[0].strip().encode()).hexdigest())"
GOLDEN_CITATION_SECTION = "40bb385d7ffe72e08ba33048f41b1cffcf32ff2316c4564c22224196241e4c57"


def test_default_off() -> None:
    s = _settings()
    assert s.chat_voice is False
    assert fragment(s) is None and generate_call(s) == {} and recorded(s) == []


def test_fragment_appended() -> None:
    reg = default_registry()
    voice = reg.get(*VOICE)
    on, off = _settings(True), _settings()
    assert (voice.id, voice.version) == ("voice", 1)
    assert generate_call(on) == {"system_suffix": SEPARATOR + voice.body}
    assert recorded(on) == ["voice@1"]
    for key in ANSWER_PROMPTS:
        body = reg.get(*key).body
        system = body + generate_call(on)["system_suffix"]
        assert system[len(body) :] == SEPARATOR + voice.body  # the answer prompt itself is not edited
        assert body + generate_call(off).get("system_suffix", "") == body  # off: nothing appended


def test_fragment_leaves_citation_rules_alone() -> None:
    body = default_registry().get(*VOICE).body
    for word in FORBIDDEN:
        assert word not in body.lower(), word
    assert "!" not in body.replace('!"', "")


def test_citation_rules_golden() -> None:
    """Section 2 of answer_rag@3 (the citation rules) is pinned, so a voice change cannot edit it."""
    body = default_registry().get("answer_rag", 3).body
    section = body.split("## 2. Citation rule")[1].split("## 3.")[0]
    assert hashlib.sha256(section.strip().encode()).hexdigest() == GOLDEN_CITATION_SECTION


async def test_request_system_prompt_carries_fragment() -> None:
    from app.chat.nodes.generate import generate
    from app.llm.providers.fake import structured
    from tests.support.chat_gen import evidence, fake_gateway

    reply = structured({"status": "answer", "segments": [{"text": "Twelve months.", "evidence": ["E1.0"]}]})
    gw, fake = fake_gateway([reply, reply])
    owner = uuid.UUID(int=1)
    base = default_registry().get("answer_rag", 3).body
    await generate(gw, owner_id=owner, evidence=evidence(), question="q", route="qa")
    await generate(
        gw,
        owner_id=owner,
        evidence=evidence(),
        question="q",
        route="qa",
        **generate_call(_settings(True)),
    )
    off, on = fake.requests
    assert off.system == base
    assert on.system == base + SEPARATOR + default_registry().get(*VOICE).body
    assert on.output_schema == off.output_schema  # the strict schema is untouched
