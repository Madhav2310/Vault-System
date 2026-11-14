"""BRIEF_ENABLED=false skips the brief stage without touching the gateway, the database or the job."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from app.core.settings import Settings
from app.ingestion.runner import StageContext
from app.insights.stage import BriefStage


def _ctx(enabled: bool) -> StageContext:
    services = {"gateway": object(), "settings": SimpleNamespace(brief_enabled=enabled)}
    return StageContext(job=cast(Any, None), sm=cast(Any, None), services=services)


def test_brief_disabled_skips() -> None:
    res = asyncio.run(BriefStage().run(_ctx(False)))
    assert res.detail == {"skipped": "disabled"} and res.write is None and res.terminal_status is None


def test_brief_enabled_by_default() -> None:
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    assert s.brief_enabled is True
