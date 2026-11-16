"""Retrieval config: strict schema, agreement with the code's constants."""

import pathlib

import pytest
import yaml
from pydantic import ValidationError

from app.retrieval.config import CONFIG_DIR, get_config, load_config


# retrieval@2 is the smallest valid file; it is used only as a template
def _raw() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load((CONFIG_DIR / "retrieval@2.yaml").read_text())


def _write(tmp: pathlib.Path, raw: dict, name: str = "retrieval@2.yaml") -> pathlib.Path:  # type: ignore[type-arg]
    p = tmp / name
    p.write_text(yaml.safe_dump(raw))
    return p


def test_schema_strict(tmp_path: pathlib.Path) -> None:
    cfg = get_config()
    assert (
        cfg.version == "retrieval@5" and cfg.k_vec == 40 and cfg.rerank.top_n == 8 and cfg.expansion.enabled
    )
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, {**_raw(), "surprise": 1}))  # unknown key
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, {**_raw(), "rerank": {**_raw()["rerank"], "extra": 1}}))
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, {k: v for k, v in _raw().items() if k != "k_vec"}))  # missing key
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, _raw(), "retrieval@3.yaml"))  # file name must match version
