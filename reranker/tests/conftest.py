from __future__ import annotations

import os
from pathlib import Path

CACHE = Path(__file__).resolve().parents[1] / ".model_cache"
os.environ.setdefault("FASTEMBED_CACHE_PATH", str(CACHE))
