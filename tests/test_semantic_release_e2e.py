# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
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


def _real_resources() -> list[SemanticResource]:
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
    return resources


def test_real_repository_assets_publish_as_one_signed_multi_runtime_release(tmp_path) -> None:
    resources = _real_resources()

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


def test_real_repository_assets_compile_replay_and_promote_through_signed_rest(
    tmp_path, monkeypatch
) -> None:
    from fastapi.testclient import TestClient

    import services.semantic_middle_layer_api.app as api_module
    from bridge.semantic_core import KnowledgeRelease, SignedPrincipalVerifier

    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "real-assets-identity")
    resources = _real_resources()
    release = KnowledgeRelease.build(
        release_id="genshin-compiler@e2e.1",
        resources=resources,
        scope={"tenant_id": "acme"},
    )
    targets = ["semantic-json", "owl", "shacl", "datalog", "rag", "mcp"]
    policy = SemanticResource.create(
        resource_id="aof://acme/platform/policy/compiler-production",
        kind=ResourceKind.POLICY,
        name="compiler-production",
        domain="platform",
        owner="platform-governance",
        spec={
            "policy_type": "compiler",
            "allowed_compilers": {target: [f"{target}@1"] for target in targets},
            "required_targets": targets,
        },
    )
    payload = {
        "release": release.to_dict(),
        "resources": [resource.to_dict() for resource in resources],
        "policy": policy.to_dict(),
        "targets": targets,
    }
    verifier = SignedPrincipalVerifier(
        key_id="identity-key-default", secret=b"real-assets-identity"
    )

    def headers(role: str, subject: str) -> dict[str, str]:
        return verifier.sign_headers(subject=subject, tenant_id="acme", roles=[role])

    client = TestClient(api_module.app)
    plan = client.post(
        "/v1/semantic/compiler/plan", json=payload, headers=headers("compiler", "real-ci")
    )
    report = client.post(
        "/v1/semantic/compiler/evaluate", json=payload, headers=headers("compiler", "real-ci")
    )
    assert plan.status_code == 200 and plan.json()["valid"] is True
    assert report.status_code == 200 and report.json()["conforms"] is True

    first = client.post(
        "/v1/semantic/compiler/runs",
        json={
            **payload,
            "run_id": "genshin-real-001",
            "expected_plan_digest": plan.json()["plan_digest"],
            "rationale": "Compile all repository semantic assets.",
        },
        headers=headers("compiler", "real-ci"),
    )
    replay = client.post(
        "/v1/semantic/compiler/runs/replay",
        json={
            **payload,
            "source_run_id": "genshin-real-001",
            "run_id": "genshin-real-002",
            "rationale": "Independently reproduce every runtime artifact.",
        },
        headers=headers("compiler", "replay-ci"),
    )
    assert first.status_code == 201
    assert replay.status_code == 201 and replay.json()["reproducible"] is True
    assert {artifact["target"] for artifact in replay.json()["artifacts"]} == set(targets)

    approval = client.post(
        "/v1/semantic/compiler/channels/approvals",
        json={
            "run_id": "genshin-real-002",
            "channel": "production",
            "rationale": "All six targets reproduced from frozen real assets.",
        },
        headers=headers("reviewer", "ontology-reviewer"),
    )
    pointer = client.post(
        "/v1/semantic/compiler/channels/promote",
        json={
            "run_id": "genshin-real-002",
            "channel": "production",
            "approval_decision_id": approval.json()["decision"]["id"],
            "rationale": "Promote only the independently reproduced run.",
        },
        headers=headers("publisher", "release-operator"),
    )
    assert approval.status_code == 201
    assert pointer.status_code == 200 and pointer.json()["run_id"] == "genshin-real-002"
    artifact_root = tmp_path / "data" / "semantic_compiler" / "acme" / "artifacts" / "genshin-real-002"
    assert all((artifact_root / item["target"] / item["uri"]).is_file() for item in replay.json()["artifacts"])
