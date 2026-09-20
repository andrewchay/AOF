# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with the
# Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""AOF 语义资产 MCP Server（stdio，只读数据资产路径）。

把 playbook 接地三件套（口径卡 Metric / 场景卡 Playbook / 表卡 ObjectType）以 MCP 工具
暴露给 agent（brando / Claude Code / Gravitas 工作区）。与治理栈服务器（根目录
``mcp_server.py``，release-pinned + 签名身份）并行：本服务只读本地资产文件，
无身份绑定；访问控制即 OS 文件权限。

接线示例（Claude Code / Gravitas mcp.json）：
  {
    "aof-semantic": {
      "type": "stdio",
      "command": "/Users/chaihao/LLM/AOF/.venv/bin/python",
      "args": ["/Users/chaihao/LLM/AOF/tools/aof_semantic_mcp.py"]
    }
  }
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server import (  # noqa: E402
    McpRequestError,
    McpServer,
    McpTool,
    _mcp_error,
    _validate_mcp_arguments,
)

from bridge.semantic_query import SemanticQuery  # noqa: E402

_SQ = SemanticQuery()


async def _tool_search(args: dict[str, Any]) -> dict[str, Any]:
    return _SQ.search(args["query"], args.get("kind"), int(args.get("k", 8)))


async def _tool_get(args: dict[str, Any]) -> dict[str, Any]:
    return _SQ.get(args["resource_id"])


async def _tool_route(args: dict[str, Any]) -> dict[str, Any]:
    return _SQ.route(
        args["question"],
        int(args.get("k_metric", 3)),
        int(args.get("k_scene", 2)),
        int(args.get("k_table", 5)),
    )


async def _tool_status(args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    return _SQ.status()


class AssetServer(McpServer):
    """只读数据资产服务：复用协议层，跳过治理栈的身份/授权绑定。"""

    async def _handle_tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if not tool:
            return _mcp_error("tool_not_found", f"unknown MCP tool: {name}")
        try:
            if not isinstance(arguments, dict):
                raise McpRequestError("tool arguments must be an object")
            _validate_mcp_arguments(tool, arguments)
            result = await tool.handler(arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except KeyError as exc:
            return _mcp_error("resource_not_found", f"unknown resource_id: {exc}")
        except (ValueError, McpRequestError) as exc:
            return _mcp_error("invalid_arguments", str(exc))
        except Exception as exc:  # pragma: no cover - 防御性兜底
            return _mcp_error("tool_execution_failed", str(exc))


def build_server() -> McpServer:
    server = AssetServer("aof-semantic", "0.1.0")
    server.register_tool(McpTool(
        name="aof_route",
        description=(
            "数据问题接地计划（首选入口）：一次返回 口径卡(Metric)+场景卡(Playbook)+表卡(ObjectType) "
            "三级命中与执行指引。场景卡命中→读合同文件；口径卡命中→定义红线直接可答；"
            "表卡命中→照常 preflight。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户的自然语言数据问题"},
                "k_metric": {"type": "integer", "description": "口径卡召回数，默认 3"},
                "k_scene": {"type": "integer", "description": "场景卡召回数，默认 2"},
                "k_table": {"type": "integer", "description": "表卡召回数，默认 5"},
            },
            "required": ["question"],
        },
        handler=_tool_route,
    ))
    server.register_tool(McpTool(
        name="aof_search",
        description="AOF 资源索引检索（FTS5 trigram + 子串兜底，5,852 资源）。kind 可选 "
                    "ObjectType/RelationType/Playbook/Metric/ContextAssertion 等。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词，多词 AND，0 命中自动 OR 兜底"},
                "kind": {"type": "string", "description": "按 kind 过滤，如 Metric"},
                "k": {"type": "integer", "description": "返回条数，默认 8"},
            },
            "required": ["query"],
        },
        handler=_tool_search,
    ))
    server.register_tool(McpTool(
        name="aof_get",
        description="按 resource_id 读取资源卡全文（含 spec：表清单/红线/合同文件路径/互链 URI）。",
        input_schema={
            "type": "object",
            "properties": {"resource_id": {"type": "string", "description": "如 aof://mihoyo/hk4e/metric/return-retention/zongze/付费"}},
            "required": ["resource_id"],
        },
        handler=_tool_get,
    ))
    server.register_tool(McpTool(
        name="aof_status",
        description="语义资产存在性与规模（索引/资源卡/Kuzu 图谱），答前体检用。",
        input_schema={"type": "object", "properties": {}},
        handler=_tool_status,
    ))
    return server


async def main() -> None:
    await build_server().run()


if __name__ == "__main__":
    asyncio.run(main())
