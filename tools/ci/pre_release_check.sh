#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PY="${AOF_PY:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

echo "[1/6] Shell syntax check"
bash -n tools/middle_layer/run_semantic_middle_layer.sh
bash -n services/semantic_middle_layer_api/run_api.sh

echo "[2/6] Python compile check"
"$PY" -m py_compile services/semantic_middle_layer_api/app.py
"$PY" -m py_compile tools/middle_layer/build_semantic_middle_layer.py

echo "[3/6] Governance release gate"
GOV_TOPIC="${AOF_GOVERNANCE_TOPIC:-}"
if [[ -n "$GOV_TOPIC" ]]; then
  echo "[info] governance scope topic=$GOV_TOPIC"
  "$PY" tools/governance/validate_release_gate.py --root data/governance --strict --topic "$GOV_TOPIC"
else
  "$PY" tools/governance/validate_release_gate.py --root data/governance --strict
fi

echo "[4/6] Lint check"
if "$PY" -c "import ruff" >/dev/null 2>&1; then
  "$PY" -m ruff check .
else
  echo "[warn] ruff not installed, skip lint"
fi

echo "[5/6] Unit/integration/e2e tests"
"$PY" -m pytest -v

echo "[6/6] API routes sanity check"
ROUTE_COUNT="$("$PY" - <<'PY'
from services.semantic_middle_layer_api.app import app
print(len([r for r in app.routes if hasattr(r, "methods")]))
PY
)"
echo "[ok] FastAPI routes: ${ROUTE_COUNT}"

echo "[done] pre-release check passed"
