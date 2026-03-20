#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
ENV_FILE="$RUNTIME_DIR/dev.env"

CONTAINER_NAME="${BESTSELLER_CONTAINER_NAME:-bestseller-dev-postgres}"
DB_VOLUME="${BESTSELLER_DB_VOLUME:-bestseller-pgdata}"
PURGE="${1:-}"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  docker rm -f "$CONTAINER_NAME" >/dev/null
  echo "Removed container: $CONTAINER_NAME"
else
  echo "Container not found: $CONTAINER_NAME"
fi

rm -f "$ENV_FILE"
rmdir "$RUNTIME_DIR" 2>/dev/null || true

if [[ "$PURGE" == "--purge" ]]; then
  docker volume rm "$DB_VOLUME" >/dev/null 2>&1 || true
  echo "Removed volume: $DB_VOLUME"
fi

echo "BestSeller development environment stopped."
