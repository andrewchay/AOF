#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <INPUT_PATH> [TOPIC] [MAX_ITERATIONS]"
  echo "Example: $0 /Users/chaihao/LLM/AOF/tools/data_adapter/samples/TEST_DATA_miyoushe_post_metrics_case.sql miyoushe_sql 4"
  exit 2
fi

INPUT_PATH="$1"
TOPIC="${2:-}"
MAX_ITERATIONS="${3:-4}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_AOF_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
AOF_ROOT="${AOF_ROOT:-$DEFAULT_AOF_ROOT}"
PY="${AOF_PY:-$AOF_ROOT/.venv/bin/python}"
SCRIPT="$AOF_ROOT/tools/ontology_factory/build_testdata_ontology_factory.py"

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

if [[ -z "${LLM_API_KEY:-}" ]]; then
  echo "[warn] LLM_API_KEY not set. Will build ontology only (skip align)."
  if [[ -n "$TOPIC" ]]; then
    "$PY" "$SCRIPT" --input "$INPUT_PATH" --topic "$TOPIC" --max-iterations "$MAX_ITERATIONS" --skip-align
  else
    "$PY" "$SCRIPT" --input "$INPUT_PATH" --max-iterations "$MAX_ITERATIONS" --skip-align
  fi
  exit 0
fi

if [[ -n "$TOPIC" ]]; then
  "$PY" "$SCRIPT" --input "$INPUT_PATH" --topic "$TOPIC" --max-iterations "$MAX_ITERATIONS"
else
  "$PY" "$SCRIPT" --input "$INPUT_PATH" --max-iterations "$MAX_ITERATIONS"
fi
