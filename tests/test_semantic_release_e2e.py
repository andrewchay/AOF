"""Repository-asset E2E for the governed Semantic IR release pipeline."""

from __future__ import annotations

from pathlib import Path

import yaml

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import ResourceKind, SemanticResource, SqliteReleaseRepository
from bridge.semantic_core.adapters import (
    AdapterContext,
    adapt_mapping_library,
    adapt_okf_bundle,
    adapt_ruleset_release,
)
from bridge.semantic_core.attestations import HmacReleaseAttestor
from bridge.semantic_core.compilers import default_compiler_registry
from bridge.semantic_core.governance import SemanticGovernancePolicy, SemanticGovernanceService
from bridge.semantic_core.validators import ontology_release_validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_real_repository_assets_publish_as_one_signed_multi_runtime_release(tmp_path) -> None:
    context = AdapterContext(tenant="acme", domain="genshin", owner="knowledge-platform")
    mapping_dir = PROJECT_ROOT / "data" / "middle_layer" / "api_demo_topic" / "mapping"
    library = {
        "metrics": yaml.safe_load((mapping_dir / "metric_catalog.yaml").read_text()),
        "dimensions": yaml.safe_load((mapping_dir / "dimension_mapping.yaml").read_text()),
        "terms": yaml.safe_load((mapping_dir / "term_mapping.yaml").read_text()),
        "sql_patterns": yaml.safe_load((mapping_dir / "sql_pattern_library.yaml").read_text()),
    }
    resources = list(adapt_mapping_library(library, context=context))

    ontology = SemanticResource.create(
        resource_id="aof://acme/genshin/ontology/genshin",
        kind=ResourceKind.ONTOLOGY,
        name="genshin",
        domain="genshin",
        owner="knowledge-platform",
        spec={
            "format": "xml",
            "content": (PROJECT_ROOT / "ontologies" / "genshin_ontology.owl").read_text(),
            "source_path": "ontologies/genshin_ontology.owl",
        },
    )
    resources.extend([
        ontology,
        SemanticResource.create(
            resource_id="aof://acme/genshin/constraint-set/genshin-shapes",
            kind=ResourceKind.CONSTRAINT_SET,
            name="genshin-shapes",
            domain="genshin",
            owner="knowledge-platform",
            depends_on=[ontology.resource_id],
            spec={"format": "turtle", "content": "@prefix sh: <http://www.w3.org/ns/shacl#> ."},
        ),
    ])
    resources.append(adapt_ruleset_release({
        "manifest": {"ruleset_id": "entity-search", "ruleset_version": "e2e-1"},
        "program": (
            PROJECT_ROOT / "examples" / "semantic-release" / "rules" / "entity-search.dl"
        ).read_text(),
    }, context=context))
    okf = PROJECT_ROOT / "examples" / "semantic-release" / "okf"
    resources.append(adapt_okf_bundle(okf, bundle_id="genshin", context=context))

    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    repository = SqliteReleaseRepository(tmp_path / "releases.sqlite3")
    attestor = HmacReleaseAttestor(key_id="e2e-key", secret=b"e2e-only-secret")
    service = SemanticGovernanceService(
        tmp_path / "governance",
        decision_store=decisions,
        compiler_registry=default_compiler_registry(),
        validators=[ontology_release_validator],
        release_repository=repository,
        access_policy=SemanticGovernancePolicy(),
        release_attestor=attestor,
    )
    proposal = service.create_proposal(
        proposal_id="genshin-real-assets",
        release_id="genshin-knowledge@e2e.1",
        resources=resources,
        actor="editor:alice",
        rationale="Publish repository ontology, mappings, and knowledge documents.",
    )
    assert service.validate(proposal["proposal_id"], actor="validator:gate")["conforms"] is True
    service.approve(proposal["proposal_id"], actor="reviewer:bob", rationale="All gates passed.")
    compiled = service.compile(
        proposal["proposal_id"],
        actor="compiler:runtime",
        targets=["semantic-json", "owl", "shacl", "datalog", "rag", "mcp"],
    )
    assert {item["target"] for item in compiled["artifacts"]} == {
        "semantic-json", "owl", "shacl", "datalog", "rag", "mcp",
    }
    published = service.publish(proposal["proposal_id"], actor="publisher:carol")
    release = repository.get(published["release_id"], tenant_id="acme")
    assert release is not None and attestor.verify(published["attestation"], release=release)
    assert decisions.audit_trail(published["publish_decision_id"])["integrity"]["valid"] is True
