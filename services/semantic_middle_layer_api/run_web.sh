#!/usr/bin/env bash
# AOF 轻量版一键启动：构建前端 (web/dist) 并用后端托管，访问 http://localhost:8787
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AOF_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WEB_DIR="$AOF_ROOT/web"
PY="${AOF_PY:-$AOF_ROOT/.venv/bin/python}"

echo "==> 构建前端 (web/dist) ..."
if command -v pnpm >/dev/null 2>&1; then
  (cd "$WEB_DIR" && pnpm install --frozen-lockfile >/dev/null 2>&1 || pnpm install >/dev/null && pnpm run build)
elif command -v npm >/dev/null 2>&1; then
  (cd "$WEB_DIR" && npm install >/dev/null && npm run build)
else
  echo "! 未找到 pnpm/npm，跳过前端构建。若已有 web/dist 可忽略。"
fi

if [[ ! -d "$WEB_DIR/dist" ]]; then
  echo "! 前端产物缺失: $WEB_DIR/dist，继续启动后端（API 可用，Web UI 不可用）"
fi

echo "==> 启动后端 (托管前端于 http://localhost:8787) ..."
if [[ -x "$PY" ]]; then
  exec "$PY" -m uvicorn services.semantic_middle_layer_api.app:app --host 127.0.0.1 --port 8787
else
  exec python3 -m uvicorn services.semantic_middle_layer_api.app:app --host 127.0.0.1 --port 8787
fi
