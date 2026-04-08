# =============================================================================
# BestSeller — Developer Makefile
# =============================================================================
# Prerequisites: Python 3.11+, uv (https://github.com/astral-sh/uv)
#
# Quick start:
#   make install    — set up virtual environment and install all dependencies
#   make dev        — install including dev extras and set up pre-commit hooks
#   make test       — run the full test suite
#   make lint       — run ruff linter
#   make format     — auto-format all source files
#   make type-check — run mypy static type checker
#   make coverage   — run tests and open HTML coverage report
#   make clean      — remove all generated artifacts
# =============================================================================

# Detect OS for open command
UNAME := $(shell uname)
ifeq ($(UNAME), Darwin)
    OPEN := open
else
    OPEN := xdg-open
endif

# Python / uv settings
PYTHON        ?= $(shell if [ -x /opt/homebrew/bin/python3 ]; then echo /opt/homebrew/bin/python3; elif command -v python3.11 >/dev/null 2>&1; then echo python3.11; elif command -v python3 >/dev/null 2>&1; then echo python3; else echo python; fi)
UV            := uv
VENV_DIR      := .venv
SRC_DIRS      := src tests
COVERAGE_HTML := htmlcov/index.html

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
.PHONY: help
help:
	@echo ""
	@echo "  BestSeller — Developer Commands"
	@echo ""
	@echo "  Setup"
	@echo "    make install        Install runtime dependencies only"
	@echo "    make dev            Install all dependencies incl. dev extras"
	@echo "    make hooks          Install pre-commit hooks"
	@echo ""
	@echo "  Quality"
	@echo "    make lint           Run ruff linter (check only)"
	@echo "    make lint-fix       Run ruff linter with auto-fix"
	@echo "    make format         Auto-format source files (ruff format)"
	@echo "    make format-check   Check formatting without modifying files"
	@echo "    make type-check     Run mypy static type checker"
	@echo "    make check          Run lint + format-check + type-check"
	@echo ""
	@echo "  Testing"
	@echo "    make test           Run all tests (unit + integration)"
	@echo "    make test-unit      Run unit tests only"
	@echo "    make test-integration Run integration tests only"
	@echo "    make test-e2e       Run end-to-end tests (requires API keys)"
	@echo "    make coverage       Run tests and open HTML coverage report"
	@echo ""
	@echo "  Generation"
	@echo "    make run            Run the CLI (pass ARGS='...' for arguments)"
	@echo "    make dev-start      Start local PostgreSQL + install environment"
	@echo "    make dev-stop       Stop local PostgreSQL"
	@echo "    make ui             Start the local Web Studio"
	@echo "    make verify         Run unit tests + end-to-end functional verification"
	@echo ""
	@echo "  Docker (Full Stack)"
	@echo "    make docker-up      Start all Docker services"
	@echo "    make docker-up-build Start + rebuild Docker images"
	@echo "    make docker-down    Stop all Docker services (keep data)"
	@echo "    make docker-purge   Stop + delete all data volumes"
	@echo "    make docker-clean   Stop + delete images and volumes"
	@echo "    make docker-logs    Tail logs (ARGS='api' for specific service)"
	@echo "    make docker-ps      Show running Docker services"
	@echo "    make docker-restart Restart entire Docker stack"
	@echo ""
	@echo "  Maintenance"
	@echo "    make db-init        Create PostgreSQL extensions and tables"
	@echo "    make db-upgrade     Apply Alembic migrations"
	@echo "    make db-upgrade-sql Render Alembic upgrade SQL without executing"
	@echo "    make clean          Remove generated artifacts"
	@echo "    make clean-all      Remove artifacts + virtual environment"
	@echo ""

# ---------------------------------------------------------------------------
# Environment Setup
# ---------------------------------------------------------------------------
.PHONY: install
install: $(VENV_DIR)/pyvenv.cfg
	@echo "Runtime environment ready. Activate with: source $(VENV_DIR)/bin/activate"

$(VENV_DIR)/pyvenv.cfg:
	$(UV) venv --python $(PYTHON) $(VENV_DIR)
	$(UV) pip install -e "."

.PHONY: dev
dev: $(VENV_DIR)/pyvenv.cfg
	$(UV) pip install -e ".[dev,export]"
	@echo "Development environment ready."
	@$(MAKE) hooks

.PHONY: hooks
hooks:
	@if command -v pre-commit >/dev/null 2>&1; then \
		pre-commit install; \
	else \
		echo "pre-commit not found, skipping hook installation."; \
	fi

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------
.PHONY: lint
lint:
	$(UV) run ruff check $(SRC_DIRS)

.PHONY: lint-fix
lint-fix:
	$(UV) run ruff check --fix $(SRC_DIRS)

.PHONY: format
format:
	$(UV) run ruff format $(SRC_DIRS)

.PHONY: format-check
format-check:
	$(UV) run ruff format --check $(SRC_DIRS)

.PHONY: type-check
type-check:
	$(UV) run mypy src/bestseller

.PHONY: check
check: lint format-check type-check
	@echo "All checks passed."

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
.PHONY: test
test:
	$(UV) run pytest -m "not slow and not e2e" $(ARGS)

.PHONY: test-unit
test-unit:
	$(UV) run pytest -m unit $(ARGS)

.PHONY: test-integration
test-integration:
	$(UV) run pytest -m integration $(ARGS)

.PHONY: test-e2e
test-e2e:
	$(UV) run pytest -m e2e $(ARGS)

.PHONY: test-all
test-all:
	$(UV) run pytest $(ARGS)

.PHONY: coverage
coverage:
	$(UV) run pytest -m "not slow and not e2e" --cov=src/bestseller --cov-report=html
	@echo "Opening coverage report..."
	$(OPEN) $(COVERAGE_HTML)

# ---------------------------------------------------------------------------
# CLI shortcut
# ---------------------------------------------------------------------------
.PHONY: run
run:
	$(UV) run bestseller $(ARGS)

.PHONY: dev-start
dev-start:
	./scripts/start.sh

.PHONY: dev-stop
dev-stop:
	./scripts/stop.sh $(ARGS)

.PHONY: ui
ui:
	./scripts/ui.sh $(ARGS)

.PHONY: verify
verify:
	./scripts/verify.sh

# ---------------------------------------------------------------------------
# Docker (Full Stack)
# ---------------------------------------------------------------------------
.PHONY: docker-up
docker-up:
	./scripts/docker-start.sh $(ARGS)

.PHONY: docker-up-build
docker-up-build:
	./scripts/docker-start.sh --build

.PHONY: docker-down
docker-down:
	./scripts/docker-stop.sh

.PHONY: docker-purge
docker-purge:
	./scripts/docker-stop.sh --purge

.PHONY: docker-clean
docker-clean:
	./scripts/docker-stop.sh --clean

.PHONY: docker-logs
docker-logs:
	docker compose logs -f $(ARGS)

.PHONY: docker-ps
docker-ps:
	docker compose ps

.PHONY: docker-restart
docker-restart:
	./scripts/docker-stop.sh && ./scripts/docker-start.sh

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
.PHONY: db-init
db-init:
	$(UV) run bestseller db init

.PHONY: db-upgrade
db-upgrade:
	$(UV) run alembic upgrade head

.PHONY: db-upgrade-sql
db-upgrade-sql:
	$(UV) run alembic upgrade head --sql

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
.PHONY: clean
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ coverage.xml .coverage

.PHONY: clean-all
clean-all: clean
	rm -rf $(VENV_DIR)
	@echo "Virtual environment removed."
