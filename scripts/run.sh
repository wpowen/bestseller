#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.runtime/dev.env"
CLI_BIN="$ROOT_DIR/.venv/bin/bestseller"

if [[ ! -x "$CLI_BIN" ]]; then
  echo "BestSeller CLI is not installed. Run ./scripts/start.sh first." >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

exec "$CLI_BIN" "$@"
