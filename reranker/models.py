"""Reranker model allowlist with recorded licenses (design §4.3: no non-commercial models)."""

from __future__ import annotations

from dataclasses import dataclass


class ModelNotAllowedError(ValueError):
    """Raised at startup when the configured model is not in the allowlist."""


@dataclass(frozen=True)
class ModelSpec:
    hf_id: str
    alias: str
    license: str  # SPDX id as published by the model owner
    license_source: str  # URL where the license statement was read
    non_commercial: bool
    note: str = ""


ALLOWLIST: dict[str, ModelSpec] = {
    s.hf_id: s
    for s in (
        ModelSpec(
            hf_id="Xenova/ms-marco-MiniLM-L-6-v2",
            alias="minilm",
            license="Apache-2.0",
            # The Xenova ONNX repo card carries no license field; it is a re-export of the upstream
            # model, whose card declares apache-2.0. fastembed's registry also lists apache-2.0.
            license_source="https://huggingface.co/cross-encoder/ms-marco-MiniLM-L-6-v2",
            non_commercial=False,
            note=(
                "Weights Apache-2.0 per upstream card (Xenova re-export has no license field). "
                "Caveat: trained on MS MARCO, whose dataset terms say 'non-commercial research "
                "purposes only' (https://microsoft.github.io/msmarco/); whether that reaches the "
                "weights is a legal question, not resolved here."
            ),
        ),
        ModelSpec(
            hf_id="BAAI/bge-reranker-base",
            alias="bge",
            license="MIT",
            license_source="https://huggingface.co/BAAI/bge-reranker-base",
            non_commercial=False,
            note="Card: 'FlagEmbedding is licensed under the MIT License. The released models can "
            "be used for commercial purposes free of charge.'",
        ),
    )
}
ALIASES: dict[str, str] = {s.alias: s.hf_id for s in ALLOWLIST.values()}
DEFAULT_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
PERMISSIVE_LICENSES = frozenset({"Apache-2.0", "MIT", "BSD-3-Clause"})


def resolve_model(name: str) -> ModelSpec:
    """Return the allowlisted spec for a model id or alias; raise otherwise."""
    spec = ALLOWLIST.get(ALIASES.get(name, name))
    if spec is None:
        raise ModelNotAllowedError(
            f"model {name!r} is not in the reranker allowlist {sorted(ALLOWLIST)}"
        )
    return spec
