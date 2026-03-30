#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_AOF_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
AOF_ROOT="${AOF_ROOT:-$DEFAULT_AOF_ROOT}"
PY="${AOF_PY:-$AOF_ROOT/.venv/bin/python}"
APP="services.semantic_middle_layer_api.app:app"

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

exec "$PY" -m uvicorn "$APP" --host 127.0.0.1 --port 8787
