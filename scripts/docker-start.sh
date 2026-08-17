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
# 留空 = 用裸 `docker compose`，让 docker-compose.override.yml 自动加载。
#
# ⚠️ 这里曾经拼 ("-f" "docker-compose.yml" "-f" "docker-compose.ssd.yml")。
# 显式 -f 会**关掉 override 的自动加载**，代价有两个：
#   ① 丢掉 ./src:/app/src 活挂载 → 容器跑镜像里烘焙的旧代码
#      （2026-08-16 因此整批修复一次都没运行过，症状酷似「Python 缓存导入」）
#   ② 逼着每次改代码都 rebuild → 6 镜像 ×2.98GB，本机还会在 apt 层 OOM
#
# 不带 ssd.yml **不会**丢 SSD：override 用 external:true + name:bestseller_pgdata
# 引用的就是已经绑在 /Volumes/MACSSD/Docker/bestseller 上的同一批卷。
# 两者还在 pgdata 上互斥（external vs driver），三个一起带直接报
# conflicting parameters —— 当初为绕开这个报错而丢掉 override，
# 一个绕过变成了长期失明。
COMPOSE_FILES=()

# 首次在新机器上创建那两个卷时才需要 ssd.yml；卷已存在则一律不带。
SSD_COMPOSE="docker-compose.ssd.yml"
SSD_DATA_DIR="/Volumes/MACSSD/Docker/bestseller"
if [[ -f "$ROOT_DIR/$SSD_COMPOSE" && -d "$SSD_DATA_DIR" ]] \
   && ! docker volume inspect bestseller_pgdata >/dev/null 2>&1; then
  echo "首次运行：卷 bestseller_pgdata 不存在，用 ssd.yml 创建它。"
  COMPOSE_FILES=("-f" "docker-compose.yml" "-f" "$SSD_COMPOSE")
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
  for key in ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY GEMINI_API_KEY MINIMAX_API_KEY NVIDIA_API_KEY NIM_API_KEY ARK_API_KEY VOLCENGINE_API_KEY VOLCENGINE_ARK_API_KEY; do
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

refresh_images_from_local_base() {
  local base_image="bestseller-local-base:docker-start-fallback"
  local source_image=""
  for candidate in bestseller-api:latest bestseller-worker:latest bestseller-web:latest; do
    if docker image inspect "$candidate" >/dev/null 2>&1; then
      source_image="$candidate"
      break
    fi
  done
  if [[ -z "$source_image" ]]; then
    return 1
  fi

  warn "Docker registry metadata fetch failed; refreshing images from local base ${source_image}."
  docker tag "$source_image" "$base_image"
  docker build \
    -t bestseller-api:latest \
    -t bestseller-worker:latest \
    -t bestseller-scheduler:latest \
    -t bestseller-mcp:latest \
    -t bestseller-web:latest \
    -t bestseller-migrate:latest \
    -f - . <<EOF
FROM ${base_image}
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY config/ ./config/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY alembic.ini ./
ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
EOF
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
  # ⚠️ macOS 自带 bash 3.2：`set -u` 下展开**空数组** "${A[@]}" 会报
  # unbound variable。COMPOSE_FILES 现在默认就是空的（裸 docker compose，
  # 让 override 自动加载），所以必须用 ${A[@]+"${A[@]}"} 这个惯用法。
  # 不加这层保护脚本会在第一条 compose 命令上直接退出——本次改动实测踩到。
  compose() {
    $COMPOSE ${COMPOSE_FILES[@]+"${COMPOSE_FILES[@]}"} "$@" < /dev/null
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

  # Show SSD status —— 按**卷实际指向哪**判断，不按 COMPOSE_FILES 长度。
  # 改用 override(external:true) 之后 COMPOSE_FILES 是空的，按长度判断会谎报
  # "SSD not detected"，而数据其实一直在 SSD 上。
  local _pgdev
  _pgdev="$(docker volume inspect bestseller_pgdata --format '{{.Options.device}}' 2>/dev/null || true)"
  if [[ -n "$_pgdev" ]]; then
    ok "SSD volumes in use — pgdata → ${_pgdev}"
  else
    log "pgdata is a plain Docker named volume (not SSD-bound)"
  fi

  # ── Step 1: Build images（默认跳过）──────────────────────────────────────
  #
  # ⚠️ 这里曾经**无条件**跑 `compose build`，注释写着「没改动就是几秒 no-op」。
  # 那个假设是错的：Dockerfile 里有 `COPY src/ ./src/`，**只要动过源码那层缓存
  # 就失效**，后面全部重建（6 镜像 ×2.98GB，本机还会在 apt 层 OOM）。
  # 而源码是天天改的 —— 于是每次启动都变成全量重建，配合 Step 0 的 down，
  # 整个重建期间服务是停的。用户实测「部署怎么这么久」就是这段。
  #
  # src/config/data 都是活挂载（docker-compose.override.yml），**改代码不需要
  # rebuild**，起容器即可生效。只有这三类改动才真的需要重建：
  #   Dockerfile / pyproject.toml / uv.lock（依赖或镜像本身变了）
  # 下面按时间戳自动检测这三个文件，真变了才建；否则跳过。
  # 想强制全量重建：--build
  # 判据用**内容哈希**，不用 mtime。
  # ⚠️ 第一版按 mtime 比镜像新就重建，实测立刻误判：pyproject.toml 内容自
  # 6-05 起没变过，mtime 却是当天 09:52（被某个工具碰过一下）——于是又触发
  # 全量重建，卡在 apt 层。**文件被摸一下不等于依赖变了。**
  local _stamp_file="$ROOT_DIR/.docker-build-stamp"
  local _cur_hash=""
  local _f
  for _f in Dockerfile pyproject.toml uv.lock; do
    if [[ -f "$ROOT_DIR/$_f" ]]; then
      _cur_hash="${_cur_hash}$(shasum -a 256 "$ROOT_DIR/$_f" 2>/dev/null | cut -d' ' -f1)"
    fi
  done
  _cur_hash="$(printf '%s' "$_cur_hash" | shasum -a 256 | cut -d' ' -f1)"

  local _need_build=false
  local _build_reason=""
  if [[ "$FORCE_BUILD" == "true" ]]; then
    _need_build=true
    _build_reason="--build 强制"
  elif ! docker image inspect bestseller-worker:latest >/dev/null 2>&1; then
    _need_build=true
    _build_reason="镜像不存在"
  elif [[ ! -f "$_stamp_file" ]]; then
    # 首次引入 stamp：镜像在、依赖大概率没变，别为了建 stamp 而全量重建。
    _build_reason="首次记录依赖指纹（不重建）"
    printf '%s' "$_cur_hash" > "$_stamp_file"
  elif [[ "$(cat "$_stamp_file" 2>/dev/null)" != "$_cur_hash" ]]; then
    _need_build=true
    _build_reason="Dockerfile/pyproject/uv.lock 内容变了"
  fi

  if [[ "$_need_build" == "true" ]]; then
    log "需要重建镜像（${_build_reason}）..."
    local _build_args=(--profile migrate build)
    [[ "$FORCE_BUILD" == "true" ]] && _build_args+=(--no-cache)
    if ! compose "${_build_args[@]}"; then
      refresh_images_from_local_base || {
        err "Docker build failed and no local BestSeller image was available for fallback."
        exit 1
      }
    fi
    printf '%s' "$_cur_hash" > "$_stamp_file"
  else
    ok "跳过镜像构建：src/config/data 是活挂载，改代码无需重建。${_build_reason:+（$_build_reason）}"
    log "（依赖真变了会自动重建；强制重建用 --build）"
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

  # 活挂载自检：容器跑的到底是不是宿主机这份代码。
  # 2026-08-16 整批修复因为活挂载被 -f 关掉而一次都没运行过，症状酷似
  # 「Python 缓存导入」。这一步把那个假设变成每次启动都验的判断。
  if [[ -x "$ROOT_DIR/scripts/verify_live_code_mount.sh" ]]; then
    echo ""
    bash "$ROOT_DIR/scripts/verify_live_code_mount.sh" || \
      warn "活挂载自检未通过 —— 容器可能在跑镜像里的旧代码，见上面的修法。"
  fi

  # In foreground mode, tail all logs
  if [[ "$DETACH" == "false" ]]; then
    echo ""
    log "Tailing logs (Ctrl+C to detach, services keep running) ..."
    $COMPOSE ${COMPOSE_FILES[@]+"${COMPOSE_FILES[@]}"} logs -f --tail=50
  fi
}

main "$@"
