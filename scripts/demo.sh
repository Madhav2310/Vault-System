#!/usr/bin/env bash
# Full product demo: upload 3 documents -> live status -> source guides -> streamed lookup -> follow-up
# -> compare (multi-document question) -> meta question -> why panel -> new version and stale marker -> metrics -> spend.
# Everything is plain curl against the running stack (`make up`); a fresh throwaway user is created.
#
#   scripts/demo.sh                      # real key in .env (LLM_PROVIDER=openai); about $0.10 of usage
#   LLM_PROVIDER=fake scripts/demo.sh    # scripted model, no key, no spend (see below)
#
# Fake mode (no spend): start the UI-test stack, then point the script at it. The fake model only answers
# the questions in backend/tests/fixtures/ui_script.yaml, so the meta step is skipped:
#   export COMPOSE_PROJECT_NAME=vault-uitest COMPOSE_FILE=docker-compose.yml:docker-compose.ui-test.yml \
#          API_PORT=8190 POSTGRES_PORT=5542 REDIS_PORT=6489
#   make up && LLM_PROVIDER=fake scripts/demo.sh
#
# Env: API_BASE_URL (default http://localhost:$API_PORT, 8100), DEMO_API_KEY (skip user creation; no Docker
# needed), DEMO_DOCS (space-separated files to upload instead of the defaults), DEMO_STRICT=1 (exit 1 when
# spend is not below DEMO_MAX_USD, default 0.20). Needs: curl, jq, awk; docker unless DEMO_API_KEY is set.
set -euo pipefail
cd "$(dirname "$0")/.."

API="${API_BASE_URL:-http://localhost:${API_PORT:-8100}}"
MAX_USD="${DEMO_MAX_USD:-0.20}"
PROVIDER="${LLM_PROVIDER:-$(sed -n 's/^LLM_PROVIDER=//p' .env 2>/dev/null | tail -1)}"
PROVIDER="${PROVIDER:-openai}"
DOCS="${DEMO_DOCS:-frontend/e2e/fixtures/services-agreement.txt frontend/e2e/fixtures/pricing-memo.txt eval/datasets/vault_eval/docs/amendment_consulting.md}"
Q_LOOKUP="${DEMO_Q_LOOKUP:-What is the notice period for termination?}"
Q_FOLLOWUP="${DEMO_Q_FOLLOWUP:-And which law governs it?}"
Q_META="${DEMO_Q_META:-How many documents do I have, and which are ready?}"
if [ "$PROVIDER" = fake ]; then
  Q_COMPARE="${DEMO_Q_COMPARE:-What is the monthly fee?}" # scripted in ui_script.yaml: two sources disagree
else
  Q_COMPARE="${DEMO_Q_COMPARE:-Compare the monthly fee in the services agreement and the pricing memo. Do they conflict?}"
fi

for t in curl jq awk; do command -v "$t" >/dev/null || { echo "demo.sh needs $t" >&2; exit 1; }; done
N=0
step() { N=$((N + 1)); printf '\n== Step %d: %s\n' "$N" "$*"; }
RUN_TAG="$(date +%s)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/vault-demo.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

# One SSE frame (the JSON after `data: `) -> readable lines. Evidence text is copied by the server from the
# stored sentence, never written by the model.
RENDER='
  if .type == "status" then "  [" + .stage + "]"
  elif .type == "segment" then
    "  " + .text + (if .support == "cited" then "" else "   (no citation)" end)
    + ([.evidence[] | "\n      cites " + (.document_title // "?") + " v" + ((.version_no // 0) | tostring)
        + (if .pages then " p" + (.pages[0] | tostring) else "" end) + ": \"" + (.cited_text | .[0:100]) + "\""] | join(""))
  elif .type == "verification" then "  [verified] " + ([.verdicts[] | .verdict] | join(", "))
  elif .type == "followups" then "  suggested follow-ups: " + (.questions | join(" | "))
  elif .type == "done" then "  [done] " + .response_type + "  ttft=" + ((.ttft_ms // 0) | tostring) + "ms  total="
    + ((.latency_ms // 0) | tostring) + "ms  cost=$" + (.usage.cost_usd | tostring)
  elif .type == "error" then "  [error] " + .code
  else empty end'

SID=""
RUN_ID=""
TURN_COST=0
# ask <label> <question> <n>: streams one turn over SSE, prints it, sets RUN_ID and adds to TURN_COST.
ask() {
  local label="$1" q="$2" cmid="demo-$RUN_TAG-$3" out="$TMP/sse.$3" c
  echo "  you ($label): $q"
  curl -fsSN "${AUTH[@]}" -H 'Accept: text/event-stream' -H 'Content-Type: application/json' \
    -d "$(jq -n --arg q "$q" --arg id "$cmid" '{content:$q, client_message_id:$id}')" \
    "$API/v1/chat/sessions/$SID/messages" | tee "$out" | sed -un 's/^data: //p' \
    | while IFS= read -r line; do printf '%s\n' "$line" | jq -r "$RENDER"; done
  RUN_ID="$(sed -n 's/^data: //p' "$out" | jq -r 'select(.type=="run.started") | .run_id' | head -1)"
  c="$(sed -n 's/^data: //p' "$out" | jq -r 'select(.type=="done") | .usage.cost_usd' | head -1)"
  TURN_COST="$(awk -v a="$TURN_COST" -v b="${c:-0}" 'BEGIN { printf "%.6f", a + b }')"
  [ -n "$RUN_ID" ] || { echo "no run.started event: the turn did not stream" >&2; exit 1; }
}

step "create a demo user (provider: $PROVIDER, API: $API)"
if [ -n "${DEMO_API_KEY:-}" ]; then
  KEY="$DEMO_API_KEY"
else
  command -v docker >/dev/null || { echo "docker not found; set DEMO_API_KEY to use an existing key" >&2; exit 1; }
  EMAIL="demo-$RUN_TAG@vault.local"
  KEY=$(docker compose exec -T api python - "$EMAIL" <<'PY'
import asyncio, sys
from app.core.auth import hash_api_key, new_api_key
from app.db.models import User
from app.db.session import get_sessionmaker

async def main(email: str) -> None:
    key = new_api_key()
    async with get_sessionmaker()() as s:
        s.add(User(email=email, api_key_hash=hash_api_key(key)))
        await s.commit()
    print(key)

asyncio.run(main(sys.argv[1]))
PY
)
fi
AUTH=(-H "Authorization: Bearer $KEY")
echo "  user ready (key not printed)"

step "upload 3 documents"
DIDS=""
VIDS=""
for f in $DOCS; do
  [ -f "$f" ] || { echo "missing document: $f" >&2; exit 1; }
  UP=$(curl -fsS "${AUTH[@]}" -F "file=@$f" "$API/v1/documents")
  echo "  $(basename "$f"): $(echo "$UP" | jq -c '{document_id, version_id, status, deduplicated}')"
  DIDS="$DIDS $(echo "$UP" | jq -r .document_id)"
  VIDS="$VIDS $(echo "$UP" | jq -r .version_id)"
done
FIRST_DID="${DIDS# }"
FIRST_DID="${FIRST_DID%% *}"

step "live status until every document is enriched (stages: extract, quality, chunk, embed, index, brief)"
pending=1
for i in $(seq 1 90); do
  pending=0
  line=""
  for v in $VIDS; do
    p=$(curl -fsS "${AUTH[@]}" "$API/v1/versions/$v/processing")
    st=$(echo "$p" | jq -r .status)
    line="$line  ${st}($(echo "$p" | jq -r '.job.current_stage // "-"'))"
    case "$st" in
      enriched) ;;
      failed | unsupported | needs_ocr)
        echo "version $v ended in $st" >&2
        exit 1
        ;;
      *) pending=$((pending + 1)) ;;
    esac
  done
  echo "  t=$((i * 2))s$line"
  [ "$pending" -eq 0 ] && break
  sleep 2
done
[ "$pending" -eq 0 ] || echo "  timed out waiting for enrichment (chat already works on ready documents)" >&2

step "source guide for each document (cited brief: type, summary, facts, suggested questions)"
for d in $DIDS; do
  curl -fsS "${AUTH[@]}" "$API/v1/documents/$d/brief" | jq '{status, document_type, category, tags,
    summary: ((.summary // "") | .[0:220]), facts: (.facts | length),
    verified_facts: ([.facts[] | select(.status == "verified")] | length), suggested_questions}'
done

step "ask a lookup question (streamed over SSE)"
SID=$(curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' -d '{}' "$API/v1/chat/sessions" | jq -r .id)
echo "  session $SID (scope: all your documents)"
ask lookup "$Q_LOOKUP" 1
LOOKUP_RUN="$RUN_ID"

step "ask a follow-up (the session remembers what 'it' means)"
ask follow-up "$Q_FOLLOWUP" 2

step "compare across documents (a multi-document question; each source is cited)"
ask compare "$Q_COMPARE" 3

step "ask a meta question (answered from your library metadata, not from document text)"
if [ "$PROVIDER" = fake ] && [ -z "${DEMO_Q_META:-}" ]; then
  echo "  skipped: the scripted fake model has no meta answer (run with a real key to see it)"
else
  ask meta "$Q_META" 4
fi

step "why panel: how the lookup answer was produced (GET /v1/runs/{id}; template-rendered, never model-written)"
curl -fsS "${AUTH[@]}" "$API/v1/runs/$LOOKUP_RUN" \
  | jq '{run_id, status, mode, route, response_type, explanation, sources, steps: [.steps[] | "\(.node) \(.duration_ms)ms"], usage}'

step "upload a new version of the first document and show the stale marker"
FIRST_FILE="${DOCS%% *}"
NEW_VERSION="$TMP/new/$(basename "$FIRST_FILE")"
mkdir -p "$TMP/new"
{
  cat "$FIRST_FILE"
  printf '\nAmendment: either party may now terminate on 60 days written notice.\n'
} >"$NEW_VERSION"
UP2=$(curl -fsS "${AUTH[@]}" -F "file=@$NEW_VERSION" "$API/v1/documents/$FIRST_DID/versions")
echo "  version 2: $(echo "$UP2" | jq -c '{document_id, version_id, status, deduplicated}')"
for i in $(seq 1 60); do
  V=$(curl -fsS "${AUTH[@]}" "$API/v1/documents/$FIRST_DID" | jq -c '.versions[0] | {version_no, status}')
  echo "  t=$((i * 2))s newest version: $V"
  case "$V" in
    *'"version_no":2'*'"status":"ready"'* | *'"version_no":2'*'"status":"enriched"'*) break ;;
  esac
  sleep 2
done
STALE=0
for _ in $(seq 1 15); do
  STALE=$(curl -fsS "${AUTH[@]}" "$API/v1/chat/sessions/$SID/messages" | jq '[.items[] | select(.staleness != null)] | length')
  [ "$STALE" -ge 1 ] && break
  sleep 2
done
curl -fsS "${AUTH[@]}" "$API/v1/chat/sessions/$SID/messages" \
  | jq '[.items[] | select(.staleness != null) | {message_id: .id, staleness}]'
if [ "$STALE" -ge 1 ]; then
  echo "  answers that cited the old version are marked stale; re-ask with {\"reask_of\": \"<message_id>\"}"
else
  echo "  no stale marker yet (it is set when the new version becomes ready)"
fi

step "metrics"
echo "  documents:"
curl -fsS "${AUTH[@]}" "$API/v1/metrics/documents" | jq -c .
CHAT=$(curl -fsS "${AUTH[@]}" "$API/v1/metrics/chat")
echo "  chat:"
echo "$CHAT" | jq '{turns, outcomes, coverage, latency_ms, ttft_ms, cost_per_turn_usd, node_latency_ms,
  llm: {calls: .llm.calls, total_cost_usd: .llm.total_cost_usd}}'

step "spend"
LEDGER=$(echo "$CHAT" | jq -r '.llm.total_cost_usd // 0')
echo "  chat turns (sum of done events): \$$TURN_COST"
echo "  ledger total, briefs and chat (GET /v1/metrics/chat, llm.total_cost_usd): \$$LEDGER"
if awk -v s="$LEDGER" -v m="$MAX_USD" 'BEGIN { exit !(s < m) }'; then
  echo "  spend \$$LEDGER is below the \$$MAX_USD budget"
else
  echo "  spend \$$LEDGER is NOT below the \$$MAX_USD budget" >&2
  [ "${DEMO_STRICT:-0}" = 1 ] && exit 1
fi
echo
echo "demo: OK"
