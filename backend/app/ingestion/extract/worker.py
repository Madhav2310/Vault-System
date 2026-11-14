"""Extraction subprocess (parser wall-clock timeout and memory limit).

    python -m app.ingestion.extract.worker <input> <mime> <output.json.gz>

Runs one extractor in a separate process so the stage can kill it on timeout and cap its memory (RLIMIT_AS on
Linux). Exit 0: output written. Exit 3: a StageError, described as JSON on stdout. Other exit codes: crash.
Parsing never happens inside the Celery worker process itself.
"""

import json
import os
import sys

from app.ingestion.errors import StageError
from app.ingestion.extract.base import Extractor


def extractor_for(mime: str) -> Extractor:
    from app.ingestion.extract.docx import DocxExtractor
    from app.ingestion.extract.pdf import PdfExtractor
    from app.ingestion.extract.text import TextExtractor

    extractors: dict[str, Extractor] = {
        "application/pdf": PdfExtractor(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DocxExtractor(),
        "text/plain": TextExtractor(markdown=False),
        "text/markdown": TextExtractor(markdown=True),
    }
    return extractors[mime]


def _limit_memory() -> None:
    mb = int(os.environ.get("VAULT_PARSE_MEMORY_MB", "0"))
    if mb and sys.platform.startswith("linux"):
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (mb * 1024 * 1024, mb * 1024 * 1024))


def main(argv: list[str]) -> int:
    src, mime, out = argv
    _limit_memory()
    try:
        doc = extractor_for(mime).extract(src)
        doc.check_offsets()
    except StageError as e:
        print(
            json.dumps(
                {"code": e.code, "detail": e.detail, "permanent": e.permanent, "status": e.version_status}
            )
        )
        return 3
    except MemoryError:
        print(
            json.dumps(
                {"code": "parse_memory", "detail": "memory limit", "permanent": True, "status": "failed"}
            )
        )
        return 3
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        f.write(doc.to_json_gz())
    os.replace(tmp, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
