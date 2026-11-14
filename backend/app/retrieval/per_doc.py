"""Per-document evidence selection for multi-document scopes.

A global top-N lets one document crowd out another, yet the passage that answers a cross-document question
often ranks 3rd to 6th inside its own document. After the global top-N, add each scoped document's own best
`per_doc` chunks, taken from the fused ranking in rank order. Pure code: no I/O, deterministic.
"""

import uuid
from collections.abc import Sequence

from app.retrieval.fusion import FusedCandidate


def add_per_document(
    top_ids: Sequence[uuid.UUID], fused: Sequence[FusedCandidate], per_doc: int, n_versions: int
) -> list[uuid.UUID]:
    """`top_ids` first (unchanged order), then each version's best chunks until it has `per_doc` selected.

    A single-version scope, or `per_doc == 0`, returns `top_ids` unchanged.
    """
    out = list(top_ids)
    if per_doc <= 0 or n_versions <= 1:
        return out
    version_of = {c.chunk_id: c.version_id for c in fused}
    have: dict[uuid.UUID, int] = {}
    for cid in out:
        v = version_of.get(cid)
        if v is not None:
            have[v] = have.get(v, 0) + 1
    chosen = set(out)
    for c in fused:  # rank order
        if c.chunk_id in chosen or have.get(c.version_id, 0) >= per_doc:
            continue
        chosen.add(c.chunk_id)
        have[c.version_id] = have.get(c.version_id, 0) + 1
        out.append(c.chunk_id)
    return out
