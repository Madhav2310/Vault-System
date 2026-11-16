"""UUIDv7 (RFC 9562 §5.7): 48-bit Unix ms timestamp, then a 12-bit counter (rand_a) for monotonicity
within one millisecond (method 1, §6.2), then 62 random bits. Time-ordered → B-tree friendly inserts."""

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0


def uuid7() -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms > _last_ms:
            # start below 0xFFF so a burst within one ms has counter space
            _last_ms, _counter = ms, int.from_bytes(os.urandom(2)) & 0x7FF
        else:  # same ms or clock went backwards: keep the last timestamp, bump the counter
            _counter += 1
            if _counter > 0xFFF:
                _last_ms, _counter = _last_ms + 1, 0
        ms, counter = _last_ms, _counter
    rand_b = int.from_bytes(os.urandom(8)) & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80 | 0x7 << 76 | counter << 64 | 0b10 << 62 | rand_b
    return uuid.UUID(int=value)


def uuid7_time_ms(u: uuid.UUID) -> int:
    return u.int >> 80
