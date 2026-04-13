#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
ENV_FILE="$RUNTIME_DIR/dev.env"
INSTALL_STAMP_FILE="$RUNTIME_DIR/install.stamp"
VENV_DIR="$ROOT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_BESTSELLER="$VENV_DIR/bin/bestseller"
VENV_ALEMBIC="$VENV_DIR/bin/alembic"

CONTAINER_NAME="${BESTSELLER_CONTAINER_NAME:-bestseller-dev-postgres}"
DB_PORT="${BESTSELLER_DB_PORT:-55432}"
DB_NAME="${BESTSELLER_DB_NAME:-bestseller}"
DB_USER="${BESTSELLER_DB_USER:-bestseller}"
DB_PASSWORD="${BESTSELLER_DB_PASSWORD:-bestseller}"
DB_VOLUME="${BESTSELLER_DB_VOLUME:-bestseller-pgdata}"
PG_IMAGE="${BESTSELLER_PG_IMAGE:-ankane/pgvector:latest}"
INSTALL_MODE="${BESTSELLER_INSTALL_MODE:-dev}"

log() {
  printf '[start] %s\n' "$1"
}

detect_llm_mock() {
  if [[ -n "${BESTSELLER_LLM_MOCK:-}" ]]; then
    echo "$BESTSELLER_LLM_MOCK"
    return
  fi

  for key in ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY GEMINI_API_KEY AZURE_API_KEY COHERE_API_KEY GROQ_API_KEY TOGETHERAI_API_KEY HUGGINGFACE_API_KEY; do
    if [[ -n "${!key:-}" ]]; then
      echo false
      return
    fi
  done

  for path in "$ROOT_DIR/.env" "$ROOT_DIR/.env.local"; do
    if [[ ! -f "$path" ]]; then
      continue
    fi
    if grep -Eq '^(ANTHROPIC_API_KEY|OPENAI_API_KEY|GOOGLE_API_KEY|GEMINI_API_KEY|AZURE_API_KEY|COHERE_API_KEY|GROQ_API_KEY|TOGETHERAI_API_KEY|HUGGINGFACE_API_KEY)=.+' "$path"; then
      echo false
      return
    fi
  done

  echo true
}

LLM_MOCK="$(detect_llm_mock)"

detect_llm_provider() {
  if [[ -n "${BESTSELLER_LLM_PROVIDER:-}" ]]; then
    echo "$BESTSELLER_LLM_PROVIDER"
    return
  fi

  for path in "$ROOT_DIR/.env" "$ROOT_DIR/.env.local"; do
    if [[ ! -f "$path" ]]; then
      continue
    fi
    local configured_provider
    configured_provider="$(grep -E '^BESTSELLER_LLM_PROVIDER=' "$path" | tail -n 1 | cut -d= -f2- || true)"
    if [[ -n "$configured_provider" ]]; then
      echo "$configured_provider"
      return
    fi
  done

  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    echo anthropic
    return
  fi
  if [[ -n "${GEMINI_API_KEY:-}" || -n "${GOOGLE_API_KEY:-}" ]]; then
    echo gemini
    return
  fi
  if [[ -n "${MINIMAX_API_KEY:-}" ]]; then
    echo minimax
    return
  fi

  for path in "$ROOT_DIR/.env" "$ROOT_DIR/.env.local"; do
    if [[ ! -f "$path" ]]; then
      continue
    fi
    if grep -Eq '^ANTHROPIC_API_KEY=.+' "$path"; then
      echo anthropic
      return
    fi
    if grep -Eq '^(GEMINI_API_KEY|GOOGLE_API_KEY)=.+' "$path"; then
      echo gemini
      return
    fi
    if grep -Eq '^MINIMAX_API_KEY=.+' "$path"; then
      echo minimax
      return
    fi
  done

  echo default
}

detect_gemini_key_env_name() {
  if [[ -n "${GOOGLE_API_KEY:-}" ]]; then
    echo GOOGLE_API_KEY
    return
  fi
  if [[ -n "${GEMINI_API_KEY:-}" ]]; then
    echo GEMINI_API_KEY
    return
  fi

  for path in "$ROOT_DIR/.env" "$ROOT_DIR/.env.local"; do
    if [[ ! -f "$path" ]]; then
      continue
    fi
    if grep -Eq '^GOOGLE_API_KEY=.+' "$path"; then
      echo GOOGLE_API_KEY
      return
    fi
    if grep -Eq '^GEMINI_API_KEY=.+' "$path"; then
      echo GEMINI_API_KEY
      return
    fi
  done

  echo GEMINI_API_KEY
}

LLM_PROVIDER="$(detect_llm_provider)"
GEMINI_KEY_ENV_NAME="$(detect_gemini_key_env_name)"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

resolve_install_extras() {
  local extras
  if [[ "$INSTALL_MODE" == "runtime" ]]; then
    extras="export"
  else
    extras="dev,export"
  fi
  if [[ "$LLM_MOCK" == "false" ]]; then
    extras="$extras,llm,cloud"
  fi
  echo "$extras"
}

compute_install_stamp() {
  local python_bin="$1"
  local extras="$2"

  {
    printf 'install_mode=%s\n' "$INSTALL_MODE"
    printf 'llm_mock=%s\n' "$LLM_MOCK"
    printf 'llm_provider=%s\n' "$LLM_PROVIDER"
    printf 'python=%s\n' "$python_bin"
    printf 'extras=%s\n' "$extras"
    if [[ -f "$ROOT_DIR/pyproject.toml" ]]; then
      shasum "$ROOT_DIR/pyproject.toml"
    fi
    if [[ -f "$ROOT_DIR/uv.lock" ]]; then
      shasum "$ROOT_DIR/uv.lock"
    fi
  } | shasum | awk '{print $1}'
}

detect_python() {
  if [[ -n "${BESTSELLER_PYTHON:-}" ]]; then
    echo "$BESTSELLER_PYTHON"
    return
  fi
  if [[ -x /opt/homebrew/bin/python3 ]]; then
    echo /opt/homebrew/bin/python3
    return
  fi
  if command -v python3.11 >/dev/null 2>&1; then
    echo python3.11
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo python3
    return
  fi
  echo python
}

wait_for_postgres() {
  until docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; do
    sleep 1
  done
}

write_runtime_env() {
  mkdir -p "$RUNTIME_DIR"
  cat >"$ENV_FILE" <<EOF
export BESTSELLER__DATABASE__URL='postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@localhost:${DB_PORT}/${DB_NAME}'
export BESTSELLER__LLM__MOCK='${LLM_MOCK}'
export BESTSELLER_LLM_PROVIDER='${LLM_PROVIDER}'
EOF

  if [[ "$LLM_PROVIDER" == "gemini" ]]; then
    cat >>"$ENV_FILE" <<EOF
export BESTSELLER__LLM__PLANNER__MODEL='openai/gemini-2.5-flash'
export BESTSELLER__LLM__PLANNER__API_BASE='https://generativelanguage.googleapis.com/v1beta/openai/'
export BESTSELLER__LLM__PLANNER__API_KEY_ENV='${GEMINI_KEY_ENV_NAME}'
export BESTSELLER__LLM__PLANNER__N_CANDIDATES='1'
export BESTSELLER__LLM__WRITER__MODEL='openai/gemini-2.5-flash'
export BESTSELLER__LLM__WRITER__API_BASE='https://generativelanguage.googleapis.com/v1beta/openai/'
export BESTSELLER__LLM__WRITER__API_KEY_ENV='${GEMINI_KEY_ENV_NAME}'
export BESTSELLER__LLM__WRITER__STREAM='false'
export BESTSELLER__LLM__CRITIC__MODEL='openai/gemini-2.5-flash'
export BESTSELLER__LLM__CRITIC__API_BASE='https://generativelanguage.googleapis.com/v1beta/openai/'
export BESTSELLER__LLM__CRITIC__API_KEY_ENV='${GEMINI_KEY_ENV_NAME}'
export BESTSELLER__LLM__SUMMARIZER__MODEL='openai/gemini-2.5-flash'
export BESTSELLER__LLM__SUMMARIZER__API_BASE='https://generativelanguage.googleapis.com/v1beta/openai/'
export BESTSELLER__LLM__SUMMARIZER__API_KEY_ENV='${GEMINI_KEY_ENV_NAME}'
export BESTSELLER__LLM__EDITOR__MODEL='openai/gemini-2.5-flash'
export BESTSELLER__LLM__EDITOR__API_BASE='https://generativelanguage.googleapis.com/v1beta/openai/'
export BESTSELLER__LLM__EDITOR__API_KEY_ENV='${GEMINI_KEY_ENV_NAME}'
EOF
  fi

  if [[ "$LLM_PROVIDER" == "minimax" ]]; then
    cat >>"$ENV_FILE" <<EOF
export BESTSELLER__LLM__PLANNER__MODEL='openai/MiniMax-M2.7'
export BESTSELLER__LLM__PLANNER__API_BASE='https://api.minimaxi.com/v1'
export BESTSELLER__LLM__PLANNER__API_KEY_ENV='MINIMAX_API_KEY'
export BESTSELLER__LLM__WRITER__MODEL='openai/MiniMax-M2.7'
export BESTSELLER__LLM__WRITER__MODEL_OVERRIDE='openai/MiniMax-M2.7'
export BESTSELLER__LLM__WRITER__API_BASE='https://api.minimaxi.com/v1'
export BESTSELLER__LLM__WRITER__API_KEY_ENV='MINIMAX_API_KEY'
export BESTSELLER__LLM__WRITER__STREAM='false'
export BESTSELLER__LLM__CRITIC__MODEL='openai/MiniMax-M2.7'
export BESTSELLER__LLM__CRITIC__API_BASE='https://api.minimaxi.com/v1'
export BESTSELLER__LLM__CRITIC__API_KEY_ENV='MINIMAX_API_KEY'
export BESTSELLER__LLM__SUMMARIZER__MODEL='openai/MiniMax-M2.7'
export BESTSELLER__LLM__SUMMARIZER__API_BASE='https://api.minimaxi.com/v1'
export BESTSELLER__LLM__SUMMARIZER__API_KEY_ENV='MINIMAX_API_KEY'
export BESTSELLER__LLM__EDITOR__MODEL='openai/MiniMax-M2.7'
export BESTSELLER__LLM__EDITOR__API_BASE='https://api.minimaxi.com/v1'
export BESTSELLER__LLM__EDITOR__API_KEY_ENV='MINIMAX_API_KEY'
EOF
  fi

  if [[ "$LLM_PROVIDER" == "minimax-quality" ]]; then
    # Mixed preset: MiniMax for planner/critic/summarizer, stronger writer/editor
    # Requires both MINIMAX_API_KEY and ANTHROPIC_API_KEY (or another strong-model key)
    cat >>"$ENV_FILE" <<EOF
export BESTSELLER__LLM__PLANNER__MODEL='openai/MiniMax-M2.7'
export BESTSELLER__LLM__PLANNER__API_BASE='https://api.minimaxi.com/v1'
export BESTSELLER__LLM__PLANNER__API_KEY_ENV='MINIMAX_API_KEY'
export BESTSELLER__LLM__WRITER__MODEL='anthropic/claude-sonnet-4-5'
export BESTSELLER__LLM__WRITER__MODEL_OVERRIDE='anthropic/claude-opus-4-5'
export BESTSELLER__LLM__WRITER__STREAM='true'
export BESTSELLER__LLM__CRITIC__MODEL='openai/MiniMax-M2.7'
export BESTSELLER__LLM__CRITIC__API_BASE='https://api.minimaxi.com/v1'
export BESTSELLER__LLM__CRITIC__API_KEY_ENV='MINIMAX_API_KEY'
export BESTSELLER__LLM__SUMMARIZER__MODEL='openai/MiniMax-M2.7'
export BESTSELLER__LLM__SUMMARIZER__API_BASE='https://api.minimaxi.com/v1'
export BESTSELLER__LLM__SUMMARIZER__API_KEY_ENV='MINIMAX_API_KEY'
export BESTSELLER__LLM__EDITOR__MODEL='anthropic/claude-sonnet-4-5'
EOF
  fi
}

ensure_virtualenv() {
  local python_bin
  local extras
  local install_stamp
  local previous_stamp

  python_bin="$(detect_python)"
  extras="$(resolve_install_extras)"
  mkdir -p "$RUNTIME_DIR"

  if [[ ! -x "$VENV_PYTHON" ]]; then
    log "Creating virtualenv with ${python_bin}."
    uv venv --python "$python_bin" "$VENV_DIR"
  fi

  if [[ "${BESTSELLER_SKIP_INSTALL:-false}" == "true" ]]; then
    log "Skipping dependency install because BESTSELLER_SKIP_INSTALL=true."
    return
  fi

  install_stamp="$(compute_install_stamp "$python_bin" "$extras")"
  previous_stamp=""
  if [[ -f "$INSTALL_STAMP_FILE" ]]; then
    previous_stamp="$(<"$INSTALL_STAMP_FILE")"
  fi

  if [[ "${BESTSELLER_FORCE_INSTALL:-false}" != "true" ]] && [[ "$install_stamp" == "$previous_stamp" ]]; then
    log "Dependencies are up to date; skipping install (.[$extras])."
    return
  fi

  log "Installing dependencies (.[$extras]). First run with real LLM support can take several minutes."
  uv pip install --quiet --python "$VENV_PYTHON" -e ".[${extras}]"
  printf '%s\n' "$install_stamp" >"$INSTALL_STAMP_FILE"
}

start_postgres() {
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    return
  fi
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    docker start "$CONTAINER_NAME" >/dev/null
    return
  fi
  docker run -d \
    --name "$CONTAINER_NAME" \
    -e POSTGRES_USER="$DB_USER" \
    -e POSTGRES_PASSWORD="$DB_PASSWORD" \
    -e POSTGRES_DB="$DB_NAME" \
    -p "${DB_PORT}:5432" \
    -v "${DB_VOLUME}:/var/lib/postgresql/data" \
    "$PG_IMAGE" >/dev/null
}

main() {
  require_command docker
  require_command uv

  log "Preparing Python environment."
  ensure_virtualenv
  log "Ensuring PostgreSQL container is running."
  start_postgres
  log "Waiting for PostgreSQL readiness."
  wait_for_postgres
  log "Writing runtime environment."
  write_runtime_env

  # shellcheck disable=SC1090
  source "$ENV_FILE"
  log "Applying database migrations."
  "$VENV_ALEMBIC" upgrade head

  echo "BestSeller development environment is ready."
  echo "Container: $CONTAINER_NAME"
  echo "Database URL: $BESTSELLER__DATABASE__URL"
  echo "LLM mock mode: $BESTSELLER__LLM__MOCK"
  echo "LLM provider preset: $BESTSELLER_LLM_PROVIDER"
  echo "Model config:"
  echo "  Planner:    ${BESTSELLER__LLM__PLANNER__MODEL:-<default.yaml>}"
  echo "  Writer:     ${BESTSELLER__LLM__WRITER__MODEL:-<default.yaml>} (override: ${BESTSELLER__LLM__WRITER__MODEL_OVERRIDE:-none})"
  echo "  Critic:     ${BESTSELLER__LLM__CRITIC__MODEL:-<default.yaml>}"
  echo "  Summarizer: ${BESTSELLER__LLM__SUMMARIZER__MODEL:-<default.yaml>}"
  echo "  Editor:     ${BESTSELLER__LLM__EDITOR__MODEL:-<default.yaml>}"
  echo "CLI wrapper: $ROOT_DIR/scripts/run.sh"
  echo "Web Studio: $ROOT_DIR/studio.sh"
  echo "Verification: $ROOT_DIR/scripts/verify.sh"
  "$VENV_BESTSELLER" status

  local studio_port="${BESTSELLER_STUDIO_PORT:-8787}"
  local pids
  pids="$(lsof -ti :"$studio_port" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    log "Killing existing process on port ${studio_port}."
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 0.5
  fi

  log "Starting Web Studio on port ${studio_port}."
  exec "$ROOT_DIR/scripts/run.sh" ui serve --open-browser --port "$studio_port"
}

main "$@"
