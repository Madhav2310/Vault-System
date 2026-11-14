# app.documents

Document and version lifecycle outside the ingestion pipeline (design §6.1, §6.7).

- `upload.py`: streaming multipart upload
- `sniff.py`: magic-byte type detection; the client's extension and Content-Type are never trusted
- `blobs.py`: content-addressed blob storage on top of `app.storage`
- `service.py`: document and version creation with per-owner dedupe
- `delete.py`: soft delete and purge

Invariants: all functions take `owner_id`; uploads and purge are idempotent.
