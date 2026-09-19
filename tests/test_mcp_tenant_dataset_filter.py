"""Tenant filtering for the governed aof_list_datasets MCP operation."""

from __future__ import annotations

from pathlib import Path

import pytest

import mcp_server
from bridge.dataset_manager import DatasetInfo
from bridge.tenant_dataset_registry import register_owned_dataset


@pytest.mark.asyncio
async def test_list_datasets_returns_only_authenticated_tenant_owned_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AOF_KNOWLEDGE_STATE_DIR", str(tmp_path))

    async def fake_list_all_datasets() -> list[DatasetInfo]:
        return [
            DatasetInfo(id="dataset-a", name="A"),
            DatasetInfo(id="dataset-b", name="B"),
            DatasetInfo(id="legacy", name="Legacy"),
        ]

    import bridge.dataset_manager

    monkeypatch.setattr(bridge.dataset_manager, "list_all_datasets", fake_list_all_datasets)
    register_owned_dataset(state_root=tmp_path, tenant_id="tenant-a", dataset_id="dataset-a")
    register_owned_dataset(state_root=tmp_path, tenant_id="tenant-b", dataset_id="dataset-b")

    result = await mcp_server._tool_list_datasets({"tenant_id": "tenant-a"})

    assert [dataset["id"] for dataset in result["datasets"]] == ["dataset-a"]


@pytest.mark.asyncio
async def test_list_datasets_fails_closed_for_unregistered_tenant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AOF_KNOWLEDGE_STATE_DIR", str(tmp_path))

    async def fake_list_all_datasets() -> list[DatasetInfo]:
        return [DatasetInfo(id="legacy", name="Legacy")]

    import bridge.dataset_manager

    monkeypatch.setattr(bridge.dataset_manager, "list_all_datasets", fake_list_all_datasets)

    assert await mcp_server._tool_list_datasets({"tenant_id": "tenant-new"}) == {"datasets": []}


def test_list_datasets_tenant_is_derived_from_signed_principal() -> None:
    class Principal:
        tenant_id = "tenant-a"
        subject = "subject"

    bound = mcp_server._bind_mcp_identity(
        "aof_list_datasets", {"principal_headers": {"x": "signed"}}, Principal()
    )

    assert bound["tenant_id"] == "tenant-a"
    with pytest.raises(mcp_server.McpAuthorizationError):
        mcp_server._bind_mcp_identity(
            "aof_list_datasets", {"tenant_id": "tenant-b"}, Principal()
        )
