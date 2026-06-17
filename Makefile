# docforge — common dev commands.
# Most of these assume the project venv is active or `.venv` exists.

PY      := .venv/Scripts/python.exe
PIP     := $(PY) -m pip
PYTEST  := $(PY) -m pytest

# `make` with no target shows help.
.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "docforge — common commands"
	@echo ""
	@echo "  install      install deps + project in editable mode"
	@echo "  test         run the full test suite"
	@echo "  test-fast    run only tests that don't hit the embedding model"
	@echo "  lint         ruff check"
	@echo "  format       ruff format"
	@echo "  serve        run the web server (uvicorn, :8000, --reload)"
	@echo "  run REPO=... run the docforge CLI against REPO"
	@echo "  eval         run docforge-eval over eval/testset"
	@echo "  docker       docker build -t docforge:local ."
	@echo "  clean        remove build/cache artifacts"
	@echo ""

.PHONY: install
install:
	$(PIP) install --upgrade pip
	$(PIP) install -e .[dev]

.PHONY: test
test:
	PYTHONPATH=src $(PYTEST) -q

.PHONY: test-fast
test-fast:
	PYTHONPATH=src $(PYTEST) -q -m "not slow" --ignore=tests/test_evals.py

.PHONY: lint
lint:
	$(PY) -m ruff check src tests

.PHONY: format
format:
	$(PY) -m ruff format src tests

.PHONY: serve
serve:
	$(PY) -m docforge.server.cli --reload

.PHONY: run
run:
	@if [ -z "$(REPO)" ]; then echo "Usage: make run REPO=/path/to/repo"; exit 1; fi
	$(PY) -m docforge.cli $(REPO)

.PHONY: eval
eval:
	$(PY) -m docforge.evals.cli --testset eval/testset --out eval/scoreboard_data.json

.PHONY: docker
docker:
	docker build -t docforge:local .

.PHONY: clean
clean:
	rm -rf .pytest_cache .ruff_cache dist build src/docforge.egg-info
	find . -path ./.venv -prune -o -name __pycache__ -prune -exec rm -rf {} +
