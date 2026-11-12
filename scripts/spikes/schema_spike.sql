-- S0.20 spike: hash-partitioned chunks with halfvec(1536) and btree_gin composite GIN.
-- Run in a scratch DB:  scripts/spikes/run_schema_spike.sh
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gin;
DROP TABLE IF EXISTS chunks;
CREATE TABLE chunks (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL,
  version_id uuid NOT NULL,
  ordinal int NOT NULL,
  embedding_text text NOT NULL,
  embedding halfvec(1536),
  tsv_en tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, embedding_text)) STORED,
  PRIMARY KEY (owner_id, id),
  UNIQUE (owner_id, version_id, ordinal)
) PARTITION BY HASH (owner_id);
SELECT format('CREATE TABLE chunks_p%s PARTITION OF chunks FOR VALUES WITH (MODULUS 32, REMAINDER %s)',
              lpad(i::text, 2, '0'), i)
FROM generate_series(0, 31) i \gexec
CREATE INDEX ix_chunks_owner_tsv_en ON chunks USING gin (owner_id, tsv_en);

-- 50k rows: 100 owners x 500 chunks; random unit-ish vectors; a few marker words for lexical queries.
CREATE TEMP TABLE owners AS SELECT gen_random_uuid() AS owner_id, n FROM generate_series(1, 100) n;
INSERT INTO chunks (owner_id, version_id, ordinal, embedding_text, embedding)
SELECT o.owner_id, o.owner_id, g,
       'clause ' || g || CASE WHEN g % 50 = 0 THEN ' indemnification termination' ELSE ' governing law' END,
       (SELECT array_agg(random() - 0.5) FROM generate_series(1, 1536) WHERE g > 0)::halfvec(1536)
FROM owners o CROSS JOIN generate_series(1, 500) g;
ANALYZE chunks;
SELECT count(*) AS rows, count(DISTINCT owner_id) AS owners FROM chunks;
