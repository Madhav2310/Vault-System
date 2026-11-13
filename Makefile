# Vault — developer entry points. Python runs only from backend/.venv (uv-managed 3.12).
SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
# Host-side tests reach compose services on their published ports (docker-compose.yml defaults).
HOST_ENV := TIKTOKEN_CACHE_DIR=$(HOME)/.cache/tiktoken DATABASE_URL=postgresql+asyncpg://vault:vault@localhost:$${POSTGRES_PORT:-5442}/vault \
            REDIS_URL=redis://localhost:$${REDIS_PORT:-6389}/0 \
            RERANKER_URL=http://localhost:$${RERANKER_PORT:-8180} \
            API_BASE_URL=http://localhost:$${API_PORT:-8100}
UV := cd backend && $(HOST_ENV) UV_PYTHON_PREFERENCE=only-managed uv run --frozen

define not_implemented
	@echo "make $@: not implemented yet ($(1))"; exit 1
endef

.PHONY: help up down logs test test-unit test-integration test-e2e lint fmt typecheck migrate seed \
        seed-users up-test up-rerank up-full eval eval-retrieval eval-retrieval-fast eval-answers eval-smoke loadtest ci

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | tr '\n' ' '; echo

up:
	GIT_SHA=$$(git rev-parse --short HEAD) $(COMPOSE) up -d --build
	scripts/wait_healthy.sh 180
up-test:  ## Postgres + Redis only (integration tests need nothing else)
	$(COMPOSE) up -d postgres redis
	scripts/wait_healthy.sh 120 postgres redis
up-rerank:  ## default stack + the reranker
	GIT_SHA=$$(git rev-parse --short HEAD) $(COMPOSE) --profile rerank up -d --build
	COMPOSE_PROFILES=rerank scripts/wait_healthy.sh 240
up-full:  ## default stack + reranker + observability (heavy)
	GIT_SHA=$$(git rev-parse --short HEAD) $(COMPOSE) --profile full up -d --build
	COMPOSE_PROFILES=full scripts/wait_healthy.sh 240
down:
	$(COMPOSE) --profile '*' down
logs:
	$(COMPOSE) logs -f --tail=200

test:
	$(UV) pytest tests/unit tests/integration tests/e2e
test-e2e:
	$(UV) pytest tests/e2e
test-unit:
	$(UV) pytest tests/unit
test-integration:
	$(UV) pytest tests/integration

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .
fmt:
	$(UV) ruff format .
	$(UV) ruff check --fix .
typecheck:
	$(UV) mypy

migrate:
	$(COMPOSE) run --rm --no-deps api alembic upgrade head
seed: seed-users
seed-users:
	$(COMPOSE) exec -T api python -m app.db.seed users
eval:
	$(call not_implemented,S2.14)
eval-retrieval:
	$(UV) env PYTHONPATH=.. python -m eval.run --suite retrieval --config retrieval --pipeline eval.pipeline_http:setup $(EVAL_ARGS)
eval-retrieval-fast:
	$(UV) env PYTHONPATH=.. python -m eval.run --suite retrieval --config $(or $(RETR_CONFIG),retrieval@1) --split $(or $(SPLIT),all) --pipeline eval.pipeline_retrieval:setup $(EVAL_ARGS)
eval-answers:  ## full answers with judges: make eval-answers SPLIT=train MAX_USD=3 [FILTER=scope_docs=1]
	$(UV) env PYTHONPATH=.. python -m eval.run --suite full --config answers --split $(or $(SPLIT),train) --max-usd $(or $(MAX_USD),3) $(if $(FILTER),--filter $(FILTER)) --pipeline eval.pipeline_http:setup $(EVAL_ARGS)
eval-smoke:
	$(UV) env PYTHONPATH=.. python -m eval.run --suite smoke --config smoke --pipeline eval.pipeline_http:setup $(EVAL_ARGS)
loadtest:
	$(call not_implemented,S5.12)

# Fails on the first error: lint → typecheck → unit tests.
ci: lint typecheck test-unit

# --- Eval corpus (S0.18): third-party sources -> eval/sources/ (gitignored); rebuild the golden set ---
.PHONY: eval-fetch eval-build eval-extraction c1-check
EVAL_PY := $(UV) --group eval python
eval-fetch:
	eval/build/fetch_sources.sh
eval-build:
	$(EVAL_PY) ../eval/build/build_external.py
	$(EVAL_PY) ../eval/build/build_authored.py
# C1 checkpoint: every corpus document through the real stages (FakeProvider embeddings, no API cost)
c1-check:
	export VAULT_CORPUS=1; $(UV) pytest tests/integration/ingestion/test_corpus_pipeline.py -s -q
# S1.13 AC8: gold-quote locatability with the production PDF extractor (no LLM calls)
eval-extraction:
	$(EVAL_PY) ../eval/extraction_check.py

# --- OpenTelemetry collector (S0.15 / S0.22) ---
CONFIG ?= ops/otel/collector.yaml
otel-validate:
	docker run --rm --entrypoint /otel-lgtm/otelcol-contrib/otelcol-contrib \
	  -v "$(abspath $(CONFIG))":/cfg/config.yaml:ro grafana/otel-lgtm:0.38.1 \
	  validate --feature-gates service.profilesSupport --config=file:/cfg/config.yaml

# S0.13 AC13: one live Haiku call + one live embedding call (needs ANTHROPIC_API_KEY, OPENAI_API_KEY; <$0.001).
.PHONY: smoke-providers
smoke-providers:
	$(UV) pytest -m live tests/live -s
