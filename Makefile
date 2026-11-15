# Vault developer entry points. Python runs only from backend/.venv (uv-managed 3.12). `make` lists the targets.
SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
# Host-side tests reach compose services on their published ports (docker-compose.yml defaults).
HOST_ENV := TIKTOKEN_CACHE_DIR=$(HOME)/.cache/tiktoken DATABASE_URL=postgresql+asyncpg://vault:vault@localhost:$${POSTGRES_PORT:-5442}/vault \
            REDIS_URL=redis://localhost:$${REDIS_PORT:-6389}/0 \
            API_BASE_URL=http://localhost:$${API_PORT:-8100}
UV := cd backend && $(HOST_ENV) UV_PYTHON_PREFERENCE=only-managed uv run --frozen
EVAL_PY := $(UV) --group eval python
CONFIG ?= ops/otel/collector.yaml
UI_OPENAPI := $(CURDIR)/frontend/.openapi.json
UI_E2E_ENV := COMPOSE_PROJECT_NAME=vault-uitest COMPOSE_FILE=docker-compose.yml:docker-compose.ui-test.yml \
              API_PORT=8190 POSTGRES_PORT=5542 REDIS_PORT=6489

.PHONY: help up up-test up-full down logs migrate seed seed-users \
        ci lint fmt typecheck test test-unit test-integration test-e2e smoke-providers \
        requirements ui ui-types ui-types-check ui-check ui-e2e demo-record diagrams otel-validate \
        eval-answers eval-retrieval eval-diagnose eval-gate eval-gate-t0 eval-audit eval-audit-apply \
        eval-labels eval-docs eval-fetch c1-check

help:  ## list the targets
	@awk 'BEGIN {FS = ":.*## "} /^##@/ {printf "\n%s\n", substr($$0, 5)} /^[a-z0-9-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Stack
up:  ## build and start the default stack, wait until healthy
	GIT_SHA=$$(git rev-parse --short HEAD) $(COMPOSE) up -d --build
	scripts/wait_healthy.sh 180
up-test:  ## Postgres and Redis only (integration tests need nothing else)
	$(COMPOSE) up -d postgres redis
	scripts/wait_healthy.sh 120 postgres redis
up-full:  ## default stack plus observability (heavy)
	OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-lgtm:4317 GIT_SHA=$$(git rev-parse --short HEAD) $(COMPOSE) --profile full up -d --build
	COMPOSE_PROFILES=full scripts/wait_healthy.sh 240
down:  ## stop every service of every profile
	$(COMPOSE) --profile '*' down
logs:  ## follow the logs of the running services
	$(COMPOSE) logs -f --tail=200
migrate:  ## apply database migrations
	$(COMPOSE) run --rm --no-deps api alembic upgrade head
seed: seed-users  ## seed the demo users
seed-users:  ## create the demo users and print their API keys
	$(COMPOSE) exec -T api python -m app.db.seed users

##@ Quality
ci: lint typecheck test-unit ui-check  ## lint, types, unit tests and frontend checks (no Docker)
lint:  ## ruff lint and format check
	$(UV) ruff check .
	$(UV) ruff format --check .
fmt:  ## format and auto-fix
	$(UV) ruff format .
	$(UV) ruff check --fix .
typecheck:  ## mypy (strict on backend/app)
	$(UV) mypy
test:  ## unit, integration and e2e tests (needs `make up`)
	$(UV) pytest tests/unit tests/integration tests/e2e
test-unit:  ## unit tests
	$(UV) pytest tests/unit
test-integration:  ## integration tests (needs `make up-test`)
	$(UV) pytest tests/integration
test-e2e:  ## end-to-end API tests
	$(UV) pytest tests/e2e
smoke-providers:  ## one live model call and one live embedding call (needs OPENAI_API_KEY; under $$0.001)
	$(UV) pytest -m live tests/live -s

##@ Frontend and docs
define ui_dump_openapi
	$(UV) python -c "import json,sys; from app.main import create_app; json.dump(create_app().openapi(), open(sys.argv[1], 'w'), indent=1, sort_keys=True)" $(UI_OPENAPI)
endef
ui:  ## build the web UI served by the API at /app
	cd frontend && npm ci && npm run build
requirements:  ## regenerate requirements.txt (runtime) and requirements-dev.txt from uv.lock
	cd backend && UV_PYTHON_PREFERENCE=only-managed uv export --frozen --no-hashes --no-emit-project --no-dev --group eval -o ../requirements.txt -q
	cd backend && UV_PYTHON_PREFERENCE=only-managed uv export --frozen --no-hashes --no-emit-project -o ../requirements-dev.txt -q
ui-types:  ## regenerate frontend/src/api/schema.gen.ts from the OpenAPI document
	$(ui_dump_openapi)
	cd frontend && npx openapi-typescript .openapi.json -o src/api/schema.gen.ts; rc=$$?; rm -f .openapi.json; exit $$rc
ui-types-check:  ## fail when the committed generated types are stale
	$(ui_dump_openapi)
	cd frontend && npx openapi-typescript .openapi.json -o /tmp/vault-schema.gen.ts; rc=$$?; rm -f .openapi.json; [ $$rc -eq 0 ] && diff -u src/api/schema.gen.ts /tmp/vault-schema.gen.ts || { echo "ui types are stale: run make ui-types"; exit 1; }
ui-check:  ## frontend typecheck and lint (skipped without node_modules)
	@if [ -d frontend/node_modules ]; then cd frontend && npm run typecheck && npm run lint; else echo "frontend skipped (no frontend/node_modules)"; fi
ui-e2e:  ## Playwright e2e against a fake-model stack on its own ports, torn down afterwards
	@set -euo pipefail; export $(UI_E2E_ENV); base=http://localhost:8190; \
	trap 'docker compose --profile "*" down -v' EXIT; \
	docker compose --profile "*" down -v >/dev/null 2>&1 || true; \
	GIT_SHA=$$(git rev-parse --short HEAD) docker compose build api; \
	docker compose up -d postgres redis; scripts/wait_healthy.sh 120 postgres redis; \
	docker compose run --rm --no-deps api alembic upgrade head; \
	GIT_SHA=$$(git rev-parse --short HEAD) docker compose up -d; scripts/wait_healthy.sh 240 api worker-ingest worker-ai; \
	key=$$(docker compose exec -T api python -m app.db.seed users | sed -n 's/^alice@vault.local: API key //p'); \
	test -n "$$key" || { echo "ui-e2e: no API key from seed"; exit 1; }; \
	for f in frontend/e2e/fixtures/*.txt; do \
	  curl -fsS -H "Authorization: Bearer $$key" -F "file=@$$f" $$base/v1/documents >/dev/null; \
	done; \
	n=$$(( 2 * $$(ls frontend/e2e/fixtures/*.txt | wc -l) )); \
	for i in $$(seq 1 90); do \
	  : "document and version both carry the status, so n counts two per file"; \
	  ready=$$(curl -fsS -H "Authorization: Bearer $$key" "$$base/v1/documents?limit=100" | { grep -o '"status":"enriched"' || true; } | wc -l | tr -d ' '); \
	  [ "$$ready" -ge "$$n" ] && break; sleep 2; \
	done; \
	[ "$$ready" -ge "$$n" ] || { echo "ui-e2e: fixtures not enriched ($$ready/$$n)"; exit 1; }; \
	cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npx playwright install chromium \
	  && UI_BASE_URL=$$base UI_API_KEY=$$key npx playwright test
demo-record:  ## record the scripted demo as video against the running stack
	cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npx playwright install chromium \
	  && DEMO_RECORD=1 UI_BASE_URL=http://localhost:$${API_PORT:-8100} npx playwright test e2e/demo.spec.ts
diagrams:  ## regenerate the chat-turn diagram source and render docs/img/*.mmd to SVG
	$(UV) python ../scripts/gen_turn_diagram.py
	for f in docs/img/*.mmd; do npx -y @mermaid-js/mermaid-cli@11.4.2 -i $$f -o $${f%.mmd}.svg || exit 1; done
otel-validate:  ## validate the OpenTelemetry collector config
	docker run --rm --entrypoint /otel-lgtm/otelcol-contrib/otelcol-contrib \
	  -v "$(abspath $(CONFIG))":/cfg/config.yaml:ro grafana/otel-lgtm:0.38.1 \
	  validate --feature-gates service.profilesSupport --config=file:/cfg/config.yaml

##@ Evaluation
eval-answers:  ## answers and judges on one split, capped: SPLIT=dev MAX_USD=1 [FILTER=stratum=injection] [EVAL_ARGS=...]
	@grep -qiE '^BRIEF_ENABLED=false' .env 2>/dev/null || [ "$${BRIEF_ENABLED:-}" = false ] || echo "reminder: BRIEF_ENABLED=false must be set in the WORKER env (.env, then recreate the worker) or uploads pay for briefs"
	$(UV) env PYTHONPATH=.. python -m eval.run --suite full --config answers --split $(or $(SPLIT),train) --max-usd $(or $(MAX_USD),3) $(if $(FILTER),--filter $(FILTER)) --pipeline eval.pipeline_http:setup $(EVAL_ARGS)
eval-retrieval:  ## retrieval-only suite in process, no chat model: [SPLIT=train] [RETR_CONFIG=retrieval@2] [EVAL_USER=...]
	$(UV) env PYTHONPATH=.. python -m eval.run --suite retrieval --config $(or $(RETR_CONFIG),retrieval@2) --split $(or $(SPLIT),train) --pipeline eval.pipeline_retrieval:setup $(EVAL_ARGS)
eval-diagnose:  ## where a run's gold passages were lost, offline: RUN=<run_id>
	@test -n "$(RUN)" || { echo "usage: make eval-diagnose RUN=<run_id>"; exit 2; }
	VAULT_EVAL_SOURCES=$(or $(VAULT_EVAL_SOURCES),$(CURDIR)/eval/sources) $(UV) env PYTHONPATH=.. python -m eval.diagnose --run $(RUN) $(DIAG_ARGS)
eval-gate:  ## Tier 1 gate, no model call: CAND=eval/reports/<run_id>.json [BASE=eval/baselines/answer.json]
	@test -n "$(CAND)" || { echo "usage: make eval-gate [BASE=eval/baselines/answer.json] CAND=eval/reports/<run_id>.json (docs/evaluation.md)"; exit 2; }
	$(UV) env PYTHONPATH=.. python -m eval.gate compare ../$(or $(BASE),eval/baselines/answer.json) ../$(CAND) $(GATE_ARGS)
eval-gate-t0:  ## Tier 0 gate: schema, documents map, citations, retrieval vs baseline, security [EVAL_USER=...]
	$(UV) env PYTHONPATH=.. python -m eval.gate schema
	$(MAKE) eval-docs ARGS=--check
	$(UV) env PYTHONPATH=.. python -m eval.gate citations $(RUNS)
	$(MAKE) eval-retrieval EVAL_ARGS="--compare retrieval"
	$(UV) pytest tests/integration/security
eval-audit:  ## gold audit of a run's answers: ANSWERS=eval/reports/<run_id>.answers.jsonl [MAX_USD=2]
	@test -n "$(ANSWERS)" || { echo "usage: make eval-audit ANSWERS=eval/reports/<run_id>.answers.jsonl [MAX_USD=2]"; exit 2; }
	$(UV) env PYTHONPATH=.. python -m eval.build.audit --answers ../$(ANSWERS) --max-usd $(or $(MAX_USD),2) $(AUDIT_ARGS)
eval-audit-apply:  ## apply audit_queue.yaml decisions to the dataset [APPLY_ARGS=--check]
	$(UV) env PYTHONPATH=.. python -m eval.build.apply_audit $(APPLY_ARGS)
eval-labels:  ## export judge-calibration label pairs: ANSWERS=... [N=200]
	@test -n "$(ANSWERS)" || { echo "usage: make eval-labels ANSWERS=eval/reports/<run_id>.answers.jsonl [N=200]"; exit 2; }
	$(UV) env PYTHONPATH=.. python -m eval.judge --export-labels $(or $(N),200) --answers ../$(ANSWERS) --out ../eval/labels/labeled.yaml
eval-docs:  ## regenerate vault_eval/documents.yaml [ARGS=--download|--check]
	cd backend && VAULT_EVAL_SOURCES=$(or $(VAULT_EVAL_SOURCES),$(CURDIR)/eval/sources) UV_PYTHON_PREFERENCE=only-managed uv run --frozen python ../eval/build/build_docs.py $(ARGS)
eval-fetch:  ## download the third-party sources into eval/sources/
	eval/build/fetch_sources.sh
c1-check:  ## every corpus document through the real ingestion stages (fake embeddings, no cost)
	export VAULT_CORPUS=1; $(UV) pytest tests/integration/ingestion/test_corpus_pipeline.py -s -q
