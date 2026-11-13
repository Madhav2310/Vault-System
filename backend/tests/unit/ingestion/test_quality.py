import pathlib

import pytest

from app.ingestion.extract.pdf import PdfExtractor
from app.ingestion.quality import assess

QASPER = pathlib.Path(__file__).resolve().parents[4] / "eval" / "sources" / "qasper_pdfs" / "1910.03814.pdf"


@pytest.mark.skipif(not QASPER.exists(), reason="eval sources not fetched (make eval-fetch)")
def test_multi_column_flag() -> None:
    rows, summary, needs_ocr = assess(PdfExtractor(page_numbers=[1, 3, 4]).extract(str(QASPER)))
    assert not needs_ocr and summary["pages"] == 3
    assert sum("multi_column" in r.flags for r in rows) >= 2  # a two-column paper
