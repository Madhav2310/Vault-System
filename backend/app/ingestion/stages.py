"""Stage registry. Stages are added as they are built (extract, quality, chunk, embed, index); a job whose
current stage has no implementation fails retryably with `stage_unavailable`."""

from app.ingestion.runner import Stage


def default_stages() -> dict[str, Stage]:
    return {}
