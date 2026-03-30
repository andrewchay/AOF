#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <INPUT_PATH> [TOPIC] [MAX_ITERATIONS] [FEEDBACK_JSONL|AUTO]"
  echo "Example1: $0 /abs/path/topic.sql miyoushe 4"
  echo "Example2: $0 /abs/path/topic.sql miyoushe 4 AUTO"
  echo "Example3: $0 /abs/path/topic.sql miyoushe 4 /abs/path/feedback.jsonl"
  exit 2
fi

INPUT_PATH="$1"
TOPIC="${2:-}"
MAX_ITERATIONS="${3:-4}"
FEEDBACK_ARG="${4:-}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_AOF_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
AOF_ROOT="${AOF_ROOT:-$DEFAULT_AOF_ROOT}"
PY="${AOF_PY:-$AOF_ROOT/.venv/bin/python}"
SCRIPT="$AOF_ROOT/tools/ontology_factory/build_testdata_ontology_factory.py"

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

if [[ ! -f "$INPUT_PATH" ]]; then
  echo "[error] input not found: $INPUT_PATH"
  exit 1
fi

slugify() {
  local s="$1"
  s="$(printf '%s' "$s" | tr '[:upper:]' '[:lower:]')"
  s="$(printf '%s' "$s" | sed -E 's/[^a-z0-9_-]+/_/g; s/_+/_/g; s/^_+|_+$//g')"
  if [[ -z "$s" ]]; then
    s="topic"
  fi
  printf '%s' "$s"
}

resolve_auto_feedback() {
  local topic_slug="$1"
  local dir="$AOF_ROOT/logs/ontology_factory"
  local latest
  latest="$(ls -1 "$dir"/TEST_DATA_feedback_candidates_"$topic_slug"_*.jsonl 2>/dev/null | tail -1 || true)"
  if [[ -n "$latest" && -s "$latest" ]]; then
    printf '%s' "$latest"
  fi
}

CMD=("$PY" "$SCRIPT" --input "$INPUT_PATH" --max-iterations "$MAX_ITERATIONS")
if [[ -n "$TOPIC" ]]; then
  CMD+=(--topic "$TOPIC")
fi

RESOLVED_FEEDBACK=""
if [[ -n "$FEEDBACK_ARG" ]]; then
  if [[ "$FEEDBACK_ARG" == "AUTO" ]]; then
    topic_base="$TOPIC"
    if [[ -z "$topic_base" ]]; then
      topic_base="$(basename "$INPUT_PATH")"
      topic_base="${topic_base%.*}"
    fi
    topic_slug="$(slugify "$topic_base")"
    RESOLVED_FEEDBACK="$(resolve_auto_feedback "$topic_slug")"
    if [[ -n "$RESOLVED_FEEDBACK" ]]; then
      echo "[info] AUTO feedback resolved: $RESOLVED_FEEDBACK"
      CMD+=(--feedback-jsonl "$RESOLVED_FEEDBACK")
    else
      echo "[info] AUTO feedback not found (or empty), run without feedback injection"
    fi
  else
    if [[ ! -f "$FEEDBACK_ARG" ]]; then
      echo "[error] feedback file not found: $FEEDBACK_ARG"
      exit 1
    fi
    RESOLVED_FEEDBACK="$FEEDBACK_ARG"
    CMD+=(--feedback-jsonl "$FEEDBACK_ARG")
  fi
fi

if [[ -z "${LLM_API_KEY:-}" ]]; then
  echo "[warn] LLM_API_KEY not set. Will build ontology only (skip align)."
  CMD+=(--skip-align)
fi

RUN_LOG="$(mktemp)"
echo "[run] ${CMD[*]}"
"${CMD[@]}" | tee "$RUN_LOG"

REPORT_JSON="$(grep -E '^- report_json: ' "$RUN_LOG" | tail -1 | sed 's/^- report_json: //')"
FEEDBACK_JSONL="$(grep -E '^- feedback_jsonl: ' "$RUN_LOG" | tail -1 | sed 's/^- feedback_jsonl: //')"
FEEDBACK_MD="$(grep -E '^- feedback_md: ' "$RUN_LOG" | tail -1 | sed 's/^- feedback_md: //')"
STATUS_LINE="$(grep -E '^\[status\] ' "$RUN_LOG" | tail -1 | sed 's/^\[status\] //')"
CANDIDATE_LINE="$(grep -E '^\[candidates\] ' "$RUN_LOG" | tail -1 | sed 's/^\[candidates\] //')"

printf '\n[summary]\n'
if [[ -n "$REPORT_JSON" ]]; then
  echo "- report_json: $REPORT_JSON"
fi
if [[ -n "$FEEDBACK_JSONL" ]]; then
  echo "- feedback_jsonl: $FEEDBACK_JSONL"
fi
if [[ -n "$FEEDBACK_MD" ]]; then
  echo "- feedback_md: $FEEDBACK_MD"
fi
if [[ -n "$STATUS_LINE" ]]; then
  echo "- feedback_status: $STATUS_LINE"
fi
if [[ -n "$CANDIDATE_LINE" ]]; then
  echo "- feedback_candidates: $CANDIDATE_LINE"
fi

printf '\n[next]\n'
if [[ -n "$CANDIDATE_LINE" && "$CANDIDATE_LINE" != "0" ]]; then
  echo "1) 打开 feedback_jsonl，把 TODO_CLASS 改成业务类"
  echo "2) 再跑：$0 \"$INPUT_PATH\" \"${TOPIC:-}\" \"$MAX_ITERATIONS\" \"$FEEDBACK_JSONL\""
else
  echo "- 当前无待处理 unmatched 候选项，可直接用本体进入 Text-to-SQL 映射层构建"
fi
