"""End-to-end governance contracts for Semantic IR Knowledge Releases."""

from __future__ import annotations

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import ResourceKind, SemanticResource
from bridge.semantic_core.compilers import CompilerRegistry, SemanticBundleCompiler
import pytest

from bridge.semantic_core.governance import (
    SemanticFinding,
    SemanticGovernanceError,
    SemanticGovernanceService,
)


def _resources() -> list[SemanticResource]:
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
    return [dataset, metric]


def test_semantic_proposal_compiles_publishes_and_records_causal_decisions(tmp_path) -> None:
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    service = SemanticGovernanceService(
        tmp_path / "semantic-governance",
        decision_store=decisions,
        compiler_registry=CompilerRegistry([SemanticBundleCompiler()]),
    )
    proposal = service.create_proposal(
        proposal_id="sales-release-001",
        release_id="sales-knowledge@2026.08.17.1",
        resources=_resources(),
        actor="editor:alice",
        rationale="Publish governed sales semantics.",
    )
    assert proposal["state"] == "proposed"

    review = service.validate(proposal["proposal_id"], actor="validator:system")
    assert review["conforms"] is True
    assert service.impact(proposal["proposal_id"])["resource_count"] == 2
    approval = service.approve(
        proposal["proposal_id"], actor="reviewer:bob", rationale="Validation passed."
    )
    assert approval["state"] == "approved"
    compiled = service.compile(proposal["proposal_id"], actor="compiler:system", targets=["semantic-json"])
    assert compiled["state"] == "ready"
    assert compiled["artifacts"][0]["target"] == "semantic-json"
    published = service.publish(proposal["proposal_id"], actor="publisher:carol")
    assert published["state"] == "published"
    assert published["release"]["release_digest"]

    trail = decisions.audit_trail(published["publish_decision_id"])
    decision_types = {node["decision"]["decision_type"] for node in trail["causal_chain"]["nodes"]}
    assert decision_types == {
        "semantic_proposal",
        "semantic_validation",
        "semantic_approval",
        "semantic_compile",
        "semantic_publish",
    }
    assert trail["integrity"]["valid"] is True


def test_blocking_finding_requires_explicit_waiver_before_approval(tmp_path) -> None:
    def domain_validator(resources):
        assert resources
        return [
            SemanticFinding(
                finding_id="finding:legacy-gmv",
                validator="sales-policy",
                severity="violation",
                message="Legacy GMV definition requires time-bounded exception.",
                resource_id="aof://acme/sales/metric/gmv",
                waiver_allowed=True,
            )
        ]

    service = SemanticGovernanceService(
        tmp_path / "governance",
        validators=[domain_validator],
        compiler_registry=CompilerRegistry([SemanticBundleCompiler()]),
    )
    proposal = service.create_proposal(
        proposal_id="sales-release-waiver",
        release_id="sales-knowledge@2026.08.17.2",
        resources=_resources(),
        actor="editor",
        rationale="Legacy migration.",
    )
    review = service.validate(proposal["proposal_id"], actor="validator")
    assert review["state"] == "conflict_review"
    with pytest.raises(SemanticGovernanceError, match="unresolved findings"):
        service.approve(proposal["proposal_id"], actor="reviewer", rationale="premature")

    waiver = service.waive_finding(
        proposal["proposal_id"],
        finding_id="finding:legacy-gmv",
        actor="risk-owner",
        rationale="Migration ticket SEM-42 expires before the next release.",
        policy="policy:legacy-semantic-waiver",
    )
    assert waiver["waiver_id"].startswith("waiver:")
    assert service.approve(
        proposal["proposal_id"], actor="reviewer", rationale="Documented exception accepted."
    )["state"] == "approved"


def test_request_changes_is_a_terminal_proposal_outcome(tmp_path) -> None:
    service = SemanticGovernanceService(tmp_path / "governance")
    proposal = service.create_proposal(
        proposal_id="sales-release-rework",
        release_id="sales-knowledge@2026.08.17.3",
        resources=_resources(),
        actor="editor",
        rationale="Review candidate.",
    )
    service.validate(proposal["proposal_id"], actor="validator")
    changed = service.request_changes(
        proposal["proposal_id"], actor="reviewer", rationale="Metric owner must clarify refunds."
    )
    assert changed["state"] == "changes_requested"
    with pytest.raises(SemanticGovernanceError, match="cannot approve"):
        service.approve(proposal["proposal_id"], actor="reviewer", rationale="stale")


def test_semantic_governance_rest_api_publishes_immutable_release(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    import services.semantic_middle_layer_api.app as api_module

    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    client = TestClient(api_module.app)
    created = client.post(
        "/v1/semantic/proposals",
        json={
            "proposal_id": "sales-api-001",
            "release_id": "sales-knowledge@2026.08.17.4",
            "resources": [resource.to_dict() for resource in _resources()],
            "actor": "editor:api",
            "rationale": "Publish sales semantics through the governed API.",
        },
    )
    assert created.status_code == 201
    assert created.json()["state"] == "proposed"

    proposal_id = created.json()["proposal_id"]
    assert client.post(
        f"/v1/semantic/proposals/{proposal_id}/validate", json={"actor": "validator:api"}
    ).json()["conforms"] is True
    assert client.get(f"/v1/semantic/proposals/{proposal_id}/impact").json()["resource_count"] == 2
    assert client.post(
        f"/v1/semantic/proposals/{proposal_id}/approve",
        json={"actor": "reviewer:api", "rationale": "Validation passed."},
    ).json()["state"] == "approved"
    assert client.post(
        f"/v1/semantic/proposals/{proposal_id}/compile",
        json={"actor": "compiler:api", "targets": ["semantic-json"]},
    ).json()["state"] == "ready"
    published = client.post(
        f"/v1/semantic/proposals/{proposal_id}/publish", json={"actor": "publisher:api"}
    )
    assert published.status_code == 200
    assert published.json()["state"] == "published"

    release_id = published.json()["release_id"]
    release = client.get(f"/v1/semantic/releases/{release_id}")
    assert release.status_code == 200
    assert release.json()["release_digest"] == published.json()["release"]["release_digest"]
    assert client.post(
        f"/v1/semantic/proposals/{proposal_id}/approve",
        json={"actor": "reviewer:api", "rationale": "Replay stale transition."},
    ).status_code == 409


def test_rest_proposal_runs_default_shacl_owl_skos_release_gate(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    import services.semantic_middle_layer_api.app as api_module

    ontology = SemanticResource.create(
        resource_id="aof://acme/people/ontology/people",
        kind=ResourceKind.ONTOLOGY,
        name="people",
        domain="people",
        owner="ontology-team",
        spec={
            "format": "turtle",
            "content": """
                @prefix ex: <https://example.test/> .
                @prefix owl: <http://www.w3.org/2002/07/owl#> .
                ex:Person a owl:Class .
                ex:alice a ex:Person .
            """,
        },
    )
    shapes = SemanticResource.create(
        resource_id="aof://acme/people/constraint-set/people-shapes",
        kind=ResourceKind.CONSTRAINT_SET,
        name="people-shapes",
        domain="people",
        owner="ontology-team",
        depends_on=[ontology.resource_id],
        spec={
            "format": "turtle",
            "content": """
                @prefix ex: <https://example.test/> .
                @prefix sh: <http://www.w3.org/ns/shacl#> .
                ex:PersonShape a sh:NodeShape ; sh:targetClass ex:Person ;
                  sh:property [ sh:path ex:name ; sh:minCount 1 ] .
            """,
        },
    )
    vocabulary = SemanticResource.create(
        resource_id="aof://acme/people/vocabulary/people-vocabulary",
        kind=ResourceKind.VOCABULARY,
        name="people-vocabulary",
        domain="people",
        owner="ontology-team",
        depends_on=[ontology.resource_id],
        spec={
            "format": "turtle",
            "content": """
                @prefix ex: <https://example.test/> .
                @prefix skos: <http://www.w3.org/2004/02/skos/core#> .
                ex:person a skos:Concept ; skos:prefLabel "Person"@en .
            """,
        },
    )

    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    client = TestClient(api_module.app)
    created = client.post("/v1/semantic/proposals", json={
        "proposal_id": "people-invalid-shacl",
        "release_id": "people-knowledge@1.0.0",
        "resources": [item.to_dict() for item in (ontology, shapes, vocabulary)],
        "actor": "editor:api",
        "rationale": "Exercise the default ontology release gate.",
    })
    assert created.status_code == 201

    review = client.post(
        "/v1/semantic/proposals/people-invalid-shacl/validate",
        json={"actor": "validator:api"},
    )
    assert review.status_code == 200
    assert review.json()["state"] == "conflict_review"
    assert any(
        item["validator"] == "ontology-shacl-owl-skos"
        and item["details"]["constraint_component"] == "MinCountConstraintComponent"
        for item in review.json()["findings"]
    )
    approval = client.post(
        "/v1/semantic/proposals/people-invalid-shacl/approve",
        json={"actor": "reviewer:api", "rationale": "Must remain blocked."},
    )
    assert approval.status_code == 409


def test_default_ontology_gate_reports_owl_and_skos_conflicts() -> None:
    from bridge.semantic_core.validators import ontology_release_validator

    ontology = SemanticResource.create(
        resource_id="aof://acme/people/ontology/conflicted-people",
        kind=ResourceKind.ONTOLOGY,
        name="conflicted-people",
        domain="people",
        owner="ontology-team",
        spec={"format": "turtle", "content": """
            @prefix ex: <https://example.test/> .
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            ex:Employee owl:disjointWith ex:Customer .
            ex:alice a ex:Employee, ex:Customer .
        """},
    )
    vocabulary = SemanticResource.create(
        resource_id="aof://acme/people/vocabulary/conflicted-people",
        kind=ResourceKind.VOCABULARY,
        name="conflicted-people",
        domain="people",
        owner="ontology-team",
        depends_on=[ontology.resource_id],
        spec={"format": "turtle", "content": """
            @prefix ex: <https://example.test/> .
            @prefix skos: <http://www.w3.org/2004/02/skos/core#> .
            ex:person a skos:Concept ; skos:broader ex:person .
        """},
    )

    findings = ontology_release_validator((ontology, vocabulary))
    assert {finding.details["type"] for finding in findings} == {"owl_conflict", "skos_conflict"}
