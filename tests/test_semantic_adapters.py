"""Compatibility contracts for converting existing AOF assets to Semantic IR."""

from __future__ import annotations

from bridge.semantic_core import KnowledgeRelease, ResourceKind
import hashlib

from bridge.semantic_core.adapters import (
    AdapterContext,
    adapt_mapping_library,
    adapt_okf_bundle,
    adapt_ontology_release,
    adapt_ruleset_release,
)


def test_mapping_adapter_builds_dependency_closed_semantic_resources() -> None:
    context = AdapterContext(tenant="acme", domain="sales", owner="data-platform")
    resources = adapt_mapping_library(
        {
            "metrics": [
                {
                    "metric_id": "gmv",
                    "cn_name": "商品交易总额",
                    "agg": "sum",
                    "measure": "paid_amount",
                    "base_table": "dwd.order_detail",
                    "required_filters": ["is_paid = 1"],
                }
            ],
            "dimensions": [
                {
                    "business_dim": "customer_id",
                    "physical_field": "dwd.order_detail.customer_id",
                    "type": "string",
                }
            ],
            "terms": [{"term": "成交金额", "canonical_metric": "gmv", "aliases": ["GMV"]}],
            "sql_patterns": [
                {
                    "pattern_id": "metric_by_date_v1",
                    "intent": "按日期统计指标",
                    "sql_template": "SELECT ${date}, ${metric} FROM ${table}",
                }
            ],
        },
        context=context,
    )

    by_kind = {}
    for resource in resources:
        by_kind.setdefault(resource.kind, []).append(resource)
    assert len(by_kind[ResourceKind.PHYSICAL_DATASET]) == 1
    assert len(by_kind[ResourceKind.METRIC]) == 1
    assert len(by_kind[ResourceKind.DIMENSION]) == 1
    assert len(by_kind[ResourceKind.CONCEPT]) == 1
    assert len(by_kind[ResourceKind.QUERY_TEMPLATE]) == 1
    KnowledgeRelease.build(release_id="sales-knowledge@legacy.1", resources=resources)


def test_ontology_adapter_preserves_source_hashes_and_relationships() -> None:
    contents = {
        "ontology.ttl": "@prefix ex: <https://example.test/> . ex:Customer a <http://www.w3.org/2002/07/owl#Class> .",
        "shapes.ttl": "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "skos.ttl": "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
    }
    release = {
        "ontology_id": "sales",
        "ontology_version": "v-legacy",
        "content_hashes": {
            name: hashlib.sha256(content.encode("utf-8")).hexdigest() for name, content in contents.items()
        },
        "approval_decision_id": "decision:approve",
        "publish_decision_id": "decision:publish",
    }
    resources = adapt_ontology_release(
        release,
        contents=contents,
        context=AdapterContext(tenant="acme", domain="sales", owner="ontology-team"),
    )

    assert {resource.kind for resource in resources} == {
        ResourceKind.ONTOLOGY,
        ResourceKind.CONSTRAINT_SET,
        ResourceKind.VOCABULARY,
    }
    ontology = next(resource for resource in resources if resource.kind == ResourceKind.ONTOLOGY)
    assert ontology.evidence[0]["content_hash"].startswith("sha256:")
    assert all(
        not resource.depends_on or ontology.resource_id in resource.depends_on for resource in resources
    )
    KnowledgeRelease.build(release_id="sales-ontology@legacy.1", resources=resources)


def test_ruleset_and_okf_adapters_preserve_legacy_assets(tmp_path) -> None:
    context = AdapterContext(tenant="acme", domain="sales", owner="knowledge-team")
    ruleset = adapt_ruleset_release(
        {
            "manifest": {
                "ruleset_id": "access",
                "ruleset_version": "rules-abc",
                "content_hash": "legacy-engine-hash",
                "description": "Employee access rules",
            },
            "program": "allowed(X) :- employee(X).",
        },
        context=context,
    )
    bundle = tmp_path / "sales-okf"
    bundle.mkdir()
    (bundle / "index.md").write_text("# Sales knowledge\n", encoding="utf-8")
    concepts = bundle / "concept"
    concepts.mkdir()
    (concepts / "customer.md").write_text("---\ntitle: Customer\n---\nA buyer.\n", encoding="utf-8")
    profile = adapt_okf_bundle(bundle, bundle_id="sales", context=context)

    assert ruleset.kind == ResourceKind.RULE_SET
    assert ruleset.spec["program"] == "allowed(X) :- employee(X)."
    assert profile.kind == ResourceKind.RETRIEVAL_PROFILE
    assert [item["path"] for item in profile.spec["files"]] == ["concept/customer.md", "index.md"]
    KnowledgeRelease.build(release_id="sales-assets@legacy.1", resources=[ruleset, profile])
