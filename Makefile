# Vault — developer entry points. Python runs only from backend/.venv (uv-managed 3.12).
SHELL := /bin/bash
.DEFAULT_GOAL := help
UV := cd backend && UV_PYTHON_PREFERENCE=only-managed uv run --frozen
COMPOSE := docker compose

define not_implemented
	@echo "make $@: not implemented yet ($(1))"; exit 1
endef

.PHONY: help up down logs test test-unit test-integration lint fmt typecheck migrate seed \
        eval eval-retrieval eval-smoke loadtest ci

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | tr '\n' ' '; echo

up:
	$(call not_implemented,S0.04)
down:
	$(call not_implemented,S0.04)
logs:
	$(call not_implemented,S0.04)

test:
	$(UV) pytest
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

# --- Eval corpus (S0.18): third-party sources -> eval/sources/ (gitignored); rebuild the golden set ---
.PHONY: eval-fetch eval-build
EVAL_PY := $(UV) --group eval python
eval-fetch:
	eval/build/fetch_sources.sh
eval-build:
	$(EVAL_PY) ../eval/build/build_external.py
	$(EVAL_PY) ../eval/build/build_authored.py
