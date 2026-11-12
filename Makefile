# Vault — developer entry points. Python runs only from backend/.venv (uv-managed 3.12).
SHELL := /bin/bash
.DEFAULT_GOAL := help
UV := cd backend && UV_PYTHON_PREFERENCE=only-managed uv run --frozen
COMPOSE := docker compose

define not_implemented
	@echo "make $@: not implemented yet ($(1))"; exit 1
endef

.PHONY: help up down logs test test-unit test-integration test-e2e lint fmt typecheck migrate seed \
        eval eval-retrieval eval-smoke loadtest ci

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
	$(call not_implemented,S0.08)
seed:
	$(call not_implemented,S0.09)
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
