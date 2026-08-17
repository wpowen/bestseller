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
	@echo "    make secrets-scan   Scan tracked/untracked files for likely secrets"
	@echo "    make check          Run lint + format-check + type-check + secrets-scan"
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
	elif command -v $(UV) >/dev/null 2>&1; then \
		$(UV) run pre-commit install; \
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

.PHONY: secrets-scan
secrets-scan:
	$(PYTHON) scripts/scan_secrets.py --all-files

.PHONY: check
check: lint format-check type-check secrets-scan
	@echo "All checks passed."

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
.PHONY: test
test:
	$(UV) run pytest -m "not slow and not e2e" $(ARGS)

# Logical backup of the book library. The DB has been wiped three times
# (2026-07-14, 07-21, 07-23) — scripts/backup-db.sh existed after the second
# wipe but was never scheduled, so the third was still a surprise.
# Install the daily job yourself (it edits your crontab):
#   (crontab -l 2>/dev/null; echo "0 4 * * * cd $(PWD) && scripts/backup-db.sh >> output/backups/backup.log 2>&1") | crontab -
.PHONY: backup
backup:
	scripts/backup-db.sh

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

# ⚠️ 曾经写成 `-f docker-compose.yml -f docker-compose.ssd.yml`，理由是
# 「SSD override 把 PGDATA 挪到外置卷，不带它会指向另一个数据目录」。
# **那个理由已经过期**：docker-compose.override.yml 用
# `external: true` + `name: bestseller_pgdata` 直接引用**已经创建好、
# 已经绑在 SSD 上**的同一批卷（实测 device=/Volumes/MACSSD/Docker/bestseller/pgdata），
# 效果与 ssd.yml 等同。
#
# 而显式 `-f` 会**关掉 docker-compose.override.yml 的自动加载**，代价有两个：
#   ① 丢掉 `./src:/app/src` 活挂载 → 容器跑镜像里烘焙的旧代码
#      （2026-08-16 因此整批修复一次都没运行过）
#   ② 每次改代码都得 rebuild → 6 个镜像 ×2.98GB，这台机器还会在 apt 层 OOM
#
# 留空即用 `docker compose`，override 自动加载。只有**首次创建那两个卷**
# 时才需要 ssd.yml，见 docker-bootstrap-volumes。
COMPOSE_FILES ?=

# Rebuild the code images and bring the WHOLE stack up.
#
# Deploying by hand invites naming only the services you were thinking about
# (`up -d worker api web`), which silently leaves everything else stopped.
# The backup sidecar was dropped that way repeatedly and its dumps directory
# stayed empty through three library wipes — the one service whose absence is
# invisible until you need it. Naming no services here means compose starts
# them all, so a partial deploy is not something you can forget your way into.
.PHONY: docker-deploy
docker-deploy:
	docker compose $(COMPOSE_FILES) up -d
	docker compose $(COMPOSE_FILES) ps
	@bash scripts/verify_live_code_mount.sh

# 改了代码**不需要**这个：src/config/data 都是活挂载，`docker-deploy` 即可
# （进程要重新导入的话 `docker compose restart worker web`）。
# 只有改了 Dockerfile / pyproject / 系统依赖才需要 rebuild。
.PHONY: docker-rebuild
docker-rebuild:
	docker compose $(COMPOSE_FILES) build worker api web
	$(MAKE) docker-deploy

# 首次在一台新机器上创建 SSD 卷。卷建成后一律回到 docker-deploy。
.PHONY: docker-bootstrap-volumes
docker-bootstrap-volumes:
	docker compose -f docker-compose.yml -f docker-compose.ssd.yml up -d --no-start
	@echo "卷已创建。以后一律用 make docker-deploy（不带 -f）。"

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
