"""Helpers for judge tests (FakeProvider only, no network)."""

import json
from typing import Any


def request_text(req: Any) -> str:
    """Everything a recorded ChatRequest sent to the model, as one string."""
    return req.system + "\n" + json.dumps(req.messages, ensure_ascii=False)
