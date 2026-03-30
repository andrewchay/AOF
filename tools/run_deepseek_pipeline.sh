#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <SPEC_JSON_PATH> <DATA_PATH> [RESULT_TAG]"
  echo "Example: $0 ./aof_spec.example.json ./samples/minimal.txt demo1"
  exit 2
fi

SPEC_PATH="$1"
DATA_PATH="$2"
RESULT_TAG="${3:-run}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_AOF_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
AOF_ROOT="${AOF_ROOT:-$DEFAULT_AOF_ROOT}"
PY="${AOF_PY:-$AOF_ROOT/.venv/bin/python}"

: "${LLM_API_KEY:?LLM_API_KEY is required}"
: "${LLM_PROVIDER:=custom}"
: "${LLM_MODEL:=deepseek/deepseek-chat}"
: "${LLM_ENDPOINT:=https://api.deepseek.com/v1}"
: "${EMBEDDING_PROVIDER:=custom}"
: "${EMBEDDING_MODEL:=deepseek/deepseek-embedding}"
: "${EMBEDDING_ENDPOINT:=https://api.deepseek.com/v1}"
: "${COGNEE_SKIP_CONNECTION_TEST:=true}"
: "${AOF_USE_MOCK_EMBEDDING:=true}"

export LLM_PROVIDER LLM_MODEL LLM_ENDPOINT
export EMBEDDING_PROVIDER EMBEDDING_MODEL EMBEDDING_ENDPOINT
export COGNEE_SKIP_CONNECTION_TEST
if [[ "${AOF_USE_MOCK_EMBEDDING}" == "true" ]]; then
  export MOCK_EMBEDDING="true"
  echo "[config] MOCK_EMBEDDING=true (default safe mode)"
else
  unset MOCK_EMBEDDING
  echo "[config] MOCK_EMBEDDING=false (real embedding mode)"
fi

echo "[1/3] doctor"
"$PY" "$AOF_ROOT/aof_doctor.py" --spec "$SPEC_PATH" --require-api-key

echo "[2/3] add"
"$PY" "$AOF_ROOT/aof_add.py" \
  --spec "$SPEC_PATH" \
  --data-path "$DATA_PATH" \
  --skip-preflight \
  --result-file "$AOF_ROOT/logs/aof_add_result.${RESULT_TAG}.json"

echo "[3/3] cognify"
"$PY" "$AOF_ROOT/aof_run.py" \
  --spec "$SPEC_PATH" \
  --run-cognify \
  --skip-preflight \
  --result-file "$AOF_ROOT/logs/aof_run_result.${RESULT_TAG}.json"

echo "[done] logs:"
echo "- $AOF_ROOT/logs/aof_add_result.${RESULT_TAG}.json"
echo "- $AOF_ROOT/logs/aof_run_result.${RESULT_TAG}.json"
