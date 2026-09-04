"""Executable consumption contracts for compiled semantic targets."""

from __future__ import annotations

from bridge.semantic_core import KnowledgeRelease, ResourceKind, SemanticResource
from bridge.semantic_core.compilers import default_compiler_registry
from bridge.semantic_core.runtime import SemanticRuntimeConsumer


def test_compiled_targets_are_verified_and_executable(tmp_path) -> None:
    ontology = SemanticResource.create(resource_id="aof://acme/demo/ontology/demo", kind=ResourceKind.ONTOLOGY, name="demo", domain="demo", owner="team", spec={"format": "turtle", "content": "@prefix ex: <https://e/> . ex:Customer a ex:Entity ."})
    shapes = SemanticResource.create(resource_id="aof://acme/demo/constraint-set/demo", kind=ResourceKind.CONSTRAINT_SET, name="demo", domain="demo", owner="team", depends_on=[ontology.resource_id], spec={"format": "turtle", "content": "@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix ex: <https://e/> . ex:S a sh:NodeShape ."})
    rule = SemanticResource.create(resource_id="aof://acme/demo/rule-set/search", kind=ResourceKind.RULE_SET, name="search", domain="demo", owner="team", spec={"language": "datalog", "program": "searchable(X) :- indexed(X)."})
    concept = SemanticResource.create(resource_id="aof://acme/demo/concept/customer", kind=ResourceKind.CONCEPT, name="customer", domain="demo", owner="team", description="Customer account")
    profile = SemanticResource.create(resource_id="aof://acme/demo/retrieval-profile/demo", kind=ResourceKind.RETRIEVAL_PROFILE, name="demo", domain="demo", owner="team", spec={"strategy": "hybrid"})
    resources = [ontology, shapes, rule, concept, profile]
    release = KnowledgeRelease.build(release_id="runtime-demo@1.0.0", resources=resources)
    registry = default_compiler_registry()
    consumer = SemanticRuntimeConsumer(registry)
    results = {}
    for target in ("owl", "shacl", "datalog", "rag", "mcp"):
        output = tmp_path / target
        artifact = registry.compile(target, release, output, resources=resources)
        results[target] = consumer.consume(
            artifact, output, release=release, query="customer", facts=[("indexed", ["customer"])],
        )

    assert results["owl"]["triple_count"] > 0
    assert results["shacl"]["shape_count"] == 1
    assert results["datalog"]["derived_count"] == 1
    assert results["rag"]["hits"][0]["resource_id"] == concept.resource_id
    assert results["mcp"]["resource_count"] == len(resources)
