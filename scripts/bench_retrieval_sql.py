"""Time the exact vector and lexical retrieval statements against the dev database, at $0.

Queries are the vault_eval_v2 questions (text only); query vectors are their cached embeddings in Redis (a
question without a cached vector is skipped, nothing is embedded). Each statement is the one the retriever
runs (`vector_statement`, `lexical_statement`) over the owner's whole live scope.

Run: cd backend && uv run --frozen env PYTHONPATH=.. python ../scripts/bench_retrieval_sql.py [options]
  --leg vector|lexical   one leg (default: both)
  --owner UUID           owner of the vector leg (default: the owner with the most live chunks)
  --lexical-owner UUID   owner of the lexical leg (default: --owner)
  --n 40                 queries per leg
  --cold                 restart the postgres container and drop the Docker VM page cache once (a privileged
                         container writes /proc/sys/vm/drop_caches), then time the first query of each leg
                         once; give the legs owners in different partitions so one leg cannot warm the other
  --lexical-equivalence  instead of timing: for every v2 question and prior turn and every owner with live
                         chunks, the lexical statement with the former per-lexeme `matched` subquery and the
                         current one return the same (chunk_id, score, id_match) list (all matches, no k cut),
                         and both counts agree on every row that matches the OR query; exits 1 on a difference

Warm: one unrecorded pass, then EXPLAIN (ANALYZE, BUFFERS) per query, then the plain statement timed in the
client. Printed: median and p95 execution ms, median shared buffers (hit + read), client median ms.
"""

import argparse
import asyncio
import hashlib
import json
import pathlib
import shutil
import statistics
import struct
import subprocess
import sys
import time
import uuid
from typing import Any

import redis
from eval.loader import load_dataset_v2
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.retrieval.lexical import MATCHED_LEXEMES, lexical_statement
from app.retrieval.query_embed import normalize_query
from app.retrieval.scope import compute_scope
from app.retrieval.vector import vector_statement

K = 40  # k_vec and k_lex of the retrieval configs
PER_DOC_QUOTA = 12
REPO = pathlib.Path(__file__).resolve().parents[1]
PG_IMAGE = "pgvector/pgvector:0.8.7-pg16-bookworm"
# the `matched` count before it became one expression per row: one tsquery parse and match per query lexeme
PER_LEXEME_MATCHED = "(SELECT count(*) FROM unnest(qq.lex) AS l WHERE c.tsv_en @@ quote_literal(l)::tsquery)"
ALL = 1_000_000


def make_cold() -> None:
    """Empty shared_buffers (restart) and the OS page cache under it (Docker Desktop runs one Linux VM)."""
    docker = shutil.which("docker") or sys.exit("docker not found")
    drop = "sync && echo 3 > /proc/sys/vm/drop_caches"
    for cmd in (  # constant argument lists, no user input
        [docker, "compose", "restart", "postgres"],
        [str(REPO / "scripts/wait_healthy.sh"), "120", "postgres"],
        [docker, "run", "--rm", "--privileged", "--entrypoint", "sh", PG_IMAGE, "-c", drop],
    ):
        subprocess.run(cmd, cwd=REPO, check=True)  # noqa: S603


def questions() -> list[str]:
    return sorted({i.question for i in load_dataset_v2().items})


def cached_vector(r: "redis.Redis[bytes]", question: str, model: str, owner: uuid.UUID) -> list[float] | None:
    norm = normalize_query(question)
    keys = [
        "qembed:v2:" + hashlib.sha256(f"{owner}\x00{model}\x00{norm}".encode()).hexdigest(),
        "qembed:v1:" + hashlib.sha256(f"{model}\x00{norm}".encode()).hexdigest(),
    ]
    for key in keys:
        blob = r.get(key)
        if isinstance(blob, bytes) and len(blob) == 2 * 1536:
            return list(struct.unpack(f"<{len(blob) // 2}e", blob))
    return None


async def explain(s: AsyncSession, sql: str, params: dict[str, Any]) -> tuple[float, int, int]:
    raw = (await s.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql), params)).scalar()
    plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]
    top = plan["Plan"]
    return float(plan["Execution Time"]), int(top["Shared Hit Blocks"]), int(top["Shared Read Blocks"])


async def default_owner(s: AsyncSession) -> uuid.UUID:
    row = (
        await s.execute(
            text(
                "SELECT c.owner_id FROM chunks c JOIN documents d ON d.id = c.document_id "
                "AND d.current_version_id = c.version_id AND d.deleted_at IS NULL "
                "GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1"
            )
        )
    ).scalar_one()
    return uuid.UUID(str(row))


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p * (len(xs) - 1)))]


async def run_leg(leg: str, owner: uuid.UUID, n: int, cold: bool) -> None:
    settings = get_settings()
    sm = get_sessionmaker()
    async with sm() as s:
        scope = await compute_scope(s, owner)
    r: redis.Redis[bytes] = redis.Redis.from_url(settings.redis_url)
    stmts: list[tuple[str, dict[str, Any]]] = []
    for q in questions():
        if len(stmts) == (1 if cold else n):
            break
        if leg == "vector":
            vec = cached_vector(r, q, settings.embedding_model, owner)
            if vec is not None:
                stmts.append(vector_statement(owner, scope.version_ids, vec, K, PER_DOC_QUOTA))
        else:
            stmts.append(lexical_statement(owner, scope.version_ids, q, K))
    if not stmts:
        sys.exit(f"no {leg} queries (cached query vectors missing?)")
    async with sm() as s:
        if not cold:
            for sql, params in stmts:  # warm-up pass, not recorded
                (await s.execute(text(sql), params)).all()
        ex = [await explain(s, sql, params) for sql, params in stmts]
        wall: list[float] = []
        if not cold:
            for sql, params in stmts:
                t0 = time.perf_counter()
                (await s.execute(text(sql), params)).all()
                wall.append((time.perf_counter() - t0) * 1000)
    ms = [e[0] for e in ex]
    bufs = [float(e[1] + e[2]) for e in ex]
    reads = [float(e[2]) for e in ex]
    label = "cold" if cold else "warm"
    print(
        f"{leg} {label} owner={owner} versions={len(scope.version_ids)} n={len(ex)} "
        f"exec_ms_median={statistics.median(ms):.1f} exec_ms_p95={pct(ms, 0.95):.1f} "
        f"buffers_median={statistics.median(bufs):.0f} read_median={statistics.median(reads):.0f}"
        + (f" client_ms_median={statistics.median(wall):.1f}" if wall else "")
    )


async def lexical_equivalence() -> None:
    texts = sorted({t for i in load_dataset_v2().items for t in (i.question, *i.prior_turns)})
    sm = get_sessionmaker()
    async with sm() as s:
        owners = [
            uuid.UUID(str(o))
            for o in (
                await s.execute(
                    text(
                        "SELECT DISTINCT c.owner_id FROM chunks c JOIN documents d ON d.id = c.document_id "
                        "AND d.current_version_id = c.version_id AND d.deleted_at IS NULL ORDER BY 1"
                    )
                )
            ).scalars()
        ]
    pairs = differing = rows_checked = hits = 0
    for owner in owners:
        async with sm() as s:
            scope = await compute_scope(s, owner)
            if not scope.version_ids:
                continue
            for q in texts:
                new_sql, params = lexical_statement(owner, scope.version_ids, q, ALL)
                if MATCHED_LEXEMES not in new_sql:  # quoted phrase: websearch statement, no lexeme count
                    continue
                old_sql = new_sql.replace(MATCHED_LEXEMES, PER_LEXEME_MATCHED)
                new = [tuple(r) for r in (await s.execute(text(new_sql), params)).all()]
                old = [tuple(r) for r in (await s.execute(text(old_sql), params)).all()]
                ctes = new_sql[: new_sql.index("SELECT c.id")]
                counts = (
                    await s.execute(
                        text(
                            f"{ctes}SELECT count(*), count(*) FILTER (WHERE "  # noqa: S608 (constants only)
                            f"{PER_LEXEME_MATCHED} IS DISTINCT FROM {MATCHED_LEXEMES}) FROM chunks c, qq "
                            "WHERE c.owner_id = :o AND c.version_id = ANY(CAST(:vs AS uuid[])) "
                            "AND qq.q IS NOT NULL AND c.tsv_en @@ qq.q"
                        ),
                        params,
                    )
                ).one()
                pairs += 1
                hits += len(new)
                rows_checked += counts[0]
                if old != new or counts[1]:
                    differing += 1
                    print(f"DIFF owner={owner} query_sha={hashlib.sha256(q.encode()).hexdigest()[:12]}")
    print(
        f"lexical equivalence owners={len(owners)} texts={len(texts)} pairs={pairs} hits={hits} "
        f"rows_counted={rows_checked} differing={differing}"
    )
    if differing:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", choices=["vector", "lexical"])
    ap.add_argument("--owner", type=uuid.UUID)
    ap.add_argument("--lexical-owner", type=uuid.UUID)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--cold", action="store_true")
    ap.add_argument("--lexical-equivalence", action="store_true")
    a = ap.parse_args()
    if a.lexical_equivalence:
        asyncio.run(lexical_equivalence())
        return

    async def go() -> None:
        async with get_sessionmaker()() as s:
            owner = a.owner or await default_owner(s)
        owners = {"vector": owner, "lexical": a.lexical_owner or owner}
        if a.cold:
            await get_engine().dispose()
            make_cold()
        for leg in [a.leg] if a.leg else ["vector", "lexical"]:
            await run_leg(leg, owners[leg], a.n, a.cold)

    asyncio.run(go())


if __name__ == "__main__":
    main()
