"""Helpers shared by chat nodes: neutralizing delimiter tags inside untrusted text."""

import re

_TAGS = re.compile(r"</?\s*(evidence|question|history|coverage|conflicts|route|inputs)\s*>", re.IGNORECASE)


def defuse(text: str) -> str:
    """Untrusted text (documents, history, the question) is placed inside delimiter tags. A literal closing
    tag in that text could end the block early and let the rest read as instructions, so tag-like strings
    are replaced. This changes only what the model is shown: stored and cited text are never altered."""
    return _TAGS.sub("[tag removed]", text)
