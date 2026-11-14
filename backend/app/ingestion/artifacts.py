"""Object-store keys for per-version stage artifacts.

Replaced on re-run; purged with the version."""

import uuid


def extracted_key(version_id: uuid.UUID) -> str:
    return f"extracted/{version_id}.json.gz"
