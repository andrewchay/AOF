"""Public behavioral contracts for typed intents and deterministic SQL plans."""

from __future__ import annotations

import pytest

from bridge.semantic_core import (
    IntentFilter,
    ResourceKind,
    SemanticIntent,
    SemanticQueryCompileError,
    SemanticResource,
    SemanticSqlCompiler,
)
from bridge.semantic_core.adapters import AdapterContext, adapt_mapping_library


def _sales_resources() -> tuple[SemanticResource, ...]:
    dataset = SemanticResource.create(
        resource_id="aof://acme/sales/physical-dataset/order-detail",
        kind=ResourceKind.PHYSICAL_DATASET,
        name="order-detail",
        domain="sales",
        owner="data-platform",
        spec={"physical_name": "dwd.order_detail"},
    )
    metric = SemanticResource.create(
        resource_id="aof://acme/sales/metric/gmv",
        kind=ResourceKind.METRIC,
        name="gmv",
        domain="sales",
        owner="data-platform",
        depends_on=[dataset.resource_id],
        spec={"aggregation": "sum", "measure": "paid_amount"},
    )
    dimension = SemanticResource.create(
        resource_id="aof://acme/sales/dimension/order-date",
        kind=ResourceKind.DIMENSION,
        name="order-date",
        domain="sales",
        owner="data-platform",
        depends_on=[dataset.resource_id],
        spec={"field": "order_date", "data_type": "date"},
    )
    region = SemanticResource.create(
        resource_id="aof://acme/sales/dimension/region",
        kind=ResourceKind.DIMENSION,
        name="region",
        domain="sales",
        owner="data-platform",
        depends_on=[dataset.resource_id],
        spec={"field": "region", "data_type": "string"},
    )
    return dataset, metric, dimension, region


def test_typed_intent_compiles_to_deterministic_revision_bound_sql() -> None:
    resources = _sales_resources()
    intent = SemanticIntent.create(
        metrics=["aof://acme/sales/metric/gmv"],
        dimensions=["aof://acme/sales/dimension/order-date"],
        purpose="daily-sales-report",
    )
    compiler = SemanticSqlCompiler(resources)

    first = compiler.compile(intent)
    second = compiler.compile(intent)

    assert first.sql == (
        'SELECT "order_date" AS "order_date", SUM("paid_amount") AS "gmv" '
        'FROM "dwd"."order_detail" GROUP BY "order_date"'
    )
    assert first.plan_digest == second.plan_digest
    assert first.intent_digest == intent.intent_digest
    assert first.parameter_values == ()
    assert first.resource_revisions == {
        resource.resource_id: resource.revision_id
        for resource in resources
        if resource.name != "region"
    }
    assert first.to_dict()["api_version"] == "aof.semantic-sql-plan/v1"


def test_semantic_filters_are_parameterized_and_canonicalized() -> None:
    resources = _sales_resources()
    filters = [
        IntentFilter.create(
            dimension="aof://acme/sales/dimension/region",
            operator="eq",
            value="east' OR 1=1 --",
        ),
        IntentFilter.create(
            dimension="aof://acme/sales/dimension/order-date",
            operator="gte",
            value="2026-08-01",
        ),
    ]
    first_intent = SemanticIntent.create(
        metrics=["aof://acme/sales/metric/gmv"],
        dimensions=["aof://acme/sales/dimension/order-date"],
        filters=filters,
        limit=100,
        purpose="daily-sales-report",
    )
    second_intent = SemanticIntent.create(
        metrics=["aof://acme/sales/metric/gmv"],
        dimensions=["aof://acme/sales/dimension/order-date"],
        filters=reversed(filters),
        limit=100,
        purpose="daily-sales-report",
    )

    first = SemanticSqlCompiler(resources).compile(first_intent)
    second = SemanticSqlCompiler(reversed(resources)).compile(second_intent)

    assert first.sql == (
        'SELECT "order_date" AS "order_date", SUM("paid_amount") AS "gmv" '
        'FROM "dwd"."order_detail" '
        'WHERE "order_date" >= :p1 AND "region" = :p2 '
        'GROUP BY "order_date" LIMIT 100'
    )
    assert first.parameter_values == ("2026-08-01", "east' OR 1=1 --")
    assert "OR 1=1" not in first.sql
    assert first_intent.intent_digest == second_intent.intent_digest
    assert first.plan_digest == second.plan_digest


def test_semantic_intent_round_trip_rejects_digest_tampering() -> None:
    intent = SemanticIntent.create(
        metrics=["aof://acme/sales/metric/gmv"],
        dimensions=["aof://acme/sales/dimension/order-date"],
        filters=[
            IntentFilter.create(
                dimension="aof://acme/sales/dimension/region",
                operator="in",
                value=["east", "west"],
            )
        ],
        purpose="daily-sales-report",
    )

    restored = SemanticIntent.from_dict(intent.to_dict())

    assert restored == intent
    tampered = intent.to_dict()
    tampered["purpose"] = "unapproved-export"
    with pytest.raises(SemanticQueryCompileError, match="intent_digest"):
        SemanticIntent.from_dict(tampered)


def test_legacy_mapping_assets_compile_through_canonical_semantic_fields() -> None:
    resources = adapt_mapping_library(
        {
            "metrics": [
                {
                    "metric_id": "gmv",
                    "agg": "sum",
                    "measure": "paid_amount",
                    "base_table": "dwd.order_detail",
                }
            ],
            "dimensions": [
                {
                    "business_dim": "order_date",
                    "physical_field": "dwd.order_detail.order_date",
                    "type": "date",
                }
            ],
        },
        context=AdapterContext(tenant="acme", domain="sales", owner="data-platform"),
    )
    intent = SemanticIntent.create(
        metrics=["aof://acme/sales/metric/gmv"],
        dimensions=["aof://acme/sales/dimension/order_date"],
        purpose="legacy-migration-verification",
    )

    plan = SemanticSqlCompiler(resources).compile(intent)

    assert plan.sql == (
        'SELECT "order_date" AS "order_date", SUM("paid_amount") AS "gmv" '
        'FROM "dwd"."order_detail" GROUP BY "order_date"'
    )
