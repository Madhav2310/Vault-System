# app.ingestion

Stage pipeline from stored file to searchable chunks.

- `stages.py`: stage implementations and registry; `runner.py`: stage runner; `errors.py`: stage error classes
- `jobs.py`: dispatch, CAS claim, lease, heartbeat on `processing_jobs`; `sweeper.py`: recovers lost dispatches, dead workers, due retries
- `status.py`: version status machine (every write goes through `set_version_status`); `events.py`: live processing status
- `chunk.py`, `sentences.py`: structure-aware chunker (`chunker@1`; sentences get handles later), boundary rules
- `identifiers.py`: identifier tokens for the exact-match search leg
- `embed_index.py`: embed and index stages; `quality.py`: page quality and flags; `artifacts.py`: object-store keys for stage artifacts

Invariants: only `chunks.original_text` is evidence; job state lives in `processing_jobs`, not Celery; every stage is idempotent.
