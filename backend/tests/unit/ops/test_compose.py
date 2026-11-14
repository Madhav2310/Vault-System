"""Every compose service is resource-limited; the default profile stays small."""

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[4]
SERVICES = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def _mb(v: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([mg])", str(v).lower())
    assert m, v
    return float(m.group(1)) * (1024 if m.group(2) == "g" else 1)


def test_every_service_limited() -> None:
    for name, svc in SERVICES.items():
        assert "mem_limit" in svc and "cpus" in svc, f"{name} lacks mem_limit/cpus"


def test_default_profile_budget() -> None:
    default = {n: s for n, s in SERVICES.items() if not s.get("profiles")}
    assert "reranker" not in default and "otel-lgtm" not in default
    assert {"postgres", "redis", "api", "worker-ingest", "worker-ai", "beat"} <= set(default)
    total_gb = sum(_mb(s["mem_limit"]) for s in default.values()) / 1024
    assert total_gb <= 4.6, total_gb
