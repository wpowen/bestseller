#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"
PRIVATE_DIR="${REPO_ROOT}/.distillation_private"
REPORT_DIR="${PRIVATE_DIR}/reports"
LOG_PATH="${PRIVATE_DIR}/host_nvidia_glm_distillation.log"
TMUX_LOG_PATH="${PRIVATE_DIR}/host_nvidia_glm_distillation.tmux.log"
PID_PATH="${PRIVATE_DIR}/host_nvidia_glm_distillation.pid"
STATUS_PATH="${REPORT_DIR}/host_nvidia_glm_distillation_status.json"
RUNTIME_PROFILE_PATH="${PRIVATE_DIR}/host_nvidia_glm_runtime-profile.disabled.json"
TMUX_SESSION="${TMUX_SESSION:-bestseller-glm-distill}"

SOURCE_START="${SOURCE_START:-0001}"
SOURCE_END="${SOURCE_END:-9999}"
CHAPTER_WORKERS="${CHAPTER_WORKERS:-4}"
MAX_CHAPTER_CHARS="${MAX_CHAPTER_CHARS:-12000}"
CHAPTER_JOB_TIMEOUT_SECONDS="${CHAPTER_JOB_TIMEOUT_SECONDS:-180}"
SUPERVISOR_SLEEP_SECONDS="${SUPERVISOR_SLEEP_SECONDS:-300}"
NVIDIA_API_BASE="${NVIDIA_API_BASE:-${NIM_API_BASE:-https://integrate.api.nvidia.com/v1}}"
NVIDIA_LLM_MODEL="${NVIDIA_LLM_MODEL:-z-ai/glm-5.1}"
NVIDIA_SUMMARIZER_MODEL="${NVIDIA_SUMMARIZER_MODEL:-${NVIDIA_LLM_MODEL}}"
NVIDIA_SUMMARIZER_MAX_TOKENS="${NVIDIA_SUMMARIZER_MAX_TOKENS:-8192}"
NVIDIA_SUMMARIZER_TIMEOUT_SECONDS="${NVIDIA_SUMMARIZER_TIMEOUT_SECONDS:-180}"

mkdir -p "${PRIVATE_DIR}" "${REPORT_DIR}"

if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="$(command -v python3 || command -v python || true)"
fi
if [[ -z "${PYTHON}" ]]; then
  echo "error: python executable not found" >&2
  exit 1
fi

load_env() {
  cd "${REPO_ROOT}"
  set -a
  if [[ -f ".env" ]]; then
    # shellcheck disable=SC1091
    source ".env"
  fi
  if [[ -f ".env.local" ]]; then
    # shellcheck disable=SC1091
    source ".env.local"
  fi
  set +a

  if [[ -z "${NVIDIA_API_KEY:-}" && -n "${NIM_API_KEY:-}" ]]; then
    export NVIDIA_API_KEY="${NIM_API_KEY}"
    export BESTSELLER__LLM__SUMMARIZER__API_KEY_ENV="NIM_API_KEY"
  else
    export BESTSELLER__LLM__SUMMARIZER__API_KEY_ENV="NVIDIA_API_KEY"
  fi
  : "${NVIDIA_API_KEY:?Please set NVIDIA_API_KEY or NIM_API_KEY in .env/.env.local.}"

  export BESTSELLER_LLM_PROVIDER="nvidia"
  rm -f "${RUNTIME_PROFILE_PATH}"
  export BESTSELLER_LLM_RUNTIME_PROFILE_PATH="${RUNTIME_PROFILE_PATH}"
  export NVIDIA_API_KEY
  export NVIDIA_API_BASE
  export NVIDIA_LLM_MODEL
  export NVIDIA_SUMMARIZER_MODEL
  export NVIDIA_SUMMARIZER_MAX_TOKENS
  export BESTSELLER__LLM__PLANNER__MODEL="openai/${NVIDIA_LLM_MODEL}"
  export BESTSELLER__LLM__PLANNER__API_BASE="${NVIDIA_API_BASE}"
  export BESTSELLER__LLM__PLANNER__API_KEY_ENV="${BESTSELLER__LLM__SUMMARIZER__API_KEY_ENV}"
  export BESTSELLER__LLM__CRITIC__MODEL="openai/${NVIDIA_LLM_MODEL}"
  export BESTSELLER__LLM__CRITIC__API_BASE="${NVIDIA_API_BASE}"
  export BESTSELLER__LLM__CRITIC__API_KEY_ENV="${BESTSELLER__LLM__SUMMARIZER__API_KEY_ENV}"
  export BESTSELLER__LLM__SUMMARIZER__MODEL="openai/${NVIDIA_SUMMARIZER_MODEL}"
  export BESTSELLER__LLM__SUMMARIZER__API_BASE="${NVIDIA_API_BASE}"
  export BESTSELLER__LLM__SUMMARIZER__MAX_TOKENS="${NVIDIA_SUMMARIZER_MAX_TOKENS}"
  export BESTSELLER__LLM__SUMMARIZER__TIMEOUT_SECONDS="${NVIDIA_SUMMARIZER_TIMEOUT_SECONDS}"
}

ts() {
  date +"%Y-%m-%dT%H:%M:%S%:z"
}

pid_alive() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1
}

write_status() {
  local state="${1:-unknown}"
  local last_exit="${2:-0}"
  cd "${REPO_ROOT}"
  "${PYTHON}" - "${STATUS_PATH}" "${state}" "${last_exit}" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

status_path = Path(sys.argv[1])
state = sys.argv[2]
last_exit = int(sys.argv[3])
root = Path.cwd()
dist = root / "data" / "distillation"
sources = sorted([p for p in dist.glob("source-*") if p.is_dir()], key=lambda p: p.name)

def jsonl_count(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count

missing_genre = 0
empty_mechanism = 0
empty_material = 0
for pkg in sources:
    manifest_path = pkg / "source_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        manifest = {}
    bucket = manifest.get("distillation_genre_bucket")
    if not bucket or bucket == "distillation-genre-unclassified":
        missing_genre += 1
    if jsonl_count(pkg / "mechanism_candidates.jsonl") == 0:
        empty_mechanism += 1
    if jsonl_count(pkg / "material_entries.review.jsonl") == 0:
        empty_material += 1

aggregates = {}
for agg in sorted((dist / "aggregates").glob("*")):
    if not agg.is_dir():
        continue
    item = {}
    manifest_path = agg / "aggregate_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            item["source_count"] = manifest.get("source_count")
            item["maturity_score"] = manifest.get("maturity_score")
            item["maturity_status"] = manifest.get("maturity_status")
        except Exception:
            pass
    item["material_entries_review_rows"] = jsonl_count(agg / "material_entries.review.jsonl")
    item["material_entries_active_rows"] = jsonl_count(agg / "material_entries.active.jsonl")
    item["mechanism_rows"] = jsonl_count(agg / "mechanism_registry.jsonl")
    aggregates[agg.name] = item

done = bool(sources) and missing_genre == 0 and empty_mechanism == 0 and empty_material == 0
payload = {
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "state": state,
    "last_exit": last_exit,
    "done": done,
    "completion_criteria": {
        "all_sources_have_specific_genre_bucket": missing_genre == 0,
        "all_sources_have_mechanism_candidates": empty_mechanism == 0,
        "all_sources_have_material_entries_review": empty_material == 0,
    },
    "source_count": len(sources),
    "remaining": {
        "missing_or_unclassified_genre_bucket": missing_genre,
        "empty_mechanism_candidates": empty_mechanism,
        "empty_material_entries_review": empty_material,
    },
    "aggregates": aggregates,
}
status_path.parent.mkdir(parents=True, exist_ok=True)
status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
PY
}

run_once() {
  cd "${REPO_ROOT}"
  "${PYTHON}" -u scripts/distillation/run_full_auto_distillation.py \
    --repo-root . \
    --source-start "${SOURCE_START}" \
    --source-end "${SOURCE_END}" \
    --resume \
    --single-pass \
    --chapter-workers "${CHAPTER_WORKERS}" \
    --chapter-job-timeout-seconds "${CHAPTER_JOB_TIMEOUT_SECONDS}" \
    --import-mode none \
    --max-chapter-chars "${MAX_CHAPTER_CHARS}" \
    --refresh-missing-craft-observations \
    --backfill-empty-tail-artifacts
}

run_supervisor() {
  load_env
  echo "$$" >"${PID_PATH}"
  trap 'rm -f "${PID_PATH}"; write_status stopped 130 >/dev/null || true; exit 130' INT TERM
  echo "$(ts) [supervisor] start provider=nvidia model=${NVIDIA_LLM_MODEL} source=${SOURCE_START}-${SOURCE_END} workers=${CHAPTER_WORKERS}"
  echo "$(ts) [supervisor] runtime_profile_path=${BESTSELLER_LLM_RUNTIME_PROFILE_PATH} api_base=${NVIDIA_API_BASE} summarizer_model=${BESTSELLER__LLM__SUMMARIZER__MODEL}"

  while true; do
    local status_json
    status_json="$(write_status running 0)"
    if "${PYTHON}" - "${status_json}" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
raise SystemExit(0 if payload.get("done") else 1)
PY
    then
      echo "$(ts) [supervisor] complete"
      write_status complete 0 >/dev/null
      rm -f "${PID_PATH}"
      return 0
    fi

    echo "$(ts) [supervisor] run pass"
    set +e
    run_once
    local code=$?
    set -e
    write_status pass_complete "${code}" >/dev/null || true
    if [[ "${code}" -ne 0 ]]; then
      echo "$(ts) [supervisor] pass exit=${code}; sleeping ${SUPERVISOR_SLEEP_SECONDS}s"
    else
      echo "$(ts) [supervisor] pass exit=0; checking remaining work after ${SUPERVISOR_SLEEP_SECONDS}s"
    fi
    sleep "${SUPERVISOR_SLEEP_SECONDS}"
  done
}

start_supervisor() {
  if [[ -f "${PID_PATH}" ]]; then
    local existing
    existing="$(cat "${PID_PATH}" 2>/dev/null || true)"
    if pid_alive "${existing}"; then
      echo "already running pid=${existing}"
      exit 0
    fi
    rm -f "${PID_PATH}"
  fi
  cd "${REPO_ROOT}"
  if command -v tmux >/dev/null 2>&1; then
    if tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
      echo "already running tmux_session=${TMUX_SESSION}"
      echo "log=${TMUX_LOG_PATH}"
      echo "status=${STATUS_PATH}"
      exit 0
    fi
    tmux new-session -d -s "${TMUX_SESSION}" -c "${REPO_ROOT}" \
      "/bin/bash '$0' run >>'${TMUX_LOG_PATH}' 2>&1"
    echo "started tmux_session=${TMUX_SESSION}"
    echo "log=${TMUX_LOG_PATH}"
    echo "status=${STATUS_PATH}"
    exit 0
  fi
  nohup "$0" run >>"${LOG_PATH}" 2>&1 &
  local pid=$!
  echo "${pid}" >"${PID_PATH}"
  echo "started pid=${pid}"
  echo "log=${LOG_PATH}"
  echo "status=${STATUS_PATH}"
}

stop_supervisor() {
  if [[ ! -f "${PID_PATH}" ]]; then
    echo "not running"
    exit 0
  fi
  local pid
  pid="$(cat "${PID_PATH}")"
  if pid_alive "${pid}"; then
    kill "${pid}"
    echo "stopped pid=${pid}"
  else
    echo "stale pid=${pid}"
  fi
  if command -v tmux >/dev/null 2>&1 && tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
    tmux kill-session -t "${TMUX_SESSION}"
    echo "stopped tmux_session=${TMUX_SESSION}"
  fi
  rm -f "${PID_PATH}"
}

case "${1:-start}" in
  start)
    start_supervisor
    ;;
  run)
    run_supervisor
    ;;
  stop)
    stop_supervisor
    ;;
  status)
    write_status status 0
    ;;
  *)
    echo "usage: $0 [start|run|stop|status]" >&2
    exit 2
    ;;
esac
