"""Result cache key: owner, normalized query, sorted scope versions, config version."""

import uuid

from app.retrieval.retrieve import result_cache_key

OWN = uuid.UUID(int=1)
V1, V2, V3 = (uuid.UUID(int=i) for i in (11, 12, 13))


def test_version_change_misses() -> None:
    base = result_cache_key(OWN, "payment terms", [V1, V2], "retrieval@1")
    assert result_cache_key(OWN, "payment terms", [V2, V1], "retrieval@1") == base  # order-insensitive
    assert result_cache_key(OWN, "  Payment   TERMS ", [V1, V2], "retrieval@1") == base  # normalized query
    assert result_cache_key(OWN, "payment terms", [V1, V3], "retrieval@1") != base  # a version changed
    assert result_cache_key(OWN, "payment terms", [V1], "retrieval@1") != base
    assert result_cache_key(uuid.UUID(int=2), "payment terms", [V1, V2], "retrieval@1") != base  # other owner


def test_config_version_misses() -> None:
    assert result_cache_key(OWN, "q", [V1], "retrieval@1") != result_cache_key(OWN, "q", [V1], "retrieval@2")


def test_embedding_model_misses() -> None:
    base = result_cache_key(OWN, "q", [V1], "retrieval@1", "text-embedding-3-small", ["chunker@1"])
    assert result_cache_key(OWN, "q", [V1], "retrieval@1", "text-embedding-3-small", ["chunker@1"]) == base
    assert result_cache_key(OWN, "q", [V1], "retrieval@1", "text-embedding-3-large", ["chunker@1"]) != base
    assert result_cache_key(OWN, "q", [V1], "retrieval@1", "text-embedding-3-small", ["chunker@2"]) != base
