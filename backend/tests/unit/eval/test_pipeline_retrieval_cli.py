"""An unknown retrieval config exits with a message naming the missing file."""

import pytest
from eval.pipeline_retrieval import resolve_config

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path)


def test_unknown_config() -> None:
    with pytest.raises(SystemExit) as e:
        resolve_config("retrieval@999")
    assert "retrieval@999.yaml" in str(e.value)
    assert resolve_config("retrieval@1").version == "retrieval@1"
