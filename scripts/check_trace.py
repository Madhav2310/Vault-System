"""Send a request carrying a known trace id to the API, then find that trace in Tempo.

Run: cd backend && uv run --frozen python ../scripts/check_trace.py   (stack must be up: make up)
"""

import os
import secrets
import sys
import time

import httpx

API = os.environ.get("API_BASE_URL", f"http://localhost:{os.environ.get('API_PORT', '8100')}")
TEMPO = os.environ.get("TEMPO_URL", f"http://localhost:{os.environ.get('TEMPO_PORT', '3200')}")

trace_id = secrets.token_hex(16)
traceparent = f"00-{trace_id}-{secrets.token_hex(8)}-01"
# /healthz is excluded from tracing by design; use a real route (404s are traced too).
r = httpx.get(f"{API}/v1/_trace_probe", headers={"traceparent": traceparent}, timeout=10)
print(f"sent trace_id={trace_id} status={r.status_code}")

deadline = time.time() + 60
while time.time() < deadline:
    resp = httpx.get(f"{TEMPO}/api/traces/{trace_id}", timeout=10)
    if resp.status_code == 200 and "vault-api" in resp.text:
        print(f"found trace {trace_id} in Tempo (service vault-api)")
        sys.exit(0)
    time.sleep(2)
print(f"trace {trace_id} not found in Tempo within 60s (last status {resp.status_code})", file=sys.stderr)
sys.exit(1)
