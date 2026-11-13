"""Follow-up cleaning is code (S18.04): limits, repeats of the question, and the schema/prompt contract."""

from app.chat.evidence import answer_schema
from app.chat.followups import MAX_FOLLOWUP_CHARS, MAX_FOLLOWUPS, clean_followups
from app.llm.prompts import default_registry
from tests.support.chat_gen import evidence


def test_repeat_dropped() -> None:
    q = "What is the notice period?"
    out = clean_followups(
        ["  what is the NOTICE   period ", "What is the notice period", "Who pays the fees?"], [q]
    )
    assert out == ("Who pays the fees?",)
    # a repeat of the standalone rewrite is dropped as well, and duplicates among the suggestions collapse
    assert clean_followups(["Who pays?", "who pays?", "Q2"], ["x", "Who pays"]) == ("Q2",)


def test_limits_enforced() -> None:
    long_one = "x" * (MAX_FOLLOWUP_CHARS + 1)
    exact = "y" * MAX_FOLLOWUP_CHARS
    out = clean_followups([long_one, "", "   ", 5, exact, "a?", "b?", "c?", "d?"])
    assert out == (exact, "a?", "b?") and len(out) == MAX_FOLLOWUPS


def test_schema_and_prompt_contract() -> None:
    schema = answer_schema(evidence())
    assert list(schema.model_json_schema()["properties"]) == ["status", "segments", "followups", "conflicts"]
    assert schema.model_validate({"status": "abstain", "segments": []}).followups == []  # type: ignore[attr-defined]
    p = default_registry().get("answer_rag", 3)
    assert "followups" in p.body and "conflicts" in p.body and p.version == 3
    assert "followups" in default_registry().get("answer_rag", 2).body  # the previous version stays loadable
    assert default_registry().get("answer_rag", 1).version == 1  # the previous version stays loadable
