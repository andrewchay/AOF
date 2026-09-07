# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Public contracts for AOF Semantic IR and Knowledge Releases."""

from __future__ import annotations

import pytest

from bridge.semantic_core import (
    FileReleaseRepository,
    KnowledgeRelease,
    ReleaseError,
    ResourceKind,
    SemanticModelError,
    SemanticResource,
)


def test_semantic_resource_revision_is_canonical() -> None:
    first = SemanticResource.create(
        resource_id="aof://acme/sales/metric/gmv",
        kind=ResourceKind.METRIC,
        name="gmv",
        domain="sales",
        owner="data-platform",
        tags=["transaction", "finance"],
        depends_on=[
            "aof://acme/sales/logical-dataset/order",
            "aof://acme/sales/object-type/order",
        ],
        spec={"aggregation": "sum", "expression": {"field": "paid_amount", "filter": "is_paid"}},
    )
    reordered = SemanticResource.create(
        resource_id="aof://acme/sales/metric/gmv",
        kind="Metric",
        name="gmv",
        domain="sales",
        owner="data-platform",
        tags=["finance", "transaction", "finance"],
        depends_on=[
            "aof://acme/sales/object-type/order",
            "aof://acme/sales/logical-dataset/order",
        ],
        spec={"expression": {"filter": "is_paid", "field": "paid_amount"}, "aggregation": "sum"},
    )

    assert first.revision_id == reordered.revision_id
    assert first.to_dict() == reordered.to_dict()


def test_semantic_evidence_is_identified_and_order_independent() -> None:
    base = {
        "resource_id": "aof://acme/sales/concept/customer",
        "kind": ResourceKind.CONCEPT,
        "name": "customer",
        "domain": "sales",
        "owner": "knowledge-team",
    }
    first = SemanticResource.create(
        **base,
        evidence=[
            {"evidence_id": "source:b", "source_uri": "docs://b", "content_hash": "sha256:b"},
            {"evidence_id": "source:a", "source_uri": "docs://a", "content_hash": "sha256:a"},
        ],
    )
    reordered = SemanticResource.create(
        **base,
        evidence=[
            {"content_hash": "sha256:a", "source_uri": "docs://a", "evidence_id": "source:a"},
            {"content_hash": "sha256:b", "source_uri": "docs://b", "evidence_id": "source:b"},
        ],
    )

    assert first.revision_id == reordered.revision_id
    with pytest.raises(SemanticModelError, match="evidence_id"):
        SemanticResource.create(**base, evidence=[{"source_uri": "docs://missing-id"}])


def test_semantic_resource_is_immutable_and_detects_tampered_round_trip() -> None:
    resource = SemanticResource.create(
        resource_id="aof://acme/sales/logical-dataset/order",
        kind=ResourceKind.LOGICAL_DATASET,
        name="order",
        domain="sales",
        owner="data-platform",
        spec={"fields": [{"name": "order_id", "type": "string"}]},
    )
    original_revision = resource.revision_id

    with pytest.raises(TypeError):
        resource.spec["fields"][0]["name"] = "tampered"
    assert resource.revision_id == original_revision
    assert SemanticResource.from_dict(resource.to_dict()) == resource

    serialized = resource.to_dict()
    serialized["spec"]["fields"][0]["name"] = "tampered"
    with pytest.raises(SemanticModelError, match="revision_id"):
        SemanticResource.from_dict(serialized)


def test_semantic_resource_rejects_invalid_identity_and_self_dependency() -> None:
    with pytest.raises(SemanticModelError, match="resource_id"):
        SemanticResource.create(
            resource_id="metric:gmv", kind=ResourceKind.METRIC, name="gmv", domain="sales", owner="team"
        )
    with pytest.raises(SemanticModelError, match="depend on itself"):
        SemanticResource.create(
            resource_id="aof://acme/sales/metric/gmv",
            kind=ResourceKind.METRIC,
            name="gmv",
            domain="sales",
            owner="team",
            depends_on=["aof://acme/sales/metric/gmv"],
        )


def test_knowledge_release_requires_dependency_closure_and_is_canonical() -> None:
    dataset = SemanticResource.create(
        resource_id="aof://acme/sales/logical-dataset/order",
        kind=ResourceKind.LOGICAL_DATASET,
        name="order",
        domain="sales",
        owner="data-platform",
    )
    metric = SemanticResource.create(
        resource_id="aof://acme/sales/metric/gmv",
        kind=ResourceKind.METRIC,
        name="gmv",
        domain="sales",
        owner="data-platform",
        depends_on=[dataset.resource_id],
    )

    with pytest.raises(ReleaseError, match="missing resource dependencies"):
        KnowledgeRelease.build(release_id="sales-knowledge@2026.08.17.1", resources=[metric])

    first = KnowledgeRelease.build(
        release_id="sales-knowledge@2026.08.17.1", resources=[metric, dataset]
    )
    reordered = KnowledgeRelease.build(
        release_id="sales-knowledge@2026.08.17.1", resources=[dataset, metric]
    )
    assert first.release_digest == reordered.release_digest
    assert first.to_dict() == reordered.to_dict()


def test_release_round_trip_detects_tampering_and_repository_prevents_overwrite(tmp_path) -> None:
    resource = SemanticResource.create(
        resource_id="aof://acme/sales/concept/customer",
        kind=ResourceKind.CONCEPT,
        name="customer",
        domain="sales",
        owner="knowledge-team",
    )
    release = KnowledgeRelease.build(
        release_id="sales-knowledge@2026.08.17.1",
        resources=[resource],
        scope={"tenant_id": "acme", "domain": "sales"},
        validation={"schema": {"status": "passed", "report_hash": "sha256:ok"}},
    )
    repository = FileReleaseRepository(tmp_path / "releases")

    assert repository.publish(release) == release
    assert repository.publish(release) == release
    assert repository.get(release.release_id) == release

    tampered = release.to_dict()
    tampered["scope"]["tenant_id"] = "other"
    with pytest.raises(ReleaseError, match="release_digest"):
        KnowledgeRelease.from_dict(tampered)

    changed_resource = SemanticResource.create(
        resource_id=resource.resource_id,
        kind=resource.kind,
        name=resource.name,
        domain=resource.domain,
        owner=resource.owner,
        description="changed",
    )
    conflicting = KnowledgeRelease.build(release_id=release.release_id, resources=[changed_resource])
    with pytest.raises(ReleaseError, match="cannot be overwritten"):
        repository.publish(conflicting)
