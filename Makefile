# Vault — developer entry points. Python runs only from backend/.venv (uv-managed 3.12).
SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
# Host-side tests reach compose services on their published ports (docker-compose.yml defaults).
HOST_ENV := DATABASE_URL=postgresql+asyncpg://vault:vault@localhost:$${POSTGRES_PORT:-5442}/vault \
            REDIS_URL=redis://localhost:$${REDIS_PORT:-6389}/0 \
            RERANKER_URL=http://localhost:$${RERANKER_PORT:-8180} \
            API_BASE_URL=http://localhost:$${API_PORT:-8100}
UV := cd backend && $(HOST_ENV) UV_PYTHON_PREFERENCE=only-managed uv run --frozen

define not_implemented
	@echo "make $@: not implemented yet ($(1))"; exit 1
endef

.PHONY: help up down logs test test-unit test-integration test-e2e lint fmt typecheck migrate seed \
        seed-users eval eval-retrieval eval-smoke loadtest ci

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | tr '\n' ' '; echo

up:
	GIT_SHA=$$(git rev-parse --short HEAD) $(COMPOSE) up -d --build
	scripts/wait_healthy.sh 180
down:
	$(COMPOSE) down
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
	$(call not_implemented,S2.17)
eval-smoke:
	$(call not_implemented,S7.10)
loadtest:
	$(call not_implemented,S5.12)

# Fails on the first error: lint → typecheck → unit tests.
ci: lint typecheck test-unit

# --- Eval corpus (S0.18): third-party sources -> eval/sources/ (gitignored); rebuild the golden set ---
.PHONY: eval-fetch eval-build
EVAL_PY := $(UV) --group eval python
eval-fetch:
	eval/build/fetch_sources.sh
eval-build:
	$(EVAL_PY) ../eval/build/build_external.py
	$(EVAL_PY) ../eval/build/build_authored.py

# --- OpenTelemetry collector (S0.15 / S0.22) ---
CONFIG ?= ops/otel/collector.yaml
otel-validate:
	docker run --rm --entrypoint /otel-lgtm/otelcol-contrib/otelcol-contrib \
	  -v "$(abspath $(CONFIG))":/cfg/config.yaml:ro grafana/otel-lgtm:0.38.1 \
	  validate --feature-gates service.profilesSupport --config=file:/cfg/config.yaml
