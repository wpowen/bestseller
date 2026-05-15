#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/../.."
PYTHON="${REPO_ROOT}/.venv/bin/python"
LOG_DIR="${REPO_ROOT}/.distillation_private"
LOG1="${LOG_DIR}/full_auto_distill_two_stage_stage1.log"
LOG2="${LOG_DIR}/full_auto_distill_two_stage_stage2.log"

mkdir -p "${LOG_DIR}"
cd "${REPO_ROOT}"

COMMON_ARGS=(
  --repo-root .
  --source-start 0001
  --source-end 2800
  --resume
  --chapter-workers 8
  --import-mode none
  --skip-genre-classify
  --max-chapter-chars 12000
)

ts() {
  date +"%Y-%m-%dT%H:%M:%S%:z"
}

echo "$(ts) [stage1] start" | tee -a "${LOG1}"
"${PYTHON}" -u scripts/distillation/run_full_auto_distillation.py "${COMMON_ARGS[@]}" 2>&1 | tee -a "${LOG1}"
status=${PIPESTATUS[0]}
if [[ ${status} -ne 0 ]]; then
  echo "$(ts) [stage1] failed exit=${status}" | tee -a "${LOG1}"
  exit ${status}
fi

echo "$(ts) [stage2] start" | tee -a "${LOG2}"
"${PYTHON}" -u scripts/distillation/run_full_auto_distillation.py "${COMMON_ARGS[@]}" --refresh-missing-craft-observations 2>&1 | tee -a "${LOG2}"
status=${PIPESTATUS[0]}
if [[ ${status} -ne 0 ]]; then
  echo "$(ts) [stage2] failed exit=${status}" | tee -a "${LOG2}"
fi

echo "$(ts) [stage2] done exit=${status}" | tee -a "${LOG2}"
exit ${status}
