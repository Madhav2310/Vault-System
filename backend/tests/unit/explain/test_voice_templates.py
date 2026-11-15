"""Every user-facing template follows docs/voice.md."""

import re

from app.explain import templates as T

ACRONYMS = {"UTC", "PDF", "OCR"}
NEXT_STEP = re.compile(r"\b(try|ask|upload|name|add|wait|check)\b", re.I)
APOLOGY = re.compile(r"\b(sorry|apologi[sz]e|apologies)\b", re.I)
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
CONSTANTS = (
    "ANSWERED_ONE_DOC", "ANSWERED_MANY_DOCS", "FULL_CONTEXT_READ", "FULL_CONTEXT_READ_MANY",
    "UNSUPPORTED_ONE", "UNSUPPORTED_MANY", "NO_ANSWER", "NO_DOCUMENTS_TEXT", "NO_EVIDENCE_TEXT",
    "ERROR_TEXT", "QUOTA_REACHED", "RATE_LIMITED",
    "STALE_ANSWER",
)  # fmt: skip
MUST_HAVE_NEXT_STEP = (
    "NO_DOCUMENTS_TEXT", "NO_EVIDENCE_TEXT", "ERROR_TEXT",
    "QUOTA_REACHED", "RATE_LIMITED", "STALE_ANSWER", "NO_ANSWER", "reason:NO_EVIDENCE",
    "reason:SCOPE_EMPTY",
    "reason:COMPARE_SIDE_MISSING", "reason:TURN_ERROR", "reason:TURN_ABANDONED", "reason:STOP_REFUSAL",
    "reason:GENERATION_INVALID", "reason:NODE_TIMEOUT",
)  # fmt: skip


def _texts() -> dict[str, str]:
    out = {f"reason:{k}": v for k, v in T.REASON_TEMPLATES.items() if v}
    out.update({f"degraded:{k}": v for k, v in T.DEGRADED_TEMPLATES.items()})
    out.update({name: getattr(T, name) for name in CONSTANTS})
    return out


def test_templates_warm() -> None:
    texts = _texts()
    assert len(texts) > 40
    for name, t in texts.items():
        assert "!" not in t, name
        assert "Error:" not in t and not re.search(r"\binvalid\b", t, re.I), name
        assert len(APOLOGY.findall(t)) <= 1, name
        shouting = [w for w in re.findall(r"\b[A-Z]{3,}\b", t) if w not in ACRONYMS]
        assert not shouting, (name, shouting)
        assert not EMOJI.search(t), name
    for name in MUST_HAVE_NEXT_STEP:
        assert NEXT_STEP.search(texts[name]), f"{name} has no next step"


def test_rendered_texts_are_warm() -> None:
    rendered = [
        T.QUOTA_REACHED.format(retry=60),
        T.RATE_LIMITED.format(retry=3),
        T.unsupported(3) or "",
    ]
    for t in rendered:
        assert "!" not in t and "{" not in t
