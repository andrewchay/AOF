# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.

"""官方 MCP SDK 传输层封装（mcp_server_sdk）的端到端验证。

以真实 stdio 子进程启动 mcp_server_sdk.py，用官方 mcp 客户端验证：
- MCP 标准 initialize / tools/list 帧协议互通；
- 无签名 principal 的工具调用被失败关闭；
- 合法签名 principal 可正常调用（工具语义与 legacy 入口一致）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = "sdk-transport-test-secret"
TEST_KEY_ID = "sdk-transport-test-key"


def _principal_headers(role: str = "admin") -> dict[str, str]:
    from bridge.semantic_core.identity import SignedPrincipalVerifier

    return SignedPrincipalVerifier(key_id=TEST_KEY_ID, secret=TEST_SECRET.encode()).sign_headers(
        subject="sdk-transport-test", tenant_id="sdk-transport-tenant", roles=[role]
    )


async def _run_client(coro):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_ROOT / "mcp_server_sdk.py")],
        cwd=str(PROJECT_ROOT),
        env={
            "AOF_SEMANTIC_IDENTITY_SECRET": TEST_SECRET,
            "AOF_SEMANTIC_IDENTITY_KEY_ID": TEST_KEY_ID,
            "AOF_SPEC_PATH": str(PROJECT_ROOT / "aof_spec.example.json"),
            "AOF_REVOCATIONS_FILE": "/tmp/aof-sdk-transport-test-revocations.sqlite",
        },
    )

    async def _scoped():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await coro(session)

    return await asyncio.wait_for(_scoped(), timeout=180)


@pytest.mark.slow
def test_sdk_transport_protocol_and_auth():
    """Given 官方 MCP 客户端 When 走 stdio 连接 Then 协议互通且鉴权语义不变。"""
    collected: dict = {}

    async def scenario(session: ClientSession) -> None:
        tools = await session.list_tools()
        collected["tool_count"] = len(tools.tools)
        collected["has_hybrid_search"] = any(t.name == "aof_hybrid_search" for t in tools.tools)
        # 每个工具的 schema 都必须要求 principal_headers（安全 schema 未被绕过）
        hs = next(t for t in tools.tools if t.name == "aof_hybrid_search")
        collected["principal_required"] = "principal_headers" in hs.input_schema.get("required", [])

        # 无 principal：应用层失败关闭，code=authentication_required
        result = await session.call_tool("aof_list_datasets", {})
        collected["no_principal_is_error"] = result.is_error
        payload = json.loads(result.content[0].text)
        collected["no_principal_code"] = payload.get("code")

        # principal 字段存在但签名非法（应用层）：必须失败关闭且 code 为 authentication_required
        result = await session.call_tool("aof_list_datasets", {"principal_headers": {"x-aof-principal-subject": "forged"}})
        collected["forged_principal_is_error"] = result.is_error
        payload = json.loads(result.content[0].text)
        collected["forged_principal_code"] = payload.get("code")

        # 合法 principal：必须越过认证边界。unit-contract 不安装可选 Cognee，
        # 因而 list_datasets 的后续业务执行可返回 tool_execution_failed；这不应
        # 被误判为 SDK stdio 鉴权失败。
        result = await session.call_tool(
            "aof_list_datasets", {"principal_headers": _principal_headers()}
        )
        data = json.loads(result.content[0].text)
        collected["authorized_code"] = data.get("code")
        collected["datasets_returned"] = "datasets" in data

    asyncio.run(_run_client(scenario))

    assert collected["tool_count"] == 38  # 37 个存量工具 + aof_knowledge_build
    assert collected["has_hybrid_search"] is True
    assert collected["principal_required"] is True
    assert collected["no_principal_is_error"] is True
    assert collected["no_principal_code"] == "authentication_required"
    assert collected["forged_principal_is_error"] is True
    assert collected["forged_principal_code"] == "authentication_required"
    assert collected["authorized_code"] != "authentication_required"
    assert collected["datasets_returned"] is True or collected["authorized_code"] == "tool_execution_failed"
