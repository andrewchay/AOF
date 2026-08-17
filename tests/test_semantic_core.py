"""Public contracts for AOF Semantic IR and Knowledge Releases."""

from __future__ import annotations

import pytest

from bridge.semantic_core import ResourceKind, SemanticModelError, SemanticResource


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
