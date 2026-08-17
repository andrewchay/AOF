"""Behavioral contracts for deterministic Semantic IR compilers."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridge.semantic_core import KnowledgeRelease, ResourceKind, SemanticResource, canonical_json
from bridge.semantic_core.compilers import (
    CompiledArtifact,
    CompilationInput,
    CompilerError,
    CompilerRegistry,
    SemanticCompiler,
    default_compiler_registry,
)


class DemoCompiler(SemanticCompiler):
    target = "demo-json"
    version = "1"
    supported_kinds = frozenset({ResourceKind.CONCEPT})

    def compile(self, compilation: CompilationInput, output_dir: Path) -> CompiledArtifact:
        path = output_dir / "concepts.json"
        path.write_text(
            canonical_json([item.to_dict() for item in compilation.resources]) + "\n", encoding="utf-8"
        )
        return self.artifact(compilation, path, media_type="application/json")


def _release() -> tuple[KnowledgeRelease, list[SemanticResource]]:
    concept = SemanticResource.create(
        resource_id="aof://acme/sales/concept/customer",
        kind=ResourceKind.CONCEPT,
        name="customer",
        domain="sales",
        owner="knowledge-team",
    )
    return KnowledgeRelease.build(release_id="sales-knowledge@2026.08.17.1", resources=[concept]), [concept]


def test_compiler_artifact_is_bound_to_release_and_reproducible(tmp_path) -> None:
    registry = CompilerRegistry([DemoCompiler()])
    release, resources = _release()

    first = registry.compile("demo-json", release, tmp_path / "first", resources=resources)
    second = registry.compile("demo-json", release, tmp_path / "second", resources=resources)

    assert first.content_hash == second.content_hash
    assert first.release_digest == release.release_digest
    assert first.input_revisions == tuple(item.revision_id for item in release.resources)
    assert registry.verify(first, tmp_path / "first", release=release).valid is True


def test_compiler_must_classify_every_resource_kind(tmp_path) -> None:
    metric = SemanticResource.create(
        resource_id="aof://acme/sales/metric/gmv",
        kind=ResourceKind.METRIC,
        name="gmv",
        domain="sales",
        owner="data-platform",
    )
    release = KnowledgeRelease.build(release_id="sales-knowledge@2026.08.17.2", resources=[metric])
    registry = CompilerRegistry([DemoCompiler()])

    with pytest.raises(CompilerError, match="unclassified resource kinds.*Metric"):
        registry.compile("demo-json", release, tmp_path, resources=[metric])


def test_compiler_requires_full_frozen_resource_revisions(tmp_path) -> None:
    registry = CompilerRegistry([DemoCompiler()])
    release, resources = _release()
    replacement = SemanticResource.create(
        resource_id=resources[0].resource_id,
        kind=ResourceKind.CONCEPT,
        name=resources[0].name,
        domain=resources[0].domain,
        owner=resources[0].owner,
        description="A different revision must never compile under the old manifest.",
    )

    with pytest.raises(CompilerError, match="do not match the release revision set"):
        registry.compile("demo-json", release, tmp_path, resources=[replacement])


def test_artifact_verification_rejects_tampering_and_path_escape(tmp_path) -> None:
    registry = CompilerRegistry([DemoCompiler()])
    release, resources = _release()
    output_dir = tmp_path / "output"
    artifact = registry.compile("demo-json", release, output_dir, resources=resources)

    (output_dir / artifact.uri).write_text("tampered\n", encoding="utf-8")
    report = registry.verify(artifact, output_dir, release=release)
    assert report.valid is False
    assert "content hash mismatch" in report.findings[0]

    escaped = CompiledArtifact(
        target=artifact.target,
        uri="../outside.json",
        media_type=artifact.media_type,
        content_hash=artifact.content_hash,
        compiler=artifact.compiler,
        release_digest=artifact.release_digest,
        input_revisions=artifact.input_revisions,
    )
    report = registry.verify(escaped, output_dir, release=release)
    assert report.valid is False
    assert report.findings == ("artifact uri escapes compiler output directory",)


def test_default_registry_compiles_governed_release_to_all_runtime_targets(tmp_path) -> None:
    ontology = SemanticResource.create(
        resource_id="aof://acme/sales/ontology/sales",
        kind=ResourceKind.ONTOLOGY,
        name="sales",
        domain="sales",
        owner="knowledge-team",
        spec={"format": "turtle", "content": "@prefix ex: <https://example.test/> . ex:Order a ex:Entity ."},
    )
    shapes = SemanticResource.create(
        resource_id="aof://acme/sales/constraint-set/sales-shapes",
        kind=ResourceKind.CONSTRAINT_SET,
        name="sales-shapes",
        domain="sales",
        owner="knowledge-team",
        depends_on=[ontology.resource_id],
        spec={"format": "turtle", "content": "@prefix sh: <http://www.w3.org/ns/shacl#> ."},
    )
    rules = SemanticResource.create(
        resource_id="aof://acme/sales/rule-set/access",
        kind=ResourceKind.RULE_SET,
        name="access",
        domain="sales",
        owner="knowledge-team",
        spec={"language": "datalog", "program": "allowed(X) :- employee(X)."},
    )
    retrieval = SemanticResource.create(
        resource_id="aof://acme/sales/retrieval-profile/sales",
        kind=ResourceKind.RETRIEVAL_PROFILE,
        name="sales",
        domain="sales",
        owner="knowledge-team",
        spec={"strategy": "hybrid", "top_k": 12},
    )
    release_resources = [ontology, shapes, rules, retrieval]
    release = KnowledgeRelease.build(release_id="sales-runtime@1.0.0", resources=release_resources)
    registry = default_compiler_registry()

    artifacts = {
        target: registry.compile(
            target, release, tmp_path / "first" / target, resources=release_resources
        )
        for target in ("owl", "shacl", "datalog", "rag", "mcp")
    }
    repeated = {
        target: registry.compile(
            target, release, tmp_path / "second" / target, resources=reversed(release_resources)
        )
        for target in artifacts
    }

    assert set(artifacts) == {"owl", "shacl", "datalog", "rag", "mcp"}
    assert all(artifact.release_digest == release.release_digest for artifact in artifacts.values())
    assert all(artifacts[target].content_hash == repeated[target].content_hash for target in artifacts)
    assert (tmp_path / "first" / "datalog" / artifacts["datalog"].uri).read_text().endswith("\n")


def test_compile_plan_is_deterministic_dependency_closed_and_version_locked() -> None:
    release, resources = _release()
    registry = default_compiler_registry()

    first = registry.plan(release, resources=resources, targets=["mcp", "semantic-json"])
    second = registry.plan(release, resources=reversed(resources), targets=["semantic-json", "mcp"])

    assert first.valid is True
    assert first.plan_digest == second.plan_digest
    assert [step.target for step in first.steps] == ["semantic-json", "mcp"]
    assert first.compiler_lock == {"mcp": "mcp@1", "semantic-json": "semantic-json@1"}
    assert first.steps[1].depends_on == ("semantic-json",)
    assert first.steps[1].input_revisions == tuple(item.revision_id for item in release.resources)

    invalid = registry.plan(release, resources=resources, targets=["unknown-runtime"])
    assert invalid.valid is False
    assert invalid.diagnostics[0]["code"] == "compiler_target_not_registered"
