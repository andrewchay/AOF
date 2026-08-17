"""Deep public contracts for deterministic Semantic IR compiler plugins."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..canonical import canonical_data
from ..models import ResourceKind
from ..releases import KnowledgeRelease


class CompilerError(ValueError):
    """Raised when a compiler violates the release compiler contract."""


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _frozen_mapping(value: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    data = canonical_data(dict(value or {}))

    def freeze(item: Any) -> Any:
        if isinstance(item, dict):
            return MappingProxyType({key: freeze(inner) for key, inner in item.items()})
        if isinstance(item, list):
            return tuple(freeze(inner) for inner in item)
        return item

    return freeze(data)


@dataclass(frozen=True)
class VerificationReport:
    valid: bool
    findings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledArtifact:
    target: str
    uri: str
    media_type: str
    content_hash: str
    compiler: str
    release_digest: str
    input_revisions: tuple[str, ...]
    metadata: Mapping[str, Any] = MappingProxyType({})

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "uri": self.uri,
            "media_type": self.media_type,
            "content_hash": self.content_hash,
            "compiler": self.compiler,
            "release_digest": self.release_digest,
            "input_revisions": list(self.input_revisions),
            "metadata": canonical_data(self.metadata),
        }


class SemanticCompiler(ABC):
    """A compiler must explicitly classify every resource kind in a release."""

    target: str = ""
    version: str = ""
    supported_kinds: frozenset[ResourceKind] = frozenset()
    ignored_kinds: frozenset[ResourceKind] = frozenset()

    def validate(self, release: KnowledgeRelease) -> VerificationReport:
        classified = {kind.value for kind in self.supported_kinds | self.ignored_kinds}
        unclassified = sorted({item.kind for item in release.resources if item.kind not in classified})
        if unclassified:
            return VerificationReport(
                False,
                (f"unclassified resource kinds for {self.target}: {', '.join(unclassified)}",),
            )
        return VerificationReport(True)

    @abstractmethod
    def compile(self, release: KnowledgeRelease, output_dir: Path) -> CompiledArtifact:
        raise NotImplementedError

    def artifact(
        self,
        release: KnowledgeRelease,
        path: Path,
        *,
        media_type: str,
        uri: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CompiledArtifact:
        if not path.is_file():
            raise CompilerError(f"compiler output does not exist: {path}")
        return CompiledArtifact(
            target=self.target,
            uri=uri or path.name,
            media_type=media_type,
            content_hash=_file_digest(path),
            compiler=f"{self.target}@{self.version}",
            release_digest=release.release_digest,
            input_revisions=tuple(item.revision_id for item in release.resources),
            metadata=_frozen_mapping(metadata),
        )


class CompilerRegistry:
    def __init__(self, compilers: Iterable[SemanticCompiler] = ()) -> None:
        self._compilers: dict[str, SemanticCompiler] = {}
        for compiler in compilers:
            self.register(compiler)

    def register(self, compiler: SemanticCompiler) -> None:
        if not compiler.target or not compiler.version:
            raise CompilerError("compiler target and version must be non-empty")
        if compiler.target in self._compilers:
            raise CompilerError(f"compiler target already registered: {compiler.target}")
        overlap = compiler.supported_kinds & compiler.ignored_kinds
        if overlap:
            raise CompilerError(
                "resource kinds cannot be both supported and ignored: "
                + ", ".join(sorted(kind.value for kind in overlap))
            )
        self._compilers[compiler.target] = compiler

    def compile(self, target: str, release: KnowledgeRelease, output_dir: str | Path) -> CompiledArtifact:
        compiler = self._compilers.get(target)
        if compiler is None:
            raise CompilerError(f"compiler target is not registered: {target}")
        validation = compiler.validate(release)
        if not validation.valid:
            raise CompilerError("; ".join(validation.findings))
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        artifact = compiler.compile(release, root)
        self._validate_artifact_contract(compiler, release, artifact)
        verification = self.verify(artifact, root, release=release)
        if not verification.valid:
            raise CompilerError("; ".join(verification.findings))
        return artifact

    def verify(
        self,
        artifact: CompiledArtifact,
        output_dir: str | Path,
        *,
        release: KnowledgeRelease | None = None,
    ) -> VerificationReport:
        root = Path(output_dir).resolve()
        path = (root / artifact.uri).resolve()
        findings = []
        try:
            path.relative_to(root)
        except ValueError:
            findings.append("artifact uri escapes compiler output directory")
            return VerificationReport(False, tuple(findings))
        if not path.is_file():
            findings.append(f"artifact file is missing: {artifact.uri}")
        elif _file_digest(path) != artifact.content_hash:
            findings.append(f"artifact content hash mismatch: {artifact.uri}")
        if release is not None:
            if artifact.release_digest != release.release_digest:
                findings.append("artifact release digest mismatch")
            expected_revisions = tuple(item.revision_id for item in release.resources)
            if artifact.input_revisions != expected_revisions:
                findings.append("artifact input revisions mismatch")
        return VerificationReport(not findings, tuple(findings))

    @staticmethod
    def _validate_artifact_contract(
        compiler: SemanticCompiler, release: KnowledgeRelease, artifact: CompiledArtifact
    ) -> None:
        if artifact.target != compiler.target:
            raise CompilerError("compiler returned an artifact for a different target")
        if artifact.compiler != f"{compiler.target}@{compiler.version}":
            raise CompilerError("compiler identity does not match registered plugin")
        if artifact.release_digest != release.release_digest:
            raise CompilerError("compiler returned an artifact for a different release")
