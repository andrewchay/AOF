# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W04.05 authoritative usage and atomic in-process quota reservations."""

from __future__ import annotations

import asyncio

import pytest

from bridge.tenant.manager import (
    QuotaExceededError,
    TenantConfig,
    TenantManager,
    TenantUsage,
)


async def _manager(
    *, datasets=0, nodes=0, max_datasets=1, max_queries=1, authoritative=False
):
    async def usage(_tenant_id: str) -> TenantUsage:
        return TenantUsage(datasets, nodes, 0)

    manager = TenantManager(usage_provider=usage if authoritative else None)
    tenant = await manager.create_tenant(
        "Quota Tenant",
        slug="quota-tenant",
        config=TenantConfig(
            max_datasets=max_datasets,
            max_graph_nodes=100,
            max_concurrent_queries=max_queries,
        ),
    )
    return manager, tenant


@pytest.mark.asyncio
async def test_authoritative_usage_refreshes_persisted_snapshot_and_decides_quota():
    manager, tenant = await _manager(
        datasets=1, nodes=41, max_datasets=1, authoritative=True
    )

    allowed, detail = await manager.check_quota(tenant.id, "dataset")
    report = await manager.get_usage_report(tenant.id)

    assert allowed is False
    assert detail["current"] == 1
    assert report["usage_source"] == "authoritative"
    assert report["datasets"]["used"] == 1
    assert report["graph_nodes"]["used"] == 41


@pytest.mark.asyncio
async def test_concurrent_reservations_cannot_oversubscribe_and_release_restores_capacity():
    manager, tenant = await _manager(max_queries=1)

    results = await asyncio.gather(
        manager.reserve_quota(tenant.id, "concurrent_query"),
        manager.reserve_quota(tenant.id, "concurrent_query"),
        return_exceptions=True,
    )
    reservations = [result for result in results if isinstance(result, str)]
    failures = [result for result in results if isinstance(result, QuotaExceededError)]
    assert len(reservations) == 1
    assert len(failures) == 1

    assert await manager.release_quota(reservations[0]) is True
    replacement = await manager.reserve_quota(tenant.id, "concurrent_query")
    assert await manager.release_quota(replacement) is True


@pytest.mark.asyncio
async def test_failed_creation_releases_reservation_and_commit_consumes_durable_quota():
    manager, tenant = await _manager(max_datasets=1)

    failed_attempt = await manager.reserve_quota(tenant.id, "dataset")
    assert await manager.release_quota(failed_attempt) is True

    successful_attempt = await manager.reserve_quota(tenant.id, "dataset")
    assert await manager.commit_quota(successful_attempt) is True
    persisted = await manager.get_tenant(tenant.id)
    assert persisted is not None and persisted.dataset_count == 1

    with pytest.raises(QuotaExceededError):
        await manager.reserve_quota(tenant.id, "dataset")


@pytest.mark.asyncio
async def test_unknown_nonpositive_and_concurrent_commit_fail_closed():
    manager, tenant = await _manager()
    assert (await manager.check_quota(tenant.id, "unknown"))[0] is False
    assert (await manager.check_quota(tenant.id, "dataset", 0))[0] is False

    reservation = await manager.reserve_quota(tenant.id, "concurrent_query")
    with pytest.raises(QuotaExceededError, match="must be released"):
        await manager.commit_quota(reservation)
    assert await manager.release_quota(reservation) is True
