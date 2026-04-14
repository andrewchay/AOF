#!/usr/bin/env python3
"""AOF MCP Server - stdio-based Model Context Protocol server.

Exposes AOF capabilities as MCP tools for Claude Code, Cursor, and other MCP clients.
Since this is a thin wrapper, it delegates all real work to the bridge modules.

Configuration:
  Set AOF_SPEC_PATH to point to your spec JSON (defaults to aof_spec.example.json)

Claude Code config example (~/.claude/server.json):
  {
    "mcpServers": {
      "aof": {
        "command": "/Users/chaihao/LLM/AOF/.venv/bin/python",
        "args": ["/Users/chaihao/LLM/AOF/mcp_server.py"]
      }
    }
  }
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Lightweight JSON-RPC 2.0 MCP implementation
# ---------------------------------------------------------------------------

@dataclass
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]


class McpServer:
    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version
        self.tools: dict[str, McpTool] = {}
        self._initialized = False
        self._read_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    def register_tool(self, tool: McpTool) -> None:
        self.tools[tool.name] = tool

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._write_lock:
            payload = json.dumps(message, ensure_ascii=False)
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()

    async def _read(self) -> dict[str, Any] | None:
        line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    async def run(self) -> None:
        while True:
            msg = await self._read()
            if msg is None:
                break

            msg_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params", {})

            if method == "initialize":
                self._initialized = True
                await self._send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": self.name, "version": self.version},
                    },
                })
            elif method == "notifications/initialized":
                pass
            elif method == "tools/list":
                await self._send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {
                                "name": t.name,
                                "description": t.description,
                                "inputSchema": t.input_schema,
                            }
                            for t in self.tools.values()
                        ]
                    },
                })
            elif method == "tools/call":
                name = params.get("name", "")
                arguments = params.get("arguments", {})
                result = await self._handle_tool_call(name, arguments)
                await self._send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": result,
                })
            else:
                await self._send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })

    async def _handle_tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if not tool:
            return {
                "content": [{"type": "text", "text": f"Error: Unknown tool: {name}"}],
                "isError": True,
            }

        try:
            result = await tool.handler(arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except Exception as e:
            traceback_str = traceback.format_exc()
            return {
                "content": [{"type": "text", "text": f"Error: {e}\n{traceback_str}"}],
                "isError": True,
            }


# ---------------------------------------------------------------------------
# AOF Tool implementations
# ---------------------------------------------------------------------------

def _load_spec() -> dict[str, Any]:
    spec_path = os.environ.get("AOF_SPEC_PATH", "aof_spec.example.json")
    p = Path(spec_path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


async def _tool_hybrid_search(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.hybrid_search import AOFHybridSearch

    spec = _load_spec()
    cognee_root = (spec.get("cognee") or {}).get("root")
    engine = AOFHybridSearch(cognee_root=cognee_root)

    result = await engine.hybrid_search(
        query=args["query"],
        dataset_id=args.get("dataset_id"),
        dataset_name=args.get("dataset_name") or (spec.get("dataset")),
        limit=args.get("limit", 10),
        expansion=args.get("expansion", False),
    )
    return {
        "query": result.query,
        "expanded_queries": result.expanded_queries,
        "keyword_count": result.keyword_count,
        "vector_count": result.vector_count,
        "execution_time_ms": result.execution_time_ms,
        "results": [
            {
                "slug": r.slug,
                "type": r.type,
                "chunk_text": r.chunk_text,
                "source": r.source,
                "score": r.score,
            }
            for r in result.results
        ],
    }


async def _tool_enhanced_search(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.enhanced_search import search_with_intent

    spec = _load_spec()
    dataset = args.get("dataset_name") or spec.get("dataset")

    result = await search_with_intent(
        query=args["query"],
        user_intent=args.get("user_intent"),
        top_k=args.get("top_k", 10),
    )
    return {
        "query": result.query,
        "search_type_used": result.search_type_used.value,
        "execution_time_ms": result.execution_time_ms,
        "results": result.results,
    }


async def _tool_graph_analytics(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.graph_analytics import GraphAnalytics

    analyzer = GraphAnalytics(dataset_name=args["dataset_name"])
    metrics = await analyzer.compute_all_metrics()
    return metrics.to_dict()


async def _tool_dataset_status(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.dataset_manager import check_dataset_status

    status = await check_dataset_status(args["dataset_id"])
    return {
        "dataset_id": status.dataset_id,
        "pipeline_name": status.pipeline_name,
        "status": status.status,
        "progress": status.progress,
        "message": status.message,
        "last_updated": status.last_updated,
    }


async def _tool_health_check(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.graph_doctor import GraphDoctor

    doctor = GraphDoctor(
        dataset_name=args["dataset_name"],
        dataset_id=args.get("dataset_id"),
        cognee_root=args.get("cognee_root"),
    )
    report = await doctor.full_check()
    return report.to_dict()


async def _tool_export_markdown(args: dict[str, Any]) -> dict[str, Any]:
    from exporters.markdown_exporter import MarkdownExporter

    spec = _load_spec()
    exporter = MarkdownExporter(
        cognee_root=args.get("cognee_root") or (spec.get("cognee") or {}).get("root")
    )
    result = await exporter.export(
        dataset_id=args.get("dataset_id"),
        dataset_name=args.get("dataset_name") or spec.get("dataset"),
        output_dir=args["output_dir"],
        dry_run=args.get("dry_run", False),
    )
    return {
        "output_dir": str(result.output_dir),
        "pages_exported": result.pages_exported,
        "pages_skipped": result.pages_skipped,
        "mode": result.mode,
        "files_created": [str(p) for p in result.files_created],
        "errors": result.errors,
    }


async def _tool_list_datasets(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.dataset_manager import list_all_datasets

    datasets = await list_all_datasets()
    return {
        "datasets": [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "data_count": d.data_count,
                "status": d.status,
            }
            for d in datasets
        ]
    }


# ---------------------------------------------------------------------------
# Build and run server
# ---------------------------------------------------------------------------

def build_server() -> McpServer:
    server = McpServer(name="aof", version="2.1.0")

    server.register_tool(McpTool(
        name="aof_hybrid_search",
        description="执行 AOF 混合搜索（关键词 + 向量 + RRF 融合 + 4 层去重）",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "dataset_id": {"type": "string", "description": "数据集 ID（可选）"},
                "dataset_name": {"type": "string", "description": "数据集名称（可选，默认从 AOF_SPEC 读取）"},
                "limit": {"type": "integer", "description": "返回结果数量", "default": 10},
                "expansion": {"type": "boolean", "description": "是否开启查询扩展", "default": False},
            },
            "required": ["query"],
        },
        handler=_tool_hybrid_search,
    ))

    server.register_tool(McpTool(
        name="aof_enhanced_search",
        description="使用 Cognee 增强语义搜索（基于意图自动选择搜索类型）",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "user_intent": {"type": "string", "description": "用户意图，如 analytics/debugging/exploration/temporal"},
                "top_k": {"type": "integer", "description": "返回结果数量", "default": 10},
            },
            "required": ["query"],
        },
        handler=_tool_enhanced_search,
    ))

    server.register_tool(McpTool(
        name="aof_graph_analytics",
        description="运行图谱分析（PageRank、社区检测、中心性、统计指标）",
        input_schema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string", "description": "数据集名称"},
            },
            "required": ["dataset_name"],
        },
        handler=_tool_graph_analytics,
    ))

    server.register_tool(McpTool(
        name="aof_dataset_status",
        description="检查数据集的 cognify 处理状态",
        input_schema={
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string", "description": "数据集 ID 或名称"},
            },
            "required": ["dataset_id"],
        },
        handler=_tool_dataset_status,
    ))

    server.register_tool(McpTool(
        name="aof_health_check",
        description="运行 Graph Doctor 健康检查",
        input_schema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string", "description": "数据集名称"},
                "dataset_id": {"type": "string", "description": "数据集 ID（可选）"},
            },
            "required": ["dataset_name"],
        },
        handler=_tool_health_check,
    ))

    server.register_tool(McpTool(
        name="aof_export_markdown",
        description="将知识图谱导出为 Markdown 文件（人类可读）",
        input_schema={
            "type": "object",
            "properties": {
                "output_dir": {"type": "string", "description": "输出目录"},
                "dataset_id": {"type": "string", "description": "数据集 ID（可选）"},
                "dataset_name": {"type": "string", "description": "数据集名称（可选）"},
                "dry_run": {"type": "boolean", "description": "是否只预览不写入", "default": False},
            },
            "required": ["output_dir"],
        },
        handler=_tool_export_markdown,
    ))

    server.register_tool(McpTool(
        name="aof_list_datasets",
        description="列出所有可访问的数据集",
        input_schema={
            "type": "object",
            "properties": {},
        },
        handler=_tool_list_datasets,
    ))

    return server


async def main() -> None:
    server = build_server()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
