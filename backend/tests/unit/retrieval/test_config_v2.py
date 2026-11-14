"""S17.06 retrieval@2 is the default; every shipped version loads; rerank.kind is a closed set."""

import pathlib

import pytest
import yaml
from pydantic import ValidationError

from app.retrieval.config import CONFIG_DIR, get_config, load_config


# retrieval@2 is kind=none: the S17.02 rule dropped the cross-encoder (p50 495 ms > 400 ms)
def test_both_versions_load(tmp_path: pathlib.Path) -> None:
    v1, v2 = get_config("retrieval@1"), get_config("retrieval@2")
    assert (v1.version, v1.rerank.candidates) == ("retrieval@1", 40)
    assert (v2.version, v2.rerank.candidates, v2.rerank.kind) == (
        "retrieval@2",
        20,
        "none",
    )
    assert get_config().version == "retrieval@5"  # the default
    assert get_config("retrieval@2-norerank").rerank.kind == "none"
    assert get_config("retrieval@2-llm").rerank.kind == "llm"
    raw = yaml.safe_load((CONFIG_DIR / "retrieval@2.yaml").read_text())
    raw["rerank"]["kind"] = "bogus"
    bad = tmp_path / "retrieval@2.yaml"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError):
        load_config(bad)
