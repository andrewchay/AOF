# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with the
# Business Source License, use of this software will be governed by
# the Apache License, Version 2.0.

"""aof_knowledge_build 端到端验证：build → semantic_query 全链路。

以真实 stdio 子进程启动 mcp_server_sdk.py：
1. aof_knowledge_build 摄入 2 篇文档并发布 + promote 到 production channel；
2. aof_semantic_query 用返回的 policy_resource_id + release_digest 查询命中；
3. digest 不匹配时被治理拒绝；
4. 未签名调用 build 被失败关闭；
5. 不同 tenant 的 release 互不串线（tenant-b 查询 tenant-a 的 digest 拒绝）。

数据根隔离在 tmp（AOF_KNOWLEDGE_STATE_DIR / AOF_COMPILER_STATE_DIR），
不触碰仓库 data/ 目录。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = "knowledge-build-test-secret"
TEST_KEY_ID = "knowledge-build-test-key"


def _headers(tenant: str, roles: list[str] | None = None) -> dict[str, str]:
    from bridge.semantic_core.identity import SignedPrincipalVerifier

    return SignedPrincipalVerifier(
        key_id=TEST_KEY_ID, secret=TEST_SECRET.encode()
    ).sign_headers(subject="kb-build-test", tenant_id=tenant, roles=roles or ["admin"])


def _docs() -> list[dict]:
    return [
        {
            "relative_path": "notes/alpha.md",
            "title": "Alpha 概念",
            "content": "Alpha 是一个知识图谱构建概念，用于端到端验证。",
            "sha256": hashlib.sha256(b"alpha-content").hexdigest(),
            "links": ["Beta 概念"],
        },
        {
            "relative_path": "notes/beta.md",
            "title": "Beta 概念",
            "content": "Beta 概念依赖 Alpha，验证 wikilink 连边解析。",
            "sha256": hashlib.sha256(b"beta-content").hexdigest(),
            "links": [],
        },
    ]


async def _run(tmp: Path, coro):
    params = StdioServerParameters(
        command=str(PROJECT_ROOT / ".venv/bin/python"),
        args=[str(PROJECT_ROOT / "mcp_server_sdk.py")],
        cwd=str(PROJECT_ROOT),
        env={
            "AOF_SEMANTIC_IDENTITY_SECRET": TEST_SECRET,
            "AOF_SEMANTIC_IDENTITY_KEY_ID": TEST_KEY_ID,
            "AOF_SPEC_PATH": str(PROJECT_ROOT / "aof_spec.example.json"),
            "AOF_KNOWLEDGE_STATE_DIR": str(tmp / "knowledge"),
            "AOF_COMPILER_STATE_DIR": str(tmp / "compiler"),
            "AOF_REVOCATIONS_FILE": str(tmp / "revocations.sqlite"),
        },
    )

    async def _scoped():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await coro(session)

    return await asyncio.wait_for(_scoped(), timeout=180)


@pytest.mark.asyncio
async def test_knowledge_build_then_semantic_query(tmp_path: Path):
    """构建 → release-pinned 查询命中 → digest 不匹配拒绝。"""

    async def scenario(session: ClientSession):
        # 1) 构建
        build = await session.call_tool(
            "aof_knowledge_build",
            {
                "kb_id": "kb-e2e",
                "docs": _docs(),
                "principal_headers": _headers("tenant-a"),
            },
        )
        assert not build.is_error, build.content[0].text
        built = json.loads(build.content[0].text)
        assert built["ok"] is True
        assert built["ledger"]["publish"]["verify"] is True
        policy_id = built["policy_resource_id"]
        digest = built["release_digest"]
        assert policy_id == "aof://tenant-a/platform/policy/query-adapter"

        # 2) release-pinned 查询命中
        queried = await session.call_tool(
            "aof_semantic_query",
            {
                "channel": "production",
                "capability": "semantic_search",
                "query": "Alpha",
                "purpose": "e2e-test",
                "policy_resource_id": policy_id,
                "rationale": "e2e",
                "parameters": {"limit": 5, "expected_release_digest": digest},
                "principal_headers": _headers("tenant-a", roles=["analyst"]),
            },
        )
        assert not queried.is_error, queried.content[0].text
        result = json.loads(queried.content[0].text)
        # 治理查询返回完整 QueryRun：命中在 governed_result.result.data.hits
        hits = result["governed_result"]["result"]["data"]["hits"]
        assert any("Alpha" in str(h.get("name", "")) for h in hits), result

        # 3) digest 不匹配被拒
        stale = await session.call_tool(
            "aof_semantic_query",
            {
                "channel": "production",
                "capability": "semantic_search",
                "query": "Alpha",
                "purpose": "e2e-test",
                "policy_resource_id": policy_id,
                "rationale": "e2e",
                "parameters": {
                    "limit": 5,
                    "expected_release_digest": "sha256:deadbeef",
                },
                "principal_headers": _headers("tenant-a", roles=["analyst"]),
            },
        )
        assert stale.is_error
        # 拒绝证据：digest 不匹配被治理边界拦截（错误文案以 AOF 实现为准）
        assert (
            "snapshot" in stale.content[0].text
            or "digest" in stale.content[0].text.lower()
        )

    await _run(tmp_path, scenario)


@pytest.mark.asyncio
async def test_knowledge_build_requires_signature(tmp_path: Path):
    async def scenario(session: ClientSession):
        res = await session.call_tool(
            "aof_knowledge_build",
            {"kb_id": "kb-e2e", "docs": _docs()},
        )
        assert res.is_error
        assert "authentication_required" in res.content[0].text

    await _run(tmp_path, scenario)


@pytest.mark.asyncio
async def test_knowledge_build_tenant_isolation(tmp_path: Path):
    """tenant-b 用 tenant-a 的 policy id 查询被拒（release 不跨租户）。"""

    async def scenario(session: ClientSession):
        build = await session.call_tool(
            "aof_knowledge_build",
            {
                "kb_id": "kb-e2e",
                "docs": _docs(),
                "principal_headers": _headers("tenant-a"),
            },
        )
        assert not build.is_error, build.content[0].text
        digest = json.loads(build.content[0].text)["release_digest"]

        res = await session.call_tool(
            "aof_semantic_query",
            {
                "channel": "production",
                "capability": "semantic_search",
                "query": "Alpha",
                "purpose": "e2e-test",
                "policy_resource_id": "aof://tenant-a/platform/policy/query-adapter",
                "rationale": "e2e",
                "parameters": {"limit": 5, "expected_release_digest": digest},
                "principal_headers": _headers("tenant-b", roles=["analyst"]),
            },
        )
        assert res.is_error

    await _run(tmp_path, scenario)
