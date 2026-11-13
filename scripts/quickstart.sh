#!/usr/bin/env bash
# One-command start: checks Docker, creates .env, starts the stack, migrates, creates a demo user.
# Usage: scripts/quickstart.sh        (needs Docker running and OPENAI_API_KEY in .env)
set -euo pipefail
cd "$(dirname "$0")/.."

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop (see docs/dev-environment.md for recommended limits) and retry." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Put your OPENAI_API_KEY in it, then rerun this script." >&2
  exit 1
fi
if ! grep -Eq '^OPENAI_API_KEY=.+' .env; then
  echo "OPENAI_API_KEY is empty in .env. Add it and rerun." >&2
  exit 1
fi

make up
make migrate
API="http://localhost:${API_PORT:-8100}"
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
echo
echo "Vault is up."
echo "  API docs:  $API/docs"
echo "  Your key:  $KEY   (send as 'Authorization: Bearer <key>'; shown once)"
echo "  Demo:      scripts/demo_core.sh"
