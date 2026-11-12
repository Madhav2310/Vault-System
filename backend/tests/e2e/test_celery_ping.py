"""Needs `make up` (redis + all three workers). Run: make test-e2e."""

import pytest

from app.worker.celery_app import QUEUES
from app.worker.tasks.ping import ping


@pytest.mark.parametrize("queue", QUEUES)
def test_ping_each_queue(queue: str) -> None:
    result = ping.apply_async(args=[f"pong-{queue}"], queue=queue)
    assert result.get(timeout=20) == f"pong-{queue}"
