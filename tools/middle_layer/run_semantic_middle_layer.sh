#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <TOPIC> <DOCS_PATH> [METADATA_JSON] [FEEDBACK_JSONL] [MAX_ITERATIONS]"
  echo "Example: $0 miyoushe /abs/docs /abs/metadata.json /abs/feedback.jsonl 3"
  exit 2
fi

TOPIC="$1"
DOCS_PATH="$2"
METADATA_JSON="${3:-}"
FEEDBACK_JSONL="${4:-}"
MAX_ITERATIONS="${5:-3}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_AOF_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
AOF_ROOT="${AOF_ROOT:-$DEFAULT_AOF_ROOT}"
PY="${AOF_PY:-$AOF_ROOT/.venv/bin/python}"
SCRIPT="$AOF_ROOT/tools/middle_layer/build_semantic_middle_layer.py"

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

CMD=("$PY" "$SCRIPT" --topic "$TOPIC" --docs-path "$DOCS_PATH" --max-iterations "$MAX_ITERATIONS")

if [[ -n "$METADATA_JSON" ]]; then
  CMD+=(--metadata-json "$METADATA_JSON")
fi
if [[ -n "$FEEDBACK_JSONL" ]]; then
  CMD+=(--feedback-jsonl "$FEEDBACK_JSONL")
fi
if [[ -z "${LLM_API_KEY:-}" ]]; then
  echo "[warn] LLM_API_KEY not set. Ontology step will skip align."
  CMD+=(--skip-align)
fi

echo "[run] ${CMD[*]}"
"${CMD[@]}"
