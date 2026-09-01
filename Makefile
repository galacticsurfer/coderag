.PHONY: help install migrate revision test test-fast lint fmt typecheck run demo clean

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install the package with dev extras
	uv venv --python 3.12 $(VENV) || python3 -m venv $(VENV)
	. $(VENV)/bin/activate && (uv pip install -e ".[dev]" || pip install -e ".[dev]")

migrate: ## Apply database migrations
	. $(VENV)/bin/activate && alembic upgrade head

revision: ## Autogenerate a new migration: make revision m="message"
	. $(VENV)/bin/activate && alembic revision --autogenerate -m "$(m)"

test: ## Run the full test suite
	. $(VENV)/bin/activate && pytest

test-fast: ## Run only tests that don't need the database
	. $(VENV)/bin/activate && pytest -m "not db and not slow"

lint: ## Ruff lint
	. $(VENV)/bin/activate && ruff check src tests scripts

fmt: ## Ruff autofix + format
	. $(VENV)/bin/activate && ruff check --fix src tests scripts && ruff format src tests scripts

typecheck: ## mypy
	. $(VENV)/bin/activate && mypy src/coderag

run: ## Run the API locally
	. $(VENV)/bin/activate && uvicorn coderag.api.app:app --reload

demo: ## Index the bundled demo repo and run a sample search (no LLM needed)
	. $(VENV)/bin/activate && coderag index ./examples/demo-repository \
		&& coderag search "where are failed payments retried?"

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
