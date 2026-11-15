#!/usr/bin/env bash
# Core demo smoke: upload two documents -> ready -> streamed cited answer (events printed) -> compare
# question -> run explanation -> history -> metrics, against the running stack (`make up`). Uses a fresh
# throwaway user (key printed only to this script's memory) and the authored sample documents. Two asks cost
# about $0.03 of OpenAI usage. Exits non-zero on the first failed step.
set -euo pipefail
cd "$(dirname "$0")/.."
API="${API_BASE_URL:-http://localhost:${API_PORT:-8100}}"
DOC="${DEMO_DOC:-eval/datasets/vault_eval/docs/amendment_consulting.md}"
DOC2="${DEMO_DOC2:-eval/datasets/vault_eval/docs/side_letter_endorsement.docx}"
QUESTION="${DEMO_QUESTION:-How much written notice does either party have to give to terminate, and what is the term?}"
Q_COMPARE="${DEMO_Q_COMPARE:-Compare the two documents: do they differ on the term or the notice period?}"
step() { printf '\n== %s\n' "$*"; }

step "create a demo user"
EMAIL="demo-$(date +%s)@vault.local"
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
AUTH=(-H "Authorization: Bearer $KEY")

upload_ready() {
  local up did st
  up=$(curl -fsS "${AUTH[@]}" -F "file=@$1" "$API/v1/documents")
  echo "$up" | jq -c '{document_id,version_id,status,deduplicated}'
  did=$(echo "$up" | jq -r .document_id)
  for _ in $(seq 1 90); do
    st=$(curl -fsS "${AUTH[@]}" "$API/v1/documents/$did" | jq -r .status)
    echo "status=$st"
    case "$st" in ready|enriched) return 0 ;; failed|unsupported|needs_ocr) echo "document ended in $st" >&2; return 1 ;; esac
    sleep 2
  done
  echo "timed out waiting for ready" >&2
  return 1
}

# ask_stream <client_message_id> <question>: stream the turn, print every event, leave the run id in RUN_ID
ask_stream() {
  local out
  out=$(mktemp)
  curl -fsSN "${AUTH[@]}" -H 'Accept: text/event-stream' -H 'Content-Type: application/json' \
    -d "$(jq -n --arg q "$2" --arg id "$1" '{content:$q, client_message_id:$id}')" \
    "$API/v1/chat/sessions/$SID/messages" | tee "$out" | sed -un 's/^data: //p' | jq -c .
  RUN_ID=$(sed -n 's/^data: //p' "$out" | jq -r 'select(.type=="run.started") | .run_id' | head -1)
  DONE=$(sed -n 's/^data: //p' "$out" | jq -c 'select(.type=="done")' | head -1)
  rm -f "$out"
  [ -n "$RUN_ID" ] && [ -n "$DONE" ] || { echo "stream did not finish with a done event" >&2; exit 1; }
}

step "upload $DOC"
upload_ready "$DOC"
step "upload $DOC2"
upload_ready "$DOC2"

SID=$(curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' -d '{}' "$API/v1/chat/sessions" | jq -r .id)

step "ask (streamed events): $QUESTION"
ask_stream demo-1 "$QUESTION"
FIRST_RUN="$RUN_ID"

step "ask a compare question (streamed events): $Q_COMPARE"
ask_stream demo-2 "$Q_COMPARE"

step "run explanation (GET /v1/runs/$RUN_ID)"
curl -fsS "${AUTH[@]}" "$API/v1/runs/$RUN_ID" | jq '{status, route, explanation}'
echo "first question, explanation:"
curl -fsS "${AUTH[@]}" "$API/v1/runs/$FIRST_RUN" | jq -r '.explanation[]'

step "history with cited evidence"
HIST=$(curl -fsS "${AUTH[@]}" "$API/v1/chat/sessions/$SID/messages")
echo "$HIST" | jq '.items[-1].segments[] | {text, support, evidence: [.evidence[] | {document_title, version_no, pages, cited_text}]}'
CITED=$(echo "$HIST" | jq '[.items[-1].segments[].evidence[]] | length')
[ "$CITED" -ge 1 ] || { echo "answer has no citations" >&2; exit 1; }

step "metrics"
curl -fsS "${AUTH[@]}" "$API/v1/metrics/documents" | jq -c .
curl -fsS "${AUTH[@]}" "$API/v1/metrics/chat" | jq -c .
echo; echo "demo_core: OK"
