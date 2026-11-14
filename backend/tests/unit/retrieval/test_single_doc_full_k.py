"""A per-document vector quota is a cross-document balance. On a one-document scope it caps the vector list
at the quota (12 of k_vec 40), so `single_doc_full_k` lifts it there and nowhere else."""

from app.retrieval.config import get_config, vector_quota


def test_the_fusion_baseline_keeps_the_quota_on_single_document_scopes() -> None:
    cfg = get_config("retrieval@2")
    assert cfg.single_doc_full_k is False
    assert vector_quota(cfg, n_versions=1) == 12


def test_the_default_drops_the_quota_only_for_one_document() -> None:
    cfg = get_config("retrieval@5")
    assert cfg.single_doc_full_k is True
    assert vector_quota(cfg, n_versions=1) is None
    assert vector_quota(cfg, n_versions=2) == 12
