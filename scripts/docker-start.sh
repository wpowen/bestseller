#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# BestSeller Docker — One-Click Start
# =============================================================================
# Usage:
#   ./scripts/docker-start.sh              # Start all services
#   ./scripts/docker-start.sh --build      # Force rebuild images
#   ./scripts/docker-start.sh --detach     # Run in background (default)
#   ./scripts/docker-start.sh --attach     # Run in foreground (logs visible)
#   ./scripts/docker-start.sh --no-migrate # Skip database migration
# =============================================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Ensure Docker tools (including credential helpers) are in PATH
export PATH="/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"

# ── Defaults ──────────────────────────────────────────────────────────────────
FORCE_BUILD=false
DETACH=true
RUN_MIGRATE=true
COMPOSE_FILES=("-f" "docker-compose.yml")

# Auto-detect SSD override
SSD_COMPOSE="docker-compose.ssd.yml"
SSD_DATA_DIR="/Volumes/SSD/Docker/bestseller"
if [[ -f "$ROOT_DIR/$SSD_COMPOSE" && -d "$SSD_DATA_DIR" ]]; then
  COMPOSE_FILES+=("-f" "$SSD_COMPOSE")
fi

# ── Parse arguments ───────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --build)       FORCE_BUILD=true ;;
    --attach)      DETACH=false ;;
    --detach)      DETACH=true ;;
    --no-migrate)  RUN_MIGRATE=false ;;
    -h|--help)
      sed -n '3,10p' "$0" | sed 's/^# //' | sed 's/^#//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()  { printf "${CYAN}[docker-start]${NC} %s\n" "$1"; }
ok()   { printf "${GREEN}[docker-start]${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}[docker-start]${NC} %s\n" "$1"; }
err()  { printf "${RED}[docker-start]${NC} %s\n" "$1" >&2; }

# ── Pre-flight checks ────────────────────────────────────────────────────────
check_prerequisites() {
  local missing=()

  if ! command -v docker >/dev/null 2>&1; then
    missing+=("docker")
  fi

  if ! docker compose version >/dev/null 2>&1; then
    if ! command -v docker-compose >/dev/null 2>&1; then
      missing+=("docker-compose")
    fi
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    err "Missing required tools: ${missing[*]}"
    err "Install Docker Desktop or Docker Engine with Compose plugin."
    exit 1
  fi

  if ! docker info >/dev/null 2>&1; then
    err "Docker daemon is not running. Please start Docker first."
    exit 1
  fi
}

# Prefer 'docker compose' (v2 plugin), fallback to 'docker-compose' (v1)
detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  else
    echo "docker-compose"
  fi
}

# ── .env validation ──────────────────────────────────────────────────────────
validate_env() {
  if [[ ! -f "$ROOT_DIR/.env" ]]; then
    warn ".env file not found. Creating from .env.example ..."
    if [[ -f "$ROOT_DIR/.env.example" ]]; then
      cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
      warn "Created .env from .env.example — please edit it with your API keys."
    else
      err "No .env or .env.example found. Cannot continue."
      exit 1
    fi
  fi

  # Check for essential LLM keys (warn only, don't block)
  local has_llm_key=false
  for key in ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY GEMINI_API_KEY; do
    local val
    val="$(grep -E "^${key}=" "$ROOT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"
    if [[ -n "$val" && "$val" != *"your-"* && "$val" != *"here"* ]]; then
      has_llm_key=true
      break
    fi
  done

  if [[ "$has_llm_key" == "false" ]]; then
    warn "No valid LLM API key detected in .env"
    warn "Novel generation will fail without at least one provider key."
    warn "Set BESTSELLER__LLM__MOCK=true in .env to run in mock mode."
  fi
}

# ── Wait for service health ──────────────────────────────────────────────────
wait_for_service() {
  local service="$1"
  local url="$2"
  local max_wait="${3:-60}"
  local waited=0

  while [[ $waited -lt $max_wait ]]; do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  return 1
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
  check_prerequisites
  validate_env

  local COMPOSE
  COMPOSE="$(detect_compose)"

  # Wrapper: run docker compose with stdin closed so any interactive prompt
  # (e.g. "Recreate volume? (y/N)") immediately auto-answers N. This makes
  # the script bulletproof against accidental pgdata wipes.
  compose() {
    $COMPOSE "${COMPOSE_FILES[@]}" "$@" < /dev/null
  }

  # ── Step 0: Stop existing BestSeller containers (releases ports) ────────
  log "Stopping any running BestSeller containers ..."
  compose down --timeout 10 2>/dev/null || true

  # Resolve ports from .env (or defaults)
  local API_PORT DB_PORT REDIS_PORT MCP_PORT WEB_PORT
  API_PORT="$(grep -E '^API_PORT=' "$ROOT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo 8000)"
  DB_PORT="$(grep -E '^DB_PORT=' "$ROOT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo 5432)"
  REDIS_PORT="$(grep -E '^REDIS_PORT=' "$ROOT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo 6379)"
  MCP_PORT="$(grep -E '^MCP_PORT=' "$ROOT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo 3000)"
  WEB_PORT="$(grep -E '^WEB_PORT=' "$ROOT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo 8787)"
  API_PORT="${API_PORT:-8000}"
  DB_PORT="${DB_PORT:-5432}"
  REDIS_PORT="${REDIS_PORT:-6379}"
  MCP_PORT="${MCP_PORT:-3000}"
  WEB_PORT="${WEB_PORT:-8787}"

  # Check port conflicts with non-Docker processes and auto-resolve
  _find_free_port() {
    local start="$1"
    local port="$start"
    local max=$((start + 20))
    while [[ $port -lt $max ]]; do
      if ! lsof -ti :"$port" >/dev/null 2>&1; then
        echo "$port"
        return 0
      fi
      port=$((port + 1))
    done
    echo "$start"  # fallback — let Docker fail with a clear message
    return 1
  }

  for pair in "API:API_PORT:${API_PORT}" "DB:DB_PORT:${DB_PORT}" "Redis:REDIS_PORT:${REDIS_PORT}" "MCP:MCP_PORT:${MCP_PORT}" "Web:WEB_PORT:${WEB_PORT}"; do
    local name="${pair%%:*}"
    local rest="${pair#*:}"
    local var_name="${rest%%:*}"
    local port="${rest##*:}"
    local pids
    pids="$(lsof -ti :"$port" 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      local new_port
      new_port="$(_find_free_port "$((port + 1))")"
      warn "Port ${port} (${name}) is in use by PID(s): ${pids} → switching to ${new_port}"
      eval "${var_name}=${new_port}"
      export "${var_name}=${new_port}"
    fi
  done

  echo ""
  printf "${BOLD}BestSeller Docker Stack${NC}\n"
  echo "========================================"

  # Show SSD status
  if [[ ${#COMPOSE_FILES[@]} -gt 2 ]]; then
    ok "SSD detected — volumes mapped to ${SSD_DATA_DIR}"
  else
    log "SSD not detected — using Docker named volumes"
  fi

  # ── Step 1: Build images ─────────────────────────────────────────────────
  # Always invoke `compose build` and let Docker's layer cache decide what's
  # actually stale. If no source/Dockerfile change is detected, the build is
  # a few-second no-op; otherwise the affected images are rebuilt automatically.
  if [[ "$FORCE_BUILD" == "true" ]]; then
    log "Force-building Docker images (--no-cache) ..."
    compose --profile migrate build --no-cache
  else
    log "Building Docker images (incremental, layer-cache aware) ..."
    compose --profile migrate build
  fi

  # ── Step 2: Start infrastructure (DB + Redis) ────────────────────────────
  log "Starting infrastructure services (db, redis) ..."
  compose up -d db redis

  # Wait for health checks
  log "Waiting for PostgreSQL to be ready ..."
  local db_wait=0
  while [[ $db_wait -lt 30 ]]; do
    if compose exec -T db pg_isready -U "${DB_USER:-bestseller}" >/dev/null 2>&1; then
      break
    fi
    sleep 1
    db_wait=$((db_wait + 1))
  done
  if [[ $db_wait -ge 30 ]]; then
    err "PostgreSQL did not become ready within 30s"
    exit 1
  fi
  ok "PostgreSQL ready."

  log "Waiting for Redis to be ready ..."
  local redis_wait=0
  while [[ $redis_wait -lt 15 ]]; do
    if compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
      break
    fi
    sleep 1
    redis_wait=$((redis_wait + 1))
  done
  if [[ $redis_wait -ge 15 ]]; then
    err "Redis did not become ready within 15s"
    exit 1
  fi
  ok "Redis ready."

  # ── Step 3: Run database migration ────────────────────────────────────────
  if [[ "$RUN_MIGRATE" == "true" ]]; then
    log "Running database migrations ..."
    compose --profile migrate run --rm migrate
    ok "Migrations applied."
  else
    warn "Skipping database migrations (--no-migrate)."
  fi

  # ── Step 4: Start application services ────────────────────────────────────
  log "Starting application services ..."
  if [[ "$DETACH" == "true" ]]; then
    compose up -d api worker scheduler web
  else
    # In attach mode, we start most in background but stream logs
    compose up -d api worker scheduler web
  fi

  # Wait for API health
  log "Waiting for API to be healthy ..."
  if wait_for_service "api" "http://localhost:${API_PORT}/health" 45; then
    ok "API is healthy."
  else
    warn "API health check timed out. Check logs with: docker compose logs api"
  fi

  # ── Step 5: Start MCP server (depends on API) ────────────────────────────
  log "Starting MCP server ..."
  compose up -d mcp

  # ── Summary ────────────────────────────────────────────────────────────────
  echo ""
  echo "========================================"
  printf "${GREEN}${BOLD} All services started successfully!${NC}\n"
  echo "========================================"
  echo ""
  printf "  ${BOLD}Services:${NC}\n"
  printf "    API Server:    ${CYAN}http://localhost:${API_PORT}${NC}\n"
  printf "    MCP Server:    ${CYAN}http://localhost:${MCP_PORT}${NC}\n"
  printf "    Web Studio:    ${CYAN}http://localhost:${WEB_PORT}${NC}\n"
  printf "    PostgreSQL:    ${CYAN}localhost:${DB_PORT}${NC}\n"
  printf "    Redis:         ${CYAN}localhost:${REDIS_PORT}${NC}\n"
  echo ""
  printf "  ${BOLD}Quick checks:${NC}\n"
  printf "    Health:        ${CYAN}curl http://localhost:${API_PORT}/health${NC}\n"
  printf "    Readiness:     ${CYAN}curl http://localhost:${API_PORT}/ready${NC}\n"
  printf "    API docs:      ${CYAN}http://localhost:${API_PORT}/docs${NC}\n"
  echo ""
  printf "  ${BOLD}Management:${NC}\n"
  printf "    Logs:          ${CYAN}docker compose logs -f [service]${NC}\n"
  printf "    Status:        ${CYAN}docker compose ps${NC}\n"
  printf "    Stop:          ${CYAN}./scripts/docker-stop.sh${NC}\n"
  echo ""

  # Show running containers
  compose ps

  # In foreground mode, tail all logs
  if [[ "$DETACH" == "false" ]]; then
    echo ""
    log "Tailing logs (Ctrl+C to detach, services keep running) ..."
    $COMPOSE "${COMPOSE_FILES[@]}" logs -f --tail=50
  fi
}

main "$@"
