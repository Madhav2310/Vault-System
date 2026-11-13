from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import Config, create_app
from models import ALLOWLIST, PERMISSIVE_LICENSES, ModelNotAllowedError, resolve_model


def test_allowlist_enforced() -> None:
    with pytest.raises(ModelNotAllowedError):
        resolve_model("jinaai/jina-reranker-v2-base-multilingual")  # CC-BY-NC
    with pytest.raises(ModelNotAllowedError):
        create_app(Config(model="not/a-model"))
    # Startup path: an unknown model never reaches the server loop.
    with pytest.raises(ModelNotAllowedError), TestClient(create_app(Config(model="x/y"))):
        pass
    assert resolve_model("minilm").hf_id == "Xenova/ms-marco-MiniLM-L-6-v2"
    assert resolve_model("BAAI/bge-reranker-base").alias == "bge"


def test_licenses_permissive() -> None:
    assert set(ALLOWLIST) == {"Xenova/ms-marco-MiniLM-L-6-v2", "BAAI/bge-reranker-base"}
    for spec in ALLOWLIST.values():
        assert spec.license in PERMISSIVE_LICENSES, spec.hf_id
        assert not spec.non_commercial, spec.hf_id
        assert spec.license_source.startswith("https://"), spec.hf_id
