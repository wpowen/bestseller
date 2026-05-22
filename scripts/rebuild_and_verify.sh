#!/usr/bin/env bash
set -euo pipefail

COMPOSE_BIN="${COMPOSE_BIN:-docker compose}"
WORKER_SERVICE="${WORKER_SERVICE:-worker}"
API_SERVICE="${API_SERVICE:-api}"
WORKER_CONTAINER="${WORKER_CONTAINER:-bestseller-worker-1}"

${COMPOSE_BIN} build "${WORKER_SERVICE}" "${API_SERVICE}"
${COMPOSE_BIN} up -d "${WORKER_SERVICE}" "${API_SERVICE}"

docker exec "${WORKER_CONTAINER}" python - <<'PY'
from bestseller.services.retention_safety_gate import evaluate_retention_safety
from bestseller.services.cast_compliance_gate import check_cast_compliance
from bestseller.services.signature_scene_critic import judge_signature_scene_semantics

assert evaluate_retention_safety
assert check_cast_compliance
assert judge_signature_scene_semantics
print("OK")
PY

docker exec "${WORKER_CONTAINER}" sh -lc '
if [ -d /app/tests/unit ]; then
  python -m pytest /app/tests/unit/test_retention_safety_gate.py -q --no-cov
else
  echo "tests not copied into image; import smoke check passed"
fi
'
