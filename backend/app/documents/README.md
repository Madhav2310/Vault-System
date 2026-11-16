# app.documents

Document and version lifecycle outside the ingestion pipeline.

- `upload.py`: streaming multipart upload
- `sniff.py`: magic-byte type detection; the client's extension and Content-Type are never trusted
- `blobs.py`: content-addressed blob storage on top of `app.storage`
- `service.py`: document and version creation with per-owner dedupe
- `delete.py`: soft delete and purge

Invariants: functions that read or write a user's data take `owner_id` (purge looks it up from the soft-deleted document); uploads and purge are idempotent.
