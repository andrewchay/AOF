"""Baseline compiler that emits a complete, portable Semantic IR bundle."""

from __future__ import annotations

from pathlib import Path

from ..canonical import canonical_json
from ..models import ResourceKind
from .base import CompilationInput, CompiledArtifact, SemanticCompiler


class SemanticBundleCompiler(SemanticCompiler):
    target = "semantic-json"
    version = "1"
    supported_kinds = frozenset(ResourceKind)

    def compile(self, compilation: CompilationInput, output_dir: Path) -> CompiledArtifact:
        path = output_dir / "semantic-bundle.json"
        payload = {
            "api_version": "aof.semantic-bundle/v1",
            "source_release": compilation.release.to_dict(),
            "resources": [resource.to_dict() for resource in compilation.resources],
        }
        path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
        return self.artifact(compilation, path, media_type="application/json")
