"""Behavioral contracts for semantic dependency impact and regression gates."""

from __future__ import annotations

from bridge.semantic_core import (
    KnowledgeRelease,
    ResourceKind,
    SemanticGovernanceService,
    SemanticImpactAnalyzer,
    SemanticIntent,
    SemanticResource,
)
from bridge.semantic_core.validators import semantic_query_regression_validator


def _impact_resources(*, aggregation: str) -> tuple[SemanticResource, ...]:
    dataset = SemanticResource.create(
        resource_id="aof://acme/sales/physical-dataset/orders",
        kind=ResourceKind.PHYSICAL_DATASET,
        name="orders",
        domain="sales",
        owner="data-platform",
        spec={"physical_name": "dwd.orders"},
    )
    metric = SemanticResource.create(
        resource_id="aof://acme/sales/metric/gmv",
        kind=ResourceKind.METRIC,
        name="gmv",
        domain="sales",
        owner="data-platform",
        depends_on=[dataset.resource_id],
        spec={"aggregation": aggregation, "measure": "paid_amount"},
    )
    template = SemanticResource.create(
        resource_id="aof://acme/sales/query-template/gmv-report",
        kind=ResourceKind.QUERY_TEMPLATE,
        name="gmv-report",
        domain="sales",
        owner="analytics",
        depends_on=[metric.resource_id],
        spec={"template": "SELECT ${metric} FROM ${table}"},
    )
    retrieval = SemanticResource.create(
        resource_id="aof://acme/sales/retrieval-profile/sales",
        kind=ResourceKind.RETRIEVAL_PROFILE,
        name="sales",
        domain="sales",
        owner="knowledge-team",
        depends_on=[metric.resource_id],
        spec={"strategy": "hybrid"},
    )
    contract = SemanticResource.create(
        resource_id="aof://acme/sales/query-contract/gmv-contract",
        kind=ResourceKind.QUERY_CONTRACT,
        name="gmv-contract",
        domain="sales",
        owner="analytics",
        depends_on=[metric.resource_id],
        spec={"contract_type": "semantic_sql"},
    )
    return dataset, metric, template, retrieval, contract


def test_metric_change_reports_transitive_resources_tools_and_contracts() -> None:
    previous = _impact_resources(aggregation="sum")
    current = _impact_resources(aggregation="avg")

    current_metric = next(item for item in current if item.kind is ResourceKind.METRIC)
    report = SemanticImpactAnalyzer().compare(
        previous,
        current,
        compiled_artifacts=[
            {
                "target": "mcp",
                "content_hash": "sha256:mcp-artifact",
                "input_revisions": [current_metric.revision_id],
            },
            {
                "target": "owl",
                "content_hash": "sha256:owl-artifact",
                "input_revisions": ["sha256:unrelated"],
            },
        ],
    )

    assert report.changed_resource_ids == ("aof://acme/sales/metric/gmv",)
    assert set(report.affected_resource_ids) == {
        "aof://acme/sales/query-contract/gmv-contract",
        "aof://acme/sales/query-template/gmv-report",
        "aof://acme/sales/retrieval-profile/sales",
    }
    assert report.affected_mcp_tools == (
        "mcp-tool:aof://acme/sales/query-template/gmv-report",
        "mcp-tool:aof://acme/sales/retrieval-profile/sales",
    )
    assert report.affected_contract_ids == (
        "aof://acme/sales/query-contract/gmv-contract",
    )
    assert report.affected_artifacts == ("artifact:mcp:sha256:mcp-artifact",)
    assert report.report_digest.startswith("sha256:")


def test_semantic_sql_contract_blocks_regressing_release(tmp_path) -> None:
    base = list(_impact_resources(aggregation="avg"))
    metric = next(item for item in base if item.kind is ResourceKind.METRIC)
    dataset = next(item for item in base if item.kind is ResourceKind.PHYSICAL_DATASET)
    dimension = SemanticResource.create(
        resource_id="aof://acme/sales/dimension/order-date",
        kind=ResourceKind.DIMENSION,
        name="order-date",
        domain="sales",
        owner="data-platform",
        depends_on=[dataset.resource_id],
        spec={"field": "order_date", "data_type": "date"},
    )
    intent = SemanticIntent.create(
        metrics=[metric.resource_id],
        dimensions=[dimension.resource_id],
        purpose="daily-sales-report",
    )
    contract_index = next(
        index for index, item in enumerate(base) if item.kind is ResourceKind.QUERY_CONTRACT
    )
    base[contract_index] = SemanticResource.create(
        resource_id=base[contract_index].resource_id,
        kind=ResourceKind.QUERY_CONTRACT,
        name="gmv-contract",
        domain="sales",
        owner="analytics",
        depends_on=[dataset.resource_id, metric.resource_id, dimension.resource_id],
        spec={
            "contract_type": "semantic_sql",
            "intent": intent.to_dict(),
            "expected_sql": (
                'SELECT "order_date" AS "order_date", SUM("paid_amount") AS "gmv" '
                'FROM "dwd"."orders" GROUP BY "order_date" ORDER BY "order_date"'
            ),
        },
    )
    resources = [*base, dimension]
    service = SemanticGovernanceService(
        tmp_path / "governance",
        validators=[semantic_query_regression_validator],
    )
    proposal = service.create_proposal(
        proposal_id="gmv-regression",
        release_id="sales@2.0.0",
        resources=resources,
        actor="editor:alice",
        rationale="Change GMV aggregation.",
    )

    review = service.validate(proposal["proposal_id"], actor="validator:ci")

    assert review["state"] == "conflict_review"
    assert review["conforms"] is False
    assert review["findings"][0]["validator"] == "semantic-query-regression"
    assert review["findings"][0]["waiver_allowed"] is False
    assert 'AVG("paid_amount")' in review["findings"][0]["details"]["actual_sql"]


def test_proposal_impact_exposes_transitive_semantic_consumers(tmp_path) -> None:
    previous = _impact_resources(aggregation="sum")
    current = _impact_resources(aggregation="avg")
    service = SemanticGovernanceService(tmp_path / "governance")
    parent = KnowledgeRelease.build(
        release_id="sales@1.0.0",
        resources=previous,
        scope={"tenant_id": "acme"},
    )
    service.release_repository.publish(parent)
    proposal = service.create_proposal(
        proposal_id="sales-impact",
        release_id="sales@2.0.0",
        parent_release=parent.release_id,
        resources=current,
        actor="editor:alice",
        rationale="Change GMV aggregation.",
    )

    impact = service.impact(proposal["proposal_id"])

    assert impact["changed"] == ["aof://acme/sales/metric/gmv"]
    assert set(impact["affected_resource_ids"]) == {
        "aof://acme/sales/query-contract/gmv-contract",
        "aof://acme/sales/query-template/gmv-report",
        "aof://acme/sales/retrieval-profile/sales",
    }
    assert impact["impact_report_digest"].startswith("sha256:")


def test_semantic_search_contract_blocks_retrieval_regression(tmp_path) -> None:
    concept = SemanticResource.create(
        resource_id="aof://acme/sales/concept/buyer",
        kind=ResourceKind.CONCEPT,
        name="buyer",
        domain="sales",
        owner="knowledge-team",
        description="A purchasing account.",
    )
    contract = SemanticResource.create(
        resource_id="aof://acme/sales/query-contract/customer-retrieval",
        kind=ResourceKind.QUERY_CONTRACT,
        name="customer-retrieval",
        domain="sales",
        owner="knowledge-team",
        depends_on=[concept.resource_id],
        spec={
            "contract_type": "semantic_search",
            "query": "customer",
            "limit": 10,
            "expected_resource_ids": [concept.resource_id],
        },
    )
    findings = semantic_query_regression_validator((concept, contract))

    assert len(findings) == 1
    assert findings[0].details["code"] == "semantic_search_regression"
    assert findings[0].details["actual_resource_ids"] == ()
    assert findings[0].waiver_allowed is False
