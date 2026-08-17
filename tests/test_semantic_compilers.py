"""Behavioral contracts for deterministic Semantic IR compilers."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridge.semantic_core import KnowledgeRelease, ResourceKind, SemanticResource, canonical_json
from bridge.semantic_core.compilers import CompiledArtifact, CompilerError, CompilerRegistry, SemanticCompiler


class DemoCompiler(SemanticCompiler):
    target = "demo-json"
    version = "1"
    supported_kinds = frozenset({ResourceKind.CONCEPT})

    def compile(self, release: KnowledgeRelease, output_dir: Path) -> CompiledArtifact:
        path = output_dir / "concepts.json"
        path.write_text(canonical_json([item.to_dict() for item in release.resources]) + "\n", encoding="utf-8")
        return self.artifact(release, path, media_type="application/json")


def _release() -> KnowledgeRelease:
    concept = SemanticResource.create(
        resource_id="aof://acme/sales/concept/customer",
        kind=ResourceKind.CONCEPT,
        name="customer",
        domain="sales",
        owner="knowledge-team",
    )
    return KnowledgeRelease.build(release_id="sales-knowledge@2026.08.17.1", resources=[concept])


def test_compiler_artifact_is_bound_to_release_and_reproducible(tmp_path) -> None:
    registry = CompilerRegistry([DemoCompiler()])
    release = _release()

    first = registry.compile("demo-json", release, tmp_path / "first")
    second = registry.compile("demo-json", release, tmp_path / "second")

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
        registry.compile("demo-json", release, tmp_path)


def test_artifact_verification_rejects_tampering_and_path_escape(tmp_path) -> None:
    registry = CompilerRegistry([DemoCompiler()])
    release = _release()
    output_dir = tmp_path / "output"
    artifact = registry.compile("demo-json", release, output_dir)

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
