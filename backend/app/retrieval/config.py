"""Retrieval configuration loader. Strict: unknown keys are rejected at load."""

import pathlib
from functools import lru_cache
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.core.settings import get_settings
from app.retrieval import fusion

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[2] / "config"
ID_BOOST = 2.0  # the identifier weight hard-coded in fusion.py (see its docstring); config must agree


class RerankCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["none", "llm"]
    candidates: int = Field(ge=1, le=100)
    top_n: int = Field(ge=1, le=100)
    window_words: int = Field(default=180, ge=50, le=400)
    collapse_leaders: bool = False  # reranker copy only: dot-leader runs become one marker


class ExpansionCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    max_evidence_tokens: int = Field(ge=100)


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=r"^retrieval@[1-9][0-9]*(-[a-z0-9]+)?$")
    k_vec: int = Field(ge=1, le=500)
    k_lex: int = Field(ge=1, le=500)
    per_doc_quota: int | None = Field(ge=1)
    rrf_k: int = Field(ge=1)
    id_boost: float
    rerank: RerankCfg
    expansion: ExpansionCfg
    cache_ttl_s: int = Field(ge=0)
    single_doc_full_k: bool = False  # one-document scope: no per_doc_quota (it caps the list below k_vec)

    def model_post_init(self, _ctx: Any) -> None:
        if self.id_boost != ID_BOOST:
            raise ValueError(f"id_boost {self.id_boost} does not match the fusion weight {ID_BOOST}")
        if self.rrf_k != fusion.RRF_K:
            raise ValueError(f"rrf_k {self.rrf_k} does not match fusion.RRF_K {fusion.RRF_K}")


def vector_quota(cfg: RetrievalConfig, n_versions: int) -> int | None:
    """The per-document vector quota for a scope of `n_versions` documents (None = no quota)."""
    return None if cfg.single_doc_full_k and n_versions <= 1 else cfg.per_doc_quota


def load_config(path: pathlib.Path) -> RetrievalConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg = RetrievalConfig.model_validate(raw)
    if path.stem != cfg.version:
        raise ValueError(f"{path.name}: version {cfg.version!r} must match the file name")
    return cfg


@lru_cache(maxsize=8)
def _load_version(version: str) -> RetrievalConfig:
    return load_config(CONFIG_DIR / f"{version}.yaml")


def get_config(version: str | None = None) -> RetrievalConfig:
    """The named config, or the `retrieval_config` setting (default retrieval@5) when none is named."""
    return _load_version(version or get_settings().retrieval_config)
