#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/../.."
PYTHON="${REPO_ROOT}/.venv/bin/python"
LOG_DIR="${REPO_ROOT}/.distillation_private"

LOG1="${LOG_DIR}/full_auto_distill_nvidia_deepseek_stage1.log"
LOG2="${LOG_DIR}/full_auto_distill_nvidia_deepseek_stage2.log"

mkdir -p "${LOG_DIR}"
cd "${REPO_ROOT}"

if [[ ! -x "$PYTHON" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
  else
    echo "error: python executable not found" >&2
    exit 1
  fi
fi


DEEPSEEK_MODEL="${NVIDIA_LLM_MODEL:-${DEEPSEEK_MODEL:-deepseek-ai/deepseek-v4-pro}}"
DEEPSEEK_SUMMARIZER_MODEL="${NVIDIA_SUMMARIZER_MODEL:-${DEEPSEEK_MODEL}}"
DEEPSEEK_API_BASE="${NVIDIA_API_BASE:-${NIM_API_BASE:-https://integrate.api.nvidia.com/v1}}"
DEEPSEEK_SUMMARIZER_MAX_TOKENS="${BESTSELLER__LLM__SUMMARIZER__MAX_TOKENS:-${NVIDIA_SUMMARIZER_MAX_TOKENS:-8192}}"
DEEPSEEK_SUMMARIZER_TIMEOUT_SECONDS="${BESTSELLER__LLM__SUMMARIZER__TIMEOUT_SECONDS:-120}"

SUMMARIZER_KEY_ENV_NAME="NVIDIA_API_KEY"
if [[ -z "${NVIDIA_API_KEY:-}" && -n "${NIM_API_KEY:-}" ]]; then
  NVIDIA_API_KEY="${NIM_API_KEY}"
  SUMMARIZER_KEY_ENV_NAME="NIM_API_KEY"
fi
: "${NVIDIA_API_KEY:?Please set NVIDIA_API_KEY or NIM_API_KEY for NVIDIA API access.}"

export BESTSELLER_LLM_PROVIDER="nvidia"
export NVIDIA_API_KEY
export NVIDIA_API_BASE="${DEEPSEEK_API_BASE}"
export NVIDIA_LLM_MODEL="${DEEPSEEK_MODEL}"
export NVIDIA_SUMMARIZER_MAX_TOKENS="${DEEPSEEK_SUMMARIZER_MAX_TOKENS}"
export BESTSELLER__LLM__SUMMARIZER__MODEL="${DEEPSEEK_SUMMARIZER_MODEL}"
export BESTSELLER__LLM__SUMMARIZER__API_BASE="${NVIDIA_API_BASE}"
export BESTSELLER__LLM__SUMMARIZER__API_KEY_ENV="${SUMMARIZER_KEY_ENV_NAME}"
export BESTSELLER__LLM__SUMMARIZER__MAX_TOKENS="${DEEPSEEK_SUMMARIZER_MAX_TOKENS}"
export BESTSELLER__LLM__SUMMARIZER__TIMEOUT_SECONDS="${DEEPSEEK_SUMMARIZER_TIMEOUT_SECONDS}"

SOURCE_START="${SOURCE_START:-0001}"
SOURCE_END="${SOURCE_END:-2800}"
CHAPTER_WORKERS="${CHAPTER_WORKERS:-8}"
SOURCE_LIMIT="${SOURCE_LIMIT:-0}"
MAX_CHAPTER_CHARS="${MAX_CHAPTER_CHARS:-12000}"

ARGS=("$@")
for ((i=0; i<${#ARGS[@]}; i++)); do
  case "${ARGS[$i]}" in
    --source-start)
      ((i++))
      SOURCE_START="${ARGS[$i]}"
      ;;
    --source-end)
      ((i++))
      SOURCE_END="${ARGS[$i]}"
      ;;
    --chapter-workers)
      ((i++))
      CHAPTER_WORKERS="${ARGS[$i]}"
      ;;
    --source-limit)
      ((i++))
      SOURCE_LIMIT="${ARGS[$i]}"
      ;;
    --max-chapter-chars)
      ((i++))
      MAX_CHAPTER_CHARS="${ARGS[$i]}"
      ;;
  esac
done

COMMON_ARGS=(
  --repo-root .
  --source-start "${SOURCE_START}"
  --source-end "${SOURCE_END}"
  --resume
  --chapter-workers "${CHAPTER_WORKERS}"
  --import-mode none
  --skip-genre-classify
  --max-chapter-chars "${MAX_CHAPTER_CHARS}"
)

if [[ "${SOURCE_LIMIT}" != "0" ]]; then
  COMMON_ARGS+=(--source-limit "${SOURCE_LIMIT}")
fi

COMMON_ARGS+=("${ARGS[@]}")

ts() {
  date +"%Y-%m-%dT%H:%M:%S%:z"
}

echo "$(ts) [config] distillation provider=nvidia model=${NVIDIA_LLM_MODEL} max_tokens=${DEEPSEEK_SUMMARIZER_MAX_TOKENS} source=${SOURCE_START}-${SOURCE_END} workers=${CHAPTER_WORKERS}" | tee -a "${LOG1}" "${LOG2}"

echo "$(ts) [stage1] start" | tee -a "${LOG1}"
"${PYTHON}" -u scripts/distillation/run_full_auto_distillation.py "${COMMON_ARGS[@]}" 2>&1 | tee -a "${LOG1}"
status=${PIPESTATUS[0]}
if [[ ${status} -ne 0 ]]; then
  echo "$(ts) [stage1] failed exit=${status}" | tee -a "${LOG1}"
  exit "${status}"
fi

echo "$(ts) [stage2] start" | tee -a "${LOG2}"
STAGE2_ARGS=(
  "${COMMON_ARGS[@]}"
  --refresh-missing-craft-observations
)
"${PYTHON}" -u scripts/distillation/run_full_auto_distillation.py "${STAGE2_ARGS[@]}" 2>&1 | tee -a "${LOG2}"
status=${PIPESTATUS[0]}
if [[ ${status} -ne 0 ]]; then
  echo "$(ts) [stage2] failed exit=${status}" | tee -a "${LOG2}"
  exit "${status}"
fi

echo "$(ts) [stage2] done exit=${status}" | tee -a "${LOG2}"
exit "${status}"
