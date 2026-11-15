"""Every shipped retrieval version loads; retrieval@5 is the default; rerank.kind is a closed set."""

import pathlib

import pytest
import yaml
from pydantic import ValidationError

from app.retrieval.config import CONFIG_DIR, get_config, load_config


def test_shipped_versions_load(tmp_path: pathlib.Path) -> None:
    assert sorted(p.stem for p in CONFIG_DIR.glob("retrieval@*.yaml")) == ["retrieval@2", "retrieval@5"]
    v2 = get_config("retrieval@2")
    assert (v2.version, v2.rerank.candidates, v2.rerank.kind) == ("retrieval@2", 20, "none")
    assert get_config().version == "retrieval@5"  # the default
    assert get_config("retrieval@5").rerank.kind == "llm"
    raw = yaml.safe_load((CONFIG_DIR / "retrieval@2.yaml").read_text())
    for kind in ("bogus", "cross_encoder"):
        raw["rerank"]["kind"] = kind
        bad = tmp_path / "retrieval@2.yaml"
        bad.write_text(yaml.safe_dump(raw))
        with pytest.raises(ValidationError):
            load_config(bad)
