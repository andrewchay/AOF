"""Immutable Knowledge Release manifests for jointly runnable semantic revisions."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .canonical import canonical_data, canonical_json, content_digest
from .models import SemanticResource


class ReleaseError(ValueError):
    """Raised when a Knowledge Release cannot be safely built or verified."""


_RELEASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9._-]*$")


def _freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    def freeze(item: Any) -> Any:
        if isinstance(item, Mapping):
            return MappingProxyType({key: freeze(inner) for key, inner in item.items()})
        if isinstance(item, list | tuple):
            return tuple(freeze(inner) for inner in item)
        return item

    return freeze(canonical_data(dict(value or {})))


@dataclass(frozen=True, order=True)
class ResourceRevisionRef:
    resource_id: str
    revision_id: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        return {
            "resource_id": self.resource_id,
            "revision_id": self.revision_id,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class KnowledgeRelease:
    release_id: str
    resources: tuple[ResourceRevisionRef, ...]
    release_digest: str
    parent_release: str | None = None
    # MappingProxyType is immutable but Python 3.11 rejects it as a dataclass
    # default.  A factory keeps the same empty, immutable public contract while
    # letting modules that import releases (including Context Exchange) load.
    scope: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    compiled_artifacts: tuple[Mapping[str, Any], ...] = ()
    validation: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    governance: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    api_version: str = "aof.release/v1"

    @classmethod
    def build(
        cls,
        *,
        release_id: str,
        resources: Iterable[SemanticResource],
        parent_release: str | None = None,
        scope: Mapping[str, Any] | None = None,
        compiled_artifacts: Iterable[Mapping[str, Any]] = (),
        validation: Mapping[str, Any] | None = None,
        governance: Mapping[str, Any] | None = None,
        api_version: str = "aof.release/v1",
    ) -> "KnowledgeRelease":
        _validate_release_id(release_id)
        if parent_release is not None:
            _validate_release_id(parent_release)
            if parent_release == release_id:
                raise ReleaseError("a release cannot be its own parent")
        by_id: dict[str, SemanticResource] = {}
        for resource in resources:
            existing = by_id.get(resource.resource_id)
            if existing is not None and existing.revision_id != resource.revision_id:
                raise ReleaseError(
                    f"multiple revisions selected for resource: {resource.resource_id}"
                )
            by_id[resource.resource_id] = resource
        if not by_id:
            raise ReleaseError(
                "a Knowledge Release requires at least one semantic resource"
            )
        missing = sorted(
            {
                dependency
                for resource in by_id.values()
                for dependency in resource.depends_on
                if dependency not in by_id
            }
        )
        if missing:
            raise ReleaseError(f"missing resource dependencies: {', '.join(missing)}")
        refs = tuple(
            sorted(
                ResourceRevisionRef(
                    resource.resource_id, resource.revision_id, resource.kind.value
                )
                for resource in by_id.values()
            )
        )
        artifacts = _normalize_artifacts(compiled_artifacts)
        frozen_scope = _freeze_mapping(scope)
        frozen_validation = _freeze_mapping(validation)
        frozen_governance = _freeze_mapping(governance)
        payload = _release_payload(
            api_version=api_version,
            release_id=release_id,
            parent_release=parent_release,
            resources=refs,
            scope=frozen_scope,
            compiled_artifacts=artifacts,
            validation=frozen_validation,
            governance=frozen_governance,
        )
        return cls(
            release_id=release_id,
            resources=refs,
            release_digest=content_digest(payload),
            parent_release=parent_release,
            scope=frozen_scope,
            compiled_artifacts=artifacts,
            validation=frozen_validation,
            governance=frozen_governance,
            api_version=api_version,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "KnowledgeRelease":
        release_id = str(value.get("release_id", ""))
        _validate_release_id(release_id)
        parent_release = value.get("parent_release")
        if parent_release is not None:
            _validate_release_id(str(parent_release))
        refs = tuple(
            sorted(
                ResourceRevisionRef(
                    resource_id=str(item["resource_id"]),
                    revision_id=str(item["revision_id"]),
                    kind=str(item["kind"]),
                )
                for item in value.get("resources", [])
            )
        )
        if not refs:
            raise ReleaseError(
                "a Knowledge Release requires at least one semantic resource"
            )
        release = cls(
            release_id=release_id,
            resources=refs,
            release_digest=str(value.get("release_digest", "")),
            parent_release=str(parent_release) if parent_release is not None else None,
            scope=_freeze_mapping(value.get("scope", {})),
            compiled_artifacts=_normalize_artifacts(
                value.get("compiled_artifacts", [])
            ),
            validation=_freeze_mapping(value.get("validation", {})),
            governance=_freeze_mapping(value.get("governance", {})),
            api_version=str(value.get("api_version", "aof.release/v1")),
        )
        if not release.verify():
            raise ReleaseError(
                "release_digest does not match canonical manifest content"
            )
        return release

    def verify(self) -> bool:
        return self.release_digest == content_digest(self._payload())

    def _payload(self) -> dict[str, Any]:
        return _release_payload(
            api_version=self.api_version,
            release_id=self.release_id,
            parent_release=self.parent_release,
            resources=self.resources,
            scope=self.scope,
            compiled_artifacts=self.compiled_artifacts,
            validation=self.validation,
            governance=self.governance,
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "release_digest": self.release_digest}


class FileReleaseRepository:
    """Small local repository with idempotent publication and no overwrite path."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def publish(self, release: KnowledgeRelease) -> KnowledgeRelease:
        if not release.verify():
            raise ReleaseError("cannot publish a release with invalid digest")
        path = self._path(release.release_id)
        if path.exists():
            existing = KnowledgeRelease.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
            if existing.release_digest != release.release_digest:
                raise ReleaseError(
                    f"published release cannot be overwritten: {release.release_id}"
                )
            return existing
        self.root.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(release.to_dict()) + "\n", encoding="utf-8")
        return release

    def get(self, release_id: str) -> KnowledgeRelease | None:
        path = self._path(release_id)
        if not path.exists():
            return None
        return KnowledgeRelease.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _path(self, release_id: str) -> Path:
        _validate_release_id(release_id)
        return self.root / f"{release_id.replace('@', '__')}.json"


class SqliteReleaseRepository:
    """Transactional, tenant-isolated release store with immutable identities."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_releases (
                    tenant_id TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    release_digest TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, release_id)
                )
                """
            )
            connection.execute("PRAGMA user_version=1")

    def publish(self, release: KnowledgeRelease, *, tenant_id: str) -> KnowledgeRelease:
        self._validate_tenant(release, tenant_id)
        if not release.verify():
            raise ReleaseError("cannot publish a release with invalid digest")
        manifest = canonical_json(release.to_dict())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT release_digest, manifest_json FROM knowledge_releases "
                "WHERE tenant_id = ? AND release_id = ?",
                (tenant_id, release.release_id),
            ).fetchone()
            if row is not None:
                if row[0] != release.release_digest:
                    raise ReleaseError(
                        f"published release cannot be overwritten: {release.release_id}"
                    )
                connection.commit()
                return KnowledgeRelease.from_dict(json.loads(row[1]))
            connection.execute(
                "INSERT INTO knowledge_releases "
                "(tenant_id, release_id, release_digest, manifest_json) VALUES (?, ?, ?, ?)",
                (tenant_id, release.release_id, release.release_digest, manifest),
            )
            connection.commit()
            return release
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, release_id: str, *, tenant_id: str) -> KnowledgeRelease | None:
        _validate_release_id(release_id)
        if not tenant_id.strip():
            raise ReleaseError("tenant_id is required")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM knowledge_releases "
                "WHERE tenant_id = ? AND release_id = ?",
                (tenant_id, release_id),
            ).fetchone()
        return None if row is None else KnowledgeRelease.from_dict(json.loads(row[0]))

    def get_unique(self, release_id: str) -> KnowledgeRelease | None:
        """Compatibility lookup; reject ambiguous cross-tenant release identities."""
        _validate_release_id(release_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT manifest_json FROM knowledge_releases WHERE release_id = ?",
                (release_id,),
            ).fetchall()
        if len(rows) > 1:
            raise ReleaseError("tenant_id is required for an ambiguous release_id")
        return None if not rows else KnowledgeRelease.from_dict(json.loads(rows[0][0]))

    def schema_version(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def verify_all(self) -> dict[str, Any]:
        errors = []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT tenant_id, release_id, release_digest, manifest_json FROM knowledge_releases"
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"sqlite integrity check failed: {integrity}")
        for tenant_id, release_id, digest, manifest in rows:
            try:
                release = KnowledgeRelease.from_dict(json.loads(manifest))
                if release.release_id != release_id or release.release_digest != digest:
                    raise ReleaseError(
                        "indexed release identity does not match manifest"
                    )
                if release.scope.get("tenant_id") != tenant_id:
                    raise ReleaseError("indexed tenant does not match manifest")
            except Exception as exc:
                errors.append(f"{tenant_id}/{release_id}: {exc}")
        return {"valid": not errors, "release_count": len(rows), "errors": errors}

    def backup_to(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self._connect()
        backup = sqlite3.connect(target)
        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()
        return target

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _validate_tenant(release: KnowledgeRelease, tenant_id: str) -> None:
        if not tenant_id.strip():
            raise ReleaseError("tenant_id is required")
        if release.scope.get("tenant_id") != tenant_id:
            raise ReleaseError(
                "release scope tenant_id does not match repository tenant_id"
            )


def _validate_release_id(release_id: str) -> None:
    if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
        raise ReleaseError(
            "release_id must match {name}@{version} using safe characters"
        )


def _normalize_artifacts(
    values: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    normalized = [canonical_data(dict(item)) for item in values]
    normalized.sort(
        key=lambda item: (
            str(item.get("target", "")),
            str(item.get("content_hash", "")),
        )
    )
    return tuple(_freeze_mapping(item) for item in normalized)


def _release_payload(
    *,
    api_version: str,
    release_id: str,
    parent_release: str | None,
    resources: tuple[ResourceRevisionRef, ...],
    scope: Mapping[str, Any],
    compiled_artifacts: tuple[Mapping[str, Any], ...],
    validation: Mapping[str, Any],
    governance: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "api_version": api_version,
        "release_id": release_id,
        "parent_release": parent_release,
        "scope": canonical_data(scope),
        "resources": [resource.to_dict() for resource in resources],
        "compiled_artifacts": [canonical_data(item) for item in compiled_artifacts],
        "validation": canonical_data(validation),
        "governance": canonical_data(governance),
    }
