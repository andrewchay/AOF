# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.

"""官方 MCP SDK（mcp>=1.8）stdio 封装的 AOF server。

背景：``mcp_server.py`` 是 AOF 自己的换行 JSON-RPC 实现；官方 MCP SDK
当前的 stdio transport 同样按行分隔消息。迁移官方 SDK 的价值在 initialize
生命周期、schema 校验和客户端兼容性，而不是改变消息分帧。本模块复用同一
工具注册表与同一签名主体鉴权管线，保证两个入口的工具与授权语义一致。

用法：``python mcp_server_sdk.py``（stdio 传输）。
"""

from __future__ import annotations

import json

import mcp.types as types
from mcp.server.lowlevel import Server as SdkServer
from mcp.server.lowlevel.server import NotificationOptions

import mcp_server as legacy


def _build_sdk_server() -> SdkServer:
    """把 legacy 注册表包装成官方 SDK Server，鉴权语义保持不变。"""
    registry = legacy.build_server()
    server: SdkServer = SdkServer(registry.name, version=registry.version)

    async def handle_list_tools(ctx: object, request: types.PaginatedRequestParams) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                )
                for tool in registry.tools.values()
            ]
        )

    async def handle_call_tool(ctx: object, params: types.CallToolRequestParams) -> types.CallToolResult:
        name = params.name
        arguments = params.arguments or {}

        def _error_result(code: str, detail: str) -> types.CallToolResult:
            text = json.dumps({"code": code, "detail": detail}, ensure_ascii=False)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=text)],
                is_error=True,
            )

        tool = registry.tools.get(name)
        if tool is None:
            return _error_result("tool_not_found", f"unknown MCP tool: {name}")
        if not isinstance(arguments, dict):
            return _error_result("invalid_arguments", "tool arguments must be an object")
        try:
            principal = legacy._authorize_mcp_call(name, arguments)
            legacy._validate_mcp_arguments(tool, arguments)
            bound_arguments = legacy._bind_mcp_identity(name, arguments, principal)
            result = await tool.handler(bound_arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=text)],
                is_error=False,
            )
        except legacy.McpAuthenticationError as exc:
            return _error_result("authentication_required", str(exc))
        except legacy.McpAuthorizationError as exc:
            return _error_result("operation_not_permitted", str(exc))
        except legacy.McpRequestError as exc:
            return _error_result("invalid_arguments", str(exc))
        except Exception as exc:  # noqa: BLE001 — 与 legacy 入口一致，执行错误转为工具错误
            return _error_result("tool_execution_failed", str(exc))

    server.add_request_handler("tools/list", types.PaginatedRequestParams, handle_list_tools)
    server.add_request_handler("tools/call", types.CallToolRequestParams, handle_call_tool)
    return server


async def main() -> None:
    from mcp.server.stdio import stdio_server

    server = _build_sdk_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True)
            ),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
