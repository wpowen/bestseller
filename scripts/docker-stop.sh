#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# BestSeller Docker — One-Click Stop
# =============================================================================
# Usage:
#   ./scripts/docker-stop.sh              # Stop all containers (keep data)
#   ./scripts/docker-stop.sh --purge      # Stop + remove volumes (delete all data)
#   ./scripts/docker-stop.sh --clean      # Stop + remove images + volumes
# =============================================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Ensure Docker tools are in PATH
export PATH="/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"

# ── Defaults ──────────────────────────────────────────────────────────────────
PURGE_VOLUMES=false
REMOVE_IMAGES=false
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
    --purge)  PURGE_VOLUMES=true ;;
    --clean)  PURGE_VOLUMES=true; REMOVE_IMAGES=true ;;
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

log()  { printf "${CYAN}[docker-stop]${NC} %s\n" "$1"; }
ok()   { printf "${GREEN}[docker-stop]${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}[docker-stop]${NC} %s\n" "$1"; }

# Prefer 'docker compose' (v2 plugin), fallback to 'docker-compose' (v1)
detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  else
    echo "docker-compose"
  fi
}

main() {
  local COMPOSE
  COMPOSE="$(detect_compose)"

  echo ""
  printf "${BOLD}Stopping BestSeller Docker Stack${NC}\n"
  echo "========================================"

  # Show current state
  log "Current service status:"
  $COMPOSE "${COMPOSE_FILES[@]}" ps 2>/dev/null || true
  echo ""

  # ── Step 1: Stop all containers ───────────────────────────────────────────
  log "Stopping all services ..."
  $COMPOSE "${COMPOSE_FILES[@]}" down --timeout 15

  ok "All containers stopped."

  # ── Step 2: Optionally remove volumes ─────────────────────────────────────
  if [[ "$PURGE_VOLUMES" == "true" ]]; then
    warn "Removing Docker volumes (all data will be deleted) ..."
    $COMPOSE "${COMPOSE_FILES[@]}" down -v --timeout 5 2>/dev/null || true

    # Also clean up named volumes explicitly
    for vol in pgdata redisdata output artifacts; do
      local full_name
      # docker compose prefixes volume names with the project name
      full_name="$(docker volume ls --format '{{.Name}}' | grep -E "bestseller.*${vol}" || true)"
      if [[ -n "$full_name" ]]; then
        docker volume rm "$full_name" 2>/dev/null || true
        warn "Removed volume: $full_name"
      fi
    done

    ok "All volumes removed."
  fi

  # ── Step 3: Optionally remove images ──────────────────────────────────────
  if [[ "$REMOVE_IMAGES" == "true" ]]; then
    warn "Removing Docker images ..."
    $COMPOSE "${COMPOSE_FILES[@]}" down --rmi local 2>/dev/null || true

    # Prune dangling images from build cache
    docker image prune -f --filter "label=com.docker.compose.project=bestseller" 2>/dev/null || true

    ok "Local images removed."
  fi

  # ── Summary ────────────────────────────────────────────────────────────────
  echo ""
  echo "========================================"
  printf "${GREEN}${BOLD} BestSeller Docker Stack stopped.${NC}\n"
  echo "========================================"
  echo ""

  if [[ "$PURGE_VOLUMES" == "true" ]]; then
    printf "  ${YELLOW}Volumes purged — database and Redis data deleted.${NC}\n"
  else
    printf "  Data volumes preserved. Use ${CYAN}--purge${NC} to delete all data.\n"
  fi

  if [[ "$REMOVE_IMAGES" == "true" ]]; then
    printf "  ${YELLOW}Local images removed. Next start will rebuild.${NC}\n"
  fi

  echo ""
  printf "  Restart:  ${CYAN}./scripts/docker-start.sh${NC}\n"
  echo ""
}

main "$@"
