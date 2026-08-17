"""End-to-end ontology governance and deterministic reasoning contracts."""

from __future__ import annotations

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.ontology_governance import (
    DatalogEngine,
    DatalogError,
    OntologyGovernanceError,
    OntologyGovernanceService,
    RuleSetRepository,
    SparqlService,
)


ONTOLOGY_VALID = """
@prefix ex: <https://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Person a owl:Class .
ex:alice a ex:Person ; ex:name "Alice"^^xsd:string ; ex:age 32 ; ex:manager ex:bob .
ex:bob a ex:Person ; ex:name "Bob"^^xsd:string ; ex:age 45 .
"""

ONTOLOGY_INVALID = ONTOLOGY_VALID.replace('ex:name "Alice"^^xsd:string ; ', "")

SHAPES = """
@prefix ex: <https://example.test/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:PersonShape a sh:NodeShape ;
  sh:targetClass ex:Person ;
  sh:property [ sh:path ex:name ; sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:string ] ;
  sh:property [ sh:path ex:age ; sh:minInclusive 18 ; sh:maxInclusive 80 ] ;
  sh:property [ sh:path ex:manager ; sh:class ex:Person ] .
"""

SKOS_VALID = """
@prefix ex: <https://example.test/> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
ex:employee a skos:Concept ; skos:prefLabel "Employee"@en ; skos:altLabel "Staff"@en .
"""


def _service(tmp_path):
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    return OntologyGovernanceService(tmp_path / "governance", decisions), decisions


def test_shacl_gate_blocks_unresolved_violation_and_preserves_review(tmp_path):
    service, _ = _service(tmp_path)
    draft = service.create_draft(
        ontology_id="people", created_by="editor:alice", ontology_text=ONTOLOGY_INVALID,
        shapes_text=SHAPES, skos_text=SKOS_VALID,
    )
    review = service.validate_draft(draft["draft_id"], actor="validator:shacl")
    assert review["conforms"] is False
    finding = next(item for item in review["findings"] if item["constraint_component"] == "MinCountConstraintComponent")
    assert finding["focus_node"] == "https://example.test/alice"
    assert review["integrity"]["hash"]
    with pytest.raises(OntologyGovernanceError, match="approval blocked"):
        service.approve(draft["draft_id"], approver="reviewer:bob", rationale="approve")


def test_waiver_approval_publish_versions_and_decision_chain(tmp_path):
    service, decisions = _service(tmp_path)
    draft = service.create_draft(
        ontology_id="people", created_by="editor:alice", ontology_text=ONTOLOGY_INVALID,
        shapes_text=SHAPES, skos_text=SKOS_VALID,
    )
    review = service.validate_draft(draft["draft_id"], actor="validator:shacl")
    finding = next(item for item in review["findings"] if item["constraint_component"] == "MinCountConstraintComponent")
    waiver = service.waive_finding(
        draft["draft_id"], finding_id=finding["finding_id"], actor="reviewer:bob",
        rationale="Legacy record; remediation ticket GOV-42 is active.", policy="policy:legacy-data-waiver-v1",
    )
    approval = service.approve(
        draft["draft_id"], approver="reviewer:bob", rationale="Single documented waiver accepted.",
        policies=["policy:ontology-approval-v1"],
    )
    published = service.publish(draft["draft_id"], actor="publisher:carol")
    release = published["release"]
    assert release["ontology_version"].startswith("v-")
    assert release["shape_version"].startswith("shapes-")
    assert release["skos_version"].startswith("skos-")
    assert release["change_set"]["added"]
    assert waiver["waiver_id"] in {item["id"] for item in approval["decision"]["decision"]["evidence"]}
    trail = decisions.audit_trail(published["decision"]["decision"]["id"])
    decision_types = {node["decision"]["decision_type"] for node in trail["causal_chain"]["nodes"]}
    assert decision_types == {"ontology_validation", "ontology_finding_waiver", "ontology_release_approval", "ontology_publish"}
    assert trail["integrity"]["valid"] is True


def test_clean_draft_impact_preview_and_immutable_state_machine(tmp_path):
    service, _ = _service(tmp_path)
    draft = service.create_draft(
        ontology_id="people", created_by="editor:alice", ontology_text=ONTOLOGY_VALID,
        shapes_text=SHAPES, skos_text=SKOS_VALID,
    )
    review = service.validate_draft(draft["draft_id"], actor="validator:shacl")
    assert review["conforms"] is True
    preview = service.impact_preview(draft["draft_id"])
    assert preview["requires_revalidation"] is True
    service.approve(draft["draft_id"], approver="reviewer:bob", rationale="Clean validation.")
    with pytest.raises(OntologyGovernanceError, match="cannot be edited"):
        service.update_draft(draft["draft_id"], actor="editor:alice", ontology_text=ONTOLOGY_INVALID)


def test_conflict_review_can_request_changes_and_requires_new_validation(tmp_path):
    service, decisions = _service(tmp_path)
    draft = service.create_draft(
        ontology_id="people", created_by="editor", ontology_text=ONTOLOGY_INVALID,
        shapes_text=SHAPES, skos_text=SKOS_VALID,
    )
    service.validate_draft(draft["draft_id"], actor="validator")
    request = service.request_changes(draft["draft_id"], reviewer="reviewer", rationale="Restore required name.")
    assert request["manifest"]["state"] == "changes_requested"
    updated = service.update_draft(draft["draft_id"], actor="editor", ontology_text=ONTOLOGY_VALID)
    assert updated["revision"] == 2
    with pytest.raises(OntologyGovernanceError, match="current draft revision"):
        service.approve(draft["draft_id"], approver="reviewer", rationale="stale review")
    assert decisions.get(request["decision"]["decision"]["id"])


def test_skos_conflicts_are_release_blockers(tmp_path):
    service, _ = _service(tmp_path)
    bad_skos = SKOS_VALID.replace('skos:altLabel "Staff"@en', 'skos:prefLabel "Staff"@en')
    draft = service.create_draft(
        ontology_id="people", created_by="editor", ontology_text=ONTOLOGY_VALID,
        shapes_text=SHAPES, skos_text=bad_skos,
    )
    review = service.validate_draft(draft["draft_id"], actor="validator")
    assert any(item["constraint_component"] == "UniquePrefLabelPerLanguage" for item in review["findings"])


def test_datalog_fixed_point_negation_and_proof_are_deterministic(tmp_path):
    program = """
ancestor(X, Y) :- parent(X, Y).
ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).
has_child(X) :- parent(X, Y).
leaf(X) :- person(X), not has_child(X).
"""
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    engine = DatalogEngine(program, ruleset_id="ruleset:kinship")
    facts = [("parent", ["alice", "bob"]), ("parent", ["bob", "carol"]), ("person", ["alice"]), ("person", ["bob"]), ("person", ["carol"])]
    first = engine.run(facts, decision_store=decisions)
    second = engine.run(reversed(facts))
    assert first["derived_facts"] == second["derived_facts"]
    derived = {(item["predicate"], tuple(item["terms"])): item for item in first["derived_facts"]}
    assert ("ancestor", ("alice", "carol")) in derived
    assert ("leaf", ("carol",)) in derived
    proof = derived[("ancestor", ("alice", "carol"))]["proof"]
    assert proof["rule_id"].endswith("rule-3")
    assert proof["ruleset_version"] == engine.ruleset_version
    assert first["decision_id"]


def test_datalog_rejects_unsafe_and_unstratifiable_programs():
    with pytest.raises(DatalogError, match="unsafe"):
        DatalogEngine("result(X) :- not blocked(X).")
    with pytest.raises(DatalogError, match="not stratifiable"):
        DatalogEngine("a(X) :- seed(X), not b(X).\nb(X) :- seed(X), not a(X).")


def test_versioned_ruleset_repository_and_fact_snapshot_provenance(tmp_path):
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    repo = RuleSetRepository(tmp_path / "rulesets", decisions)
    manifest = repo.publish(
        ruleset_id="access", program="allowed(X) :- employee(X).", actor="governor:rules",
        description="Employee access rule v1.",
    )
    assert manifest["ruleset_version"].startswith("rules-")
    result = repo.run("access", manifest["ruleset_version"], [("employee", ["alice"])])
    assert result["derived_facts"][0]["proof"]["ruleset_version"] == manifest["content_hash"]
    assert result["derived_facts"][0]["proof"]["inputs"][0]["fact_id"].startswith("fact:")
    inference = decisions.get(result["decision_id"])["decision"]
    assert any(item["type"] == "fact_snapshot" for item in inference["evidence"])
    with pytest.raises(DatalogError, match="already exists"):
        repo.publish(ruleset_id="access", program="allowed(X) :- employee(X).", actor="governor:rules")


def test_sparql_is_read_only_and_bound_to_snapshot():
    service = SparqlService(ONTOLOGY_VALID, snapshot_id="ontology:people:v1")
    result = service.query("SELECT ?person WHERE { ?person a <https://example.test/Person> } ORDER BY ?person")
    assert result["snapshot_id"] == "ontology:people:v1"
    assert result["count"] == 2
    with pytest.raises(ValueError, match="Update"):
        service.query("DELETE WHERE { ?s ?p ?o }")


def test_governance_rest_api_full_release_and_reasoning(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import services.semantic_middle_layer_api.app as api_module

    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    client = TestClient(api_module.app)
    created = client.post("/v1/ontology/drafts", json={
        "ontology_id": "people", "created_by": "editor:api", "ontology_text": ONTOLOGY_VALID,
        "shapes_text": SHAPES, "skos_text": SKOS_VALID,
    })
    assert created.status_code == 201
    draft_id = created.json()["draft_id"]
    validation = client.post(f"/v1/ontology/drafts/{draft_id}/validate", json={"actor": "validator:api"})
    assert validation.status_code == 200 and validation.json()["conforms"] is True
    approval = client.post(f"/v1/ontology/drafts/{draft_id}/approve", json={"approver": "reviewer:api", "rationale": "Clean."})
    assert approval.status_code == 200
    publication = client.post(f"/v1/ontology/drafts/{draft_id}/publish", json={"actor": "publisher:api"})
    assert publication.status_code == 200
    version = publication.json()["release"]["ontology_version"]
    sparql = client.post(f"/v1/ontology/releases/people/{version}/sparql", json={"query": "ASK { <https://example.test/alice> a <https://example.test/Person> }"})
    assert sparql.status_code == 200 and sparql.json()["boolean"] is True

    reasoning = client.post("/v1/reasoning/datalog/run", json={
        "program": "reachable(X, Y) :- edge(X, Y).", "ruleset_id": "ruleset:api",
        "facts": [{"predicate": "edge", "terms": ["a", "b"]}],
    })
    assert reasoning.status_code == 200
    assert reasoning.json()["derived_facts"][0]["predicate"] == "reachable"
    ruleset = client.post("/v1/reasoning/rulesets", json={
        "ruleset_id": "reachability", "program": "reachable(X, Y) :- edge(X, Y).", "actor": "governor:api",
    })
    assert ruleset.status_code == 201
    ruleset_run = client.post(
        f"/v1/reasoning/rulesets/reachability/{ruleset.json()['ruleset_version']}/run",
        json={"facts": [{"predicate": "edge", "terms": ["a", "b"]}]},
    )
    assert ruleset_run.status_code == 200 and ruleset_run.json()["derived_count"] == 1


def test_governance_mcp_tools_expose_gate_and_reasoner(tmp_path, monkeypatch):
    import asyncio
    import mcp_server

    monkeypatch.setattr(mcp_server, "PROJECT_ROOT", tmp_path)
    server = mcp_server.build_server()
    expected = {
        "aof_ontology_create_draft", "aof_ontology_validate_draft", "aof_ontology_waive_finding",
        "aof_ontology_approve_draft", "aof_ontology_publish_draft", "aof_datalog_reason",
        "aof_ontology_request_changes", "aof_publish_datalog_ruleset", "aof_run_datalog_ruleset",
    }
    assert expected.issubset(server.tools)
    draft = asyncio.run(server.tools["aof_ontology_create_draft"].handler({
        "ontology_id": "people", "created_by": "agent:mcp", "ontology_text": ONTOLOGY_VALID,
        "shapes_text": SHAPES, "skos_text": SKOS_VALID,
    }))
    review = asyncio.run(server.tools["aof_ontology_validate_draft"].handler({"draft_id": draft["draft_id"], "actor": "agent:validator"}))
    assert review["conforms"] is True
    inference = asyncio.run(server.tools["aof_datalog_reason"].handler({
        "program": "reachable(X, Y) :- edge(X, Y).", "facts": [{"predicate": "edge", "terms": ["a", "b"]}],
    }))
    assert inference["derived_count"] == 1


def test_unsupported_shacl_is_a_blocker_and_review_chain_detects_tampering(tmp_path):
    service, _ = _service(tmp_path)
    unsupported = SHAPES.replace("sh:minCount 1", "sh:minCount 1 ; sh:uniqueLang true")
    draft = service.create_draft(
        ontology_id="people", created_by="editor", ontology_text=ONTOLOGY_VALID,
        shapes_text=unsupported, skos_text=SKOS_VALID,
    )
    review = service.validate_draft(draft["draft_id"], actor="validator")
    assert any(item["type"] == "unsupported_shacl_constraint" for item in review["findings"])
    reviews_path = service._draft_dir(draft["draft_id"]) / "reviews.jsonl"
    reviews_path.write_text(reviews_path.read_text(encoding="utf-8").replace("uniqueLang", "ignored", 1), encoding="utf-8")
    with pytest.raises(OntologyGovernanceError, match="integrity"):
        service.approve(draft["draft_id"], approver="reviewer", rationale="must fail")
