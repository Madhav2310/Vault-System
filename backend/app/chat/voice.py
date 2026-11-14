"""Warm voice (S23.01): the `voice@1` prompt fragment appended to the answer prompts by code.

The fragment changes tone only. It is appended to the system prompt after the answer prompt's own body, so the
body, the citation rules and the strict schema are untouched. Off by default (`chat_voice`, env `CHAT_VOICE`)
until the paired A/B accepts it. The version is recorded with the run next to the prompt versions.
"""

from typing import Any

from app.llm.prompts import default_registry

VOICE: tuple[str, int] = ("voice", 1)
SEPARATOR = "\n\n"


def enabled(settings: Any) -> bool:
    return bool(getattr(settings, "chat_voice", False))


def fragment(settings: Any) -> str | None:
    """The text to append to the system prompt, or None when the voice is off."""
    if not enabled(settings):
        return None
    return SEPARATOR + default_registry().get(*VOICE).body


def generate_call(settings: Any) -> dict[str, Any]:
    """`generate` keyword arguments for the voice; empty (nothing changes) unless the switch is on."""
    frag = fragment(settings)
    return {"system_suffix": frag} if frag else {}


def recorded(settings: Any) -> list[str]:
    """Entries for the run's `config.prompts` list."""
    return [f"{VOICE[0]}@{VOICE[1]}"] if enabled(settings) else []
