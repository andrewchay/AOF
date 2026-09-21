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
import sys
from pathlib import Path

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bridge.knowledge_build_operations import snapshot_digest as status_snapshot_digest

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
        command=sys.executable,
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
                "operation_id": "op-e2e-1",
                "kb_id": "kb-e2e",
                "docs": _docs(),
                "principal_headers": _headers("tenant-a"),
            },
        )
        assert not build.is_error, build.content[0].text
        built = json.loads(build.content[0].text)
        assert built["ok"] is True
        assert built["operation_id"] == "op-e2e-1"
        assert built["operation_state"] == "succeeded"
        assert built["operation_snapshot_digest"] == status_snapshot_digest("kb-e2e", _docs())
        assert built["ledger"]["publish"]["verify"] is True
        policy_id = built["policy_resource_id"]
        digest = built["release_digest"]
        assert policy_id == "aof://tenant-a/platform/policy/query-adapter"

        # 2) status 可查询，跨 tenant 不可见；同 operation 重试幂等返回原 release。
        status = await session.call_tool(
            "aof_knowledge_build_status",
            {"operation_id": "op-e2e-1", "principal_headers": _headers("tenant-a", roles=["viewer"])},
        )
        assert not status.is_error, status.content[0].text
        status_payload = json.loads(status.content[0].text)
        assert status_payload["found"] is True
        assert status_payload["state"] == "succeeded"
        assert status_payload["snapshot_digest"] == built["operation_snapshot_digest"]
        assert status_payload["result"]["operation_snapshot_digest"] == built["operation_snapshot_digest"]
        assert status_payload["result"]["release_id"] == built["release_id"]

        hidden = await session.call_tool(
            "aof_knowledge_build_status",
            {"operation_id": "op-e2e-1", "principal_headers": _headers("tenant-b", roles=["viewer"])},
        )
        assert json.loads(hidden.content[0].text) == {"found": False, "operation_id": "op-e2e-1"}

        repeated = await session.call_tool(
            "aof_knowledge_build",
            {
                "operation_id": "op-e2e-1",
                "kb_id": "kb-e2e",
                "docs": _docs(),
                "principal_headers": _headers("tenant-a"),
            },
        )
        assert not repeated.is_error, repeated.content[0].text
        assert json.loads(repeated.content[0].text)["release_id"] == built["release_id"]

        # 3) release-pinned 查询命中
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

        # 4) build 仅为自身 tenant 登记 ownership（K03 Task 3 写侧）
        kb_descriptor = f"kb-{hashlib.sha256(b'kb-e2e').hexdigest()[:16]}"
        registry_a = tmp_path / "knowledge" / "tenant-a" / "dataset-ownership.json"
        registry_payload = json.loads(registry_a.read_text(encoding="utf-8"))
        assert registry_payload["tenant_id"] == "tenant-a"
        assert kb_descriptor in registry_payload["dataset_ids"]
        # tenant-b 未 build，不应看到 tenant-a 的 descriptor
        registry_b = tmp_path / "knowledge" / "tenant-b" / "dataset-ownership.json"
        assert not registry_b.exists() or kb_descriptor not in json.loads(
            registry_b.read_text(encoding="utf-8")
        ).get("dataset_ids", [])

        # 5) digest 不匹配被拒
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
            {"operation_id": "op-unsigned", "kb_id": "kb-e2e", "docs": _docs()},
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
                "operation_id": "op-tenant-isolation",
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
