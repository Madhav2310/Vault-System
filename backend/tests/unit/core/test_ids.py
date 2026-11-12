import time
import uuid

from app.core.ids import uuid7, uuid7_time_ms


def test_uuid7_monotonic() -> None:
    ids = [uuid7() for _ in range(20_000)]  # spans many same-millisecond bursts
    assert all(u.version == 7 and u.variant == uuid.RFC_4122 for u in ids)
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert [str(u) for u in ids] == sorted(str(u) for u in ids)  # text order == time order
    now_ms = time.time_ns() // 1_000_000
    assert abs(uuid7_time_ms(ids[-1]) - now_ms) < 5_000
