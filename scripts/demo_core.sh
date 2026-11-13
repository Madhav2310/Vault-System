#!/usr/bin/env bash
# Core demo (C3 exit check): upload -> ready -> ask -> cited answer -> history -> metrics, against the running
# stack (`make up`). Uses a fresh throwaway user (key printed only to this script's memory) and the authored
# sample document. One ask costs about $0.01 of OpenAI usage. Exits non-zero on the first failed step.
set -euo pipefail
cd "$(dirname "$0")/.."
API="${API_BASE_URL:-http://localhost:${API_PORT:-8100}}"
DOC="${DEMO_DOC:-eval/datasets/vault_eval_v1/docs/amendment_consulting.md}"
QUESTION="${DEMO_QUESTION:-How much written notice does either party have to give to terminate, and what is the term?}"
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

step "upload $DOC"
UP=$(curl -fsS "${AUTH[@]}" -F "file=@$DOC" "$API/v1/documents")
echo "$UP" | jq -c '{document_id,version_id,status,deduplicated}'
VID=$(echo "$UP" | jq -r .version_id)
DID=$(echo "$UP" | jq -r .document_id)

step "wait for ready"
for _ in $(seq 1 90); do
  ST=$(curl -fsS "${AUTH[@]}" "$API/v1/documents/$DID" | jq -r .status)
  echo "status=$ST"
  case "$ST" in ready|enriched) break ;; failed|unsupported|needs_ocr) echo "document ended in $ST" >&2; exit 1 ;; esac
  sleep 2
done
[ "$ST" = ready ] || [ "$ST" = enriched ] || { echo "timed out waiting for ready" >&2; exit 1; }

step "ask: $QUESTION"
SID=$(curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' -d '{}' "$API/v1/chat/sessions" | jq -r .id)
ANS=$(curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg q "$QUESTION" '{content:$q, client_message_id:"demo-1"}')" "$API/v1/chat/sessions/$SID/messages")
echo "$ANS" | jq '{status, response_type: .message.response_type, answer: .message.content}'
[ "$(echo "$ANS" | jq -r .message.response_type)" = answer ] || { echo "no answer produced" >&2; exit 1; }

step "history with cited evidence"
HIST=$(curl -fsS "${AUTH[@]}" "$API/v1/chat/sessions/$SID/messages")
echo "$HIST" | jq '.items[-1].segments[] | {text, support, evidence: [.evidence[] | {document_title, version_no, pages, cited_text}]}'
CITED=$(echo "$HIST" | jq '[.items[-1].segments[].evidence[]] | length')
[ "$CITED" -ge 1 ] || { echo "answer has no citations" >&2; exit 1; }

step "metrics"
curl -fsS "${AUTH[@]}" "$API/v1/metrics/documents" | jq -c .
curl -fsS "${AUTH[@]}" "$API/v1/metrics/chat" | jq -c .
echo; echo "demo_core: OK"
