"""S16.05: PDF extraction time/memory budget on the 121-page AMD 10-K, and the page cap."""

import pathlib
import subprocess
import sys

import pytest

from app.ingestion.errors import UnsupportedContentError
from app.ingestion.extract.pdf import PdfExtractor
from tests.support.uploads import FIXTURES

ROOT = pathlib.Path(__file__).resolve().parents[4]
AMD = ROOT / "eval/sources/fb_pdfs/AMD_2022_10K.pdf"
PROBE = (
    "import resource,sys,time;t=time.time()\n"
    "from app.ingestion.extract.pdf import PdfExtractor\n"
    "d=PdfExtractor().extract(sys.argv[1])\n"
    "r=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1e6 if sys.platform=='darwin' else 1e3)\n"
    "print(len(d.pages), round(time.time()-t,1), round(r))\n"
)


def _probe() -> tuple[int, float, float]:
    out = subprocess.run([sys.executable, "-c", PROBE, str(AMD)], capture_output=True, text=True, check=True)
    pages, secs, rss = out.stdout.split()[-3:]
    return int(pages), float(secs), float(rss)


@pytest.mark.skipif(not AMD.exists(), reason="corpus sources not fetched (make eval-fetch)")
def test_amd_peak_rss() -> None:
    pages, _secs, rss = _probe()
    assert pages == 121 and rss <= 450, f"peak RSS {rss} MB"


@pytest.mark.skipif(not AMD.exists(), reason="corpus sources not fetched (make eval-fetch)")
def test_amd_wall_time() -> None:
    _pages, secs, _rss = _probe()
    assert secs <= 25, f"{secs} s (run on an idle machine)"


def test_page_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_MAX_PAGES", "0")
    with pytest.raises(UnsupportedContentError) as e:
        PdfExtractor().extract(str(FIXTURES / "geometry" / "ligatures.pdf"))
    assert e.value.code == "too_many_pages"
