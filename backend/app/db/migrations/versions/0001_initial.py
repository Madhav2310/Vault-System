"""Initial schema: every table.

Revision ID: 0001
Revises:
Create Date: 2026-11-12

Written as explicit SQL (not autogenerate): hash partitions, halfvec and generated tsvector columns
are not round-tripped by SQLAlchemy. `tests/integration/db/test_schema.py` checks the result and that
the ORM models match it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

CHUNK_PARTITIONS = 32
TS = "created_at timestamptz NOT NULL DEFAULT now()"

UPGRADE = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gin;

CREATE TABLE users (
  id uuid PRIMARY KEY,
  email text NOT NULL UNIQUE,
  password_hash text,
  api_key_hash text NOT NULL UNIQUE,
  is_admin boolean NOT NULL DEFAULT false,
  {TS}
);

CREATE TABLE idempotency_keys (
  owner_id uuid NOT NULL REFERENCES users(id),
  key text NOT NULL,
  request_hash text NOT NULL,
  response_status int,
  response_body jsonb,
  {TS},
  PRIMARY KEY (owner_id, key)
);
CREATE INDEX ix_idempotency_keys_created_at ON idempotency_keys (created_at);

CREATE TABLE blobs (
  sha256 text PRIMARY KEY,
  size_bytes bigint NOT NULL,
  mime text NOT NULL,
  storage_key text NOT NULL,
  refcount int NOT NULL DEFAULT 0,
  {TS}
);

CREATE TABLE documents (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  title text NOT NULL,
  current_version_id uuid,
  category text,
  tags text[] NOT NULL DEFAULT '{{}}',
  deleted_at timestamptz,
  {TS},
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_documents_owner_deleted ON documents (owner_id, deleted_at);

CREATE TABLE document_versions (
  id uuid PRIMARY KEY,
  document_id uuid NOT NULL REFERENCES documents(id),
  owner_id uuid NOT NULL REFERENCES users(id),
  version_no int NOT NULL,
  filename text NOT NULL,
  mime text NOT NULL,
  size_bytes bigint NOT NULL,
  sha256 text NOT NULL REFERENCES blobs(sha256),
  page_count int,
  token_count int,
  status text NOT NULL,
  quality jsonb,
  error_code text,
  error_detail text,
  chunker_version text,
  embedding_model text,
  ready_at timestamptz,
  enriched_at timestamptz,
  superseded_at timestamptz,
  {TS},
  CONSTRAINT uq_document_versions_document_version UNIQUE (document_id, version_no)
);
CREATE INDEX ix_document_versions_owner_sha ON document_versions (owner_id, sha256);
CREATE INDEX ix_document_versions_owner_status ON document_versions (owner_id, status);
ALTER TABLE documents ADD CONSTRAINT fk_documents_current_version
  FOREIGN KEY (current_version_id) REFERENCES document_versions(id);

CREATE TABLE pages (
  version_id uuid NOT NULL REFERENCES document_versions(id),
  page_no int NOT NULL,
  width real NOT NULL,
  height real NOT NULL,
  text_chars int NOT NULL,
  quality_score real,
  flags text[] NOT NULL DEFAULT '{{}}',
  {TS},
  PRIMARY KEY (version_id, page_no)
);

CREATE TABLE processing_jobs (
  id uuid PRIMARY KEY,
  version_id uuid NOT NULL UNIQUE REFERENCES document_versions(id),
  owner_id uuid NOT NULL REFERENCES users(id),
  current_stage text NOT NULL,
  status text NOT NULL,
  attempt_count int NOT NULL DEFAULT 0,
  max_attempts int NOT NULL DEFAULT 5,
  next_attempt_at timestamptz,
  lease_owner text,
  lease_expires_at timestamptz,
  last_dispatched_at timestamptz,
  error_code text,
  error_detail text,
  {TS},
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_processing_jobs_status_next ON processing_jobs (status, next_attempt_at);
CREATE INDEX ix_processing_jobs_status_lease ON processing_jobs (status, lease_expires_at);
CREATE INDEX ix_processing_jobs_owner_status ON processing_jobs (owner_id, status);

CREATE TABLE job_events (
  id uuid PRIMARY KEY,
  job_id uuid NOT NULL REFERENCES processing_jobs(id),
  stage text NOT NULL,
  status text NOT NULL,
  attempt int NOT NULL,
  duration_ms int,
  detail jsonb,
  {TS}
);
CREATE INDEX ix_job_events_job ON job_events (job_id, created_at);
CREATE INDEX ix_job_events_created_at ON job_events (created_at);

CREATE TABLE chunks (
  id uuid NOT NULL,
  owner_id uuid NOT NULL,
  document_id uuid NOT NULL REFERENCES documents(id),
  version_id uuid NOT NULL REFERENCES document_versions(id),
  ordinal int NOT NULL,
  kind text NOT NULL,
  original_text text NOT NULL,
  structural_prefix text,
  contextual_prefix text,
  embedding_text text NOT NULL,
  page_start int NOT NULL,
  page_end int NOT NULL,
  section_path text[] NOT NULL DEFAULT '{{}}',
  sentences jsonb NOT NULL,
  spans jsonb NOT NULL,
  token_count int NOT NULL,
  content_hash text NOT NULL,
  embedding halfvec(1536),
  embedding_model text,
  tsv_en tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, embedding_text)) STORED,
  tsv_ids tsvector,
  {TS},
  PRIMARY KEY (owner_id, id),
  CONSTRAINT uq_chunks_owner_version_ordinal UNIQUE (owner_id, version_id, ordinal)
) PARTITION BY HASH (owner_id);
"""

UPGRADE += "".join(
    f"CREATE TABLE chunks_p{i:02d} PARTITION OF chunks "
    f"FOR VALUES WITH (MODULUS {CHUNK_PARTITIONS}, REMAINDER {i});\n"
    for i in range(CHUNK_PARTITIONS)
)

UPGRADE += f"""
CREATE INDEX ix_chunks_owner_tsv_en ON chunks USING gin (owner_id, tsv_en);
CREATE INDEX ix_chunks_owner_tsv_ids ON chunks USING gin (owner_id, tsv_ids);

CREATE TABLE llm_cache (
  key text PRIMARY KEY,
  output jsonb NOT NULL,
  model text NOT NULL,
  prompt_id text NOT NULL,
  prompt_version int NOT NULL,
  {TS}
);

CREATE TABLE document_insights (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  version_id uuid NOT NULL REFERENCES document_versions(id),
  kind text NOT NULL,
  params_hash text NOT NULL,
  params jsonb NOT NULL DEFAULT '{{}}',
  content jsonb,
  status text NOT NULL,
  model text,
  prompt_version int NOT NULL,
  {TS},
  CONSTRAINT uq_document_insights_key UNIQUE (version_id, kind, params_hash, prompt_version)
);
CREATE INDEX ix_document_insights_owner ON document_insights (owner_id, version_id);

CREATE TABLE chat_sessions (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  title text,
  scope_document_ids uuid[],
  rolling_summary text,
  resolved_entities jsonb NOT NULL DEFAULT '{{}}',
  summary_upto_message_id uuid,
  active_run_id uuid,
  run_lease_expires_at timestamptz,
  {TS},
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_chat_sessions_owner ON chat_sessions (owner_id, updated_at);

CREATE TABLE chat_messages (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  session_id uuid NOT NULL REFERENCES chat_sessions(id),
  role text NOT NULL,
  client_message_id text,
  content text NOT NULL,
  response_type text,
  coverage_state text,
  run_id uuid,
  followups jsonb,
  stale_since timestamptz,
  {TS},
  CONSTRAINT uq_chat_messages_session_client_id UNIQUE (session_id, client_message_id)
);
CREATE INDEX ix_chat_messages_session_created ON chat_messages (session_id, created_at);

CREATE TABLE rag_runs (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  session_id uuid REFERENCES chat_sessions(id),
  user_message_id uuid,
  assistant_message_id uuid,
  matrix_cell_id uuid,
  replay_of_run_id uuid REFERENCES rag_runs(id),
  trace_id text,
  route text NOT NULL,
  mode text NOT NULL,
  original_query text NOT NULL,
  standalone_query text,
  analysis jsonb,
  coverage jsonb,
  conflicts jsonb,
  status text NOT NULL,
  reason_codes text[] NOT NULL DEFAULT '{{}}',
  degraded text[] NOT NULL DEFAULT '{{}}',
  limits jsonb,
  config jsonb,
  llm_calls_used int NOT NULL DEFAULT 0,
  input_tokens int NOT NULL DEFAULT 0,
  output_tokens int NOT NULL DEFAULT 0,
  cache_read_tokens int NOT NULL DEFAULT 0,
  cost_usd numeric(10,6) NOT NULL DEFAULT 0,
  latency_ms int,
  ttft_ms int,
  {TS},
  finished_at timestamptz
);
CREATE INDEX ix_rag_runs_owner_created ON rag_runs (owner_id, created_at);
CREATE INDEX ix_rag_runs_session ON rag_runs (session_id);

CREATE TABLE run_versions (
  run_id uuid NOT NULL REFERENCES rag_runs(id),
  version_id uuid NOT NULL REFERENCES document_versions(id),
  {TS},
  PRIMARY KEY (run_id, version_id)
);
CREATE INDEX ix_run_versions_version ON run_versions (version_id);

CREATE TABLE run_steps (
  id uuid PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES rag_runs(id),
  seq int NOT NULL,
  node text NOT NULL,
  status text NOT NULL,
  started_at timestamptz NOT NULL,
  duration_ms int,
  decision jsonb,
  reason_code text,
  error_code text,
  {TS},
  CONSTRAINT uq_run_steps_run_seq UNIQUE (run_id, seq)
);

CREATE TABLE retrieval_candidates (
  run_id uuid NOT NULL REFERENCES rag_runs(id),
  attempt int NOT NULL,
  chunk_id uuid NOT NULL,
  vector_rank int,
  vector_distance real,
  lexical_rank int,
  lexical_score real,
  id_match boolean NOT NULL DEFAULT false,
  rrf_score real,
  rerank_score real,
  selected boolean NOT NULL DEFAULT false,
  expanded_from uuid,
  result_index int,
  {TS},
  PRIMARY KEY (run_id, attempt, chunk_id)
);

CREATE TABLE answer_segments (
  id uuid PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES rag_runs(id),
  message_id uuid REFERENCES chat_messages(id),
  ordinal int NOT NULL,
  text text NOT NULL,
  support text,
  {TS},
  CONSTRAINT uq_answer_segments_run_ordinal UNIQUE (run_id, ordinal)
);

CREATE TABLE segment_evidence (
  segment_id uuid NOT NULL REFERENCES answer_segments(id),
  chunk_id uuid NOT NULL,
  sentence_start int NOT NULL,
  sentence_end int NOT NULL,
  cited_text text NOT NULL,
  {TS},
  PRIMARY KEY (segment_id, chunk_id, sentence_start),
  CONSTRAINT ck_segment_evidence_half_open CHECK (sentence_start < sentence_end)
);

CREATE TABLE llm_calls (
  id uuid PRIMARY KEY,
  owner_id uuid REFERENCES users(id),
  run_id uuid REFERENCES rag_runs(id),
  job_id uuid REFERENCES processing_jobs(id),
  purpose text NOT NULL,
  lane text NOT NULL,
  provider text NOT NULL,
  model text NOT NULL,
  prompt_id text,
  prompt_version int,
  attempt int NOT NULL,
  input_tokens int NOT NULL DEFAULT 0,
  output_tokens int NOT NULL DEFAULT 0,
  cache_read_tokens int NOT NULL DEFAULT 0,
  cache_write_tokens int NOT NULL DEFAULT 0,
  cost_usd numeric(10,6) NOT NULL DEFAULT 0,
  latency_ms int,
  ttft_ms int,
  status text NOT NULL,
  error_type text,
  {TS}
);
CREATE INDEX ix_llm_calls_created_at ON llm_calls (created_at);
CREATE INDEX ix_llm_calls_owner_created ON llm_calls (owner_id, created_at);
CREATE INDEX ix_llm_calls_run ON llm_calls (run_id);

CREATE TABLE matrices (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  title text NOT NULL,
  document_ids uuid[] NOT NULL,
  {TS}
);
CREATE INDEX ix_matrices_owner ON matrices (owner_id, created_at);

CREATE TABLE matrix_columns (
  id uuid PRIMARY KEY,
  matrix_id uuid NOT NULL REFERENCES matrices(id),
  ordinal int NOT NULL,
  question text NOT NULL,
  column_version int NOT NULL DEFAULT 1,
  source text NOT NULL,
  {TS}
);

CREATE TABLE matrix_cells (
  id uuid PRIMARY KEY,
  matrix_id uuid NOT NULL REFERENCES matrices(id),
  column_id uuid NOT NULL REFERENCES matrix_columns(id),
  column_version int NOT NULL,
  version_id uuid NOT NULL REFERENCES document_versions(id),
  run_id uuid REFERENCES rag_runs(id),
  status text NOT NULL,
  coverage_state text,
  {TS},
  CONSTRAINT uq_matrix_cells_key UNIQUE (matrix_id, column_id, column_version, version_id)
);

CREATE TABLE feedback (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  run_id uuid NOT NULL REFERENCES rag_runs(id),
  segment_id uuid REFERENCES answer_segments(id),
  chunk_id uuid,
  kind text NOT NULL,
  note text,
  eval_status text NOT NULL DEFAULT 'new'
    CONSTRAINT ck_feedback_eval_status CHECK (eval_status IN ('new','exported','promoted','dismissed')),
  {TS},
  CONSTRAINT uq_feedback_key UNIQUE NULLS NOT DISTINCT (owner_id, run_id, segment_id, chunk_id, kind)
);

CREATE TABLE eval_runs (
  id uuid PRIMARY KEY,
  git_sha text NOT NULL,
  config jsonb NOT NULL DEFAULT '{{}}',
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  {TS}
);

CREATE TABLE eval_results (
  eval_run_id uuid NOT NULL REFERENCES eval_runs(id),
  question_id text NOT NULL,
  metric text NOT NULL,
  value double precision NOT NULL,
  {TS},
  PRIMARY KEY (eval_run_id, question_id, metric)
);
"""

TABLES_REVERSE = [
    "eval_results", "eval_runs", "feedback", "matrix_cells", "matrix_columns", "matrices",
    "llm_calls", "segment_evidence", "answer_segments", "retrieval_candidates", "run_steps",
    "run_versions", "rag_runs", "chat_messages", "chat_sessions", "document_insights", "llm_cache",
    "chunks", "job_events", "processing_jobs", "pages",
]  # fmt: skip


def upgrade() -> None:
    # asyncpg prepares each statement, so multi-statement strings must be split.
    for stmt in (s.strip() for s in UPGRADE.split(";\n")):
        if stmt:
            op.execute(stmt)


def downgrade() -> None:
    for t in TABLES_REVERSE:
        op.execute(f"DROP TABLE {t}")
    op.execute("ALTER TABLE documents DROP CONSTRAINT fk_documents_current_version")
    for t in ("document_versions", "documents", "blobs", "idempotency_keys", "users"):
        op.execute(f"DROP TABLE {t}")
    op.execute("DROP EXTENSION IF EXISTS btree_gin")
    op.execute("DROP EXTENSION IF EXISTS vector")
