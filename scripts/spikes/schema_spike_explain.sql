-- Scoped exact vector query + scoped lexical query for one owner (literal owner → plan-time pruning).
\set ON_ERROR_STOP on
SELECT owner_id AS target FROM chunks ORDER BY owner_id LIMIT 1 \gset
CREATE TEMP TABLE q AS SELECT embedding FROM chunks WHERE owner_id = :'target' ORDER BY ordinal LIMIT 1;
EXPLAIN (COSTS OFF)
SELECT id FROM chunks WHERE owner_id = :'target' ORDER BY embedding <=> (SELECT embedding FROM q) LIMIT 40;
-- At 1.5k rows/partition a seq scan is legitimately cheaper; disable it to prove the composite GIN
-- (owner_id, tsv_en) is applicable. Planner picks it unaided at larger partitions (see spike report).
SET enable_seqscan = off;
EXPLAIN (COSTS OFF)
SELECT id FROM chunks WHERE owner_id = :'target' AND tsv_en @@ plainto_tsquery('english', 'indemnification')
LIMIT 40;
RESET enable_seqscan;
EXPLAIN (ANALYZE, TIMING ON, COSTS OFF, SUMMARY ON)
SELECT id FROM chunks WHERE owner_id = :'target' ORDER BY embedding <=> (SELECT embedding FROM q) LIMIT 40;
