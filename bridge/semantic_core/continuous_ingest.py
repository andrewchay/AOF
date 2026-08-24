"""Governed, cursor-bound enterprise knowledge ingestion contracts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .canonical import canonical_data, canonical_json, content_digest


class ContinuousIngestionError(ValueError):
    """Raised when source or ingestion evidence cannot be trusted."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContinuousIngestionError(f"{field} must be a non-empty string")
    return value.strip()


def _reject_secrets(value: Any, path: str = "config") -> None:
    secret_tokens = ("secret", "password", "token", "credential", "api_key")
    if isinstance(value, Mapping):
        for key, inner in value.items():
            if any(token in str(key).lower() for token in secret_tokens):
                raise ContinuousIngestionError(
                    f"{path} cannot embed secret field: {key}"
                )
            _reject_secrets(inner, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, inner in enumerate(value):
            _reject_secrets(inner, f"{path}[{index}]")


@dataclass(frozen=True)
class KnowledgeSource:
    source_id: str
    tenant_id: str
    source_type: str
    owner: str
    config: Mapping[str, Any]
    revision_id: str
    cursor: str | None = None

    @classmethod
    def create(
        cls,
        *,
        source_id: str,
        tenant_id: str,
        source_type: str,
        owner: str,
        config: Mapping[str, Any],
    ) -> "KnowledgeSource":
        normalized = canonical_data(config)
        _reject_secrets(normalized)
        payload = {
            "source_id": _text(source_id, "source_id"),
            "tenant_id": _text(tenant_id, "tenant_id"),
            "source_type": _text(source_type, "source_type"),
            "owner": _text(owner, "owner"),
            "config": normalized,
        }
        return cls(**payload, revision_id=content_digest(payload))

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(
            {
                "source_id": self.source_id,
                "tenant_id": self.tenant_id,
                "source_type": self.source_type,
                "owner": self.owner,
                "config": self.config,
                "revision_id": self.revision_id,
                "cursor": self.cursor,
            }
        )


@dataclass(frozen=True)
class SourceBatch:
    cursor_from: str | None
    cursor_to: str
    records: tuple[Mapping[str, Any], ...]
    source_snapshot: Mapping[str, Any]
    batch_digest: str

    @classmethod
    def create(
        cls,
        *,
        cursor_from: str | None,
        cursor_to: str,
        records: Sequence[Mapping[str, Any]],
        source_snapshot: Mapping[str, Any],
    ) -> "SourceBatch":
        normalized_records = tuple(canonical_data(item) for item in records)
        snapshot = canonical_data(source_snapshot)
        payload = {
            "cursor_from": cursor_from,
            "cursor_to": _text(cursor_to, "cursor_to"),
            "records": normalized_records,
            "source_snapshot": snapshot,
        }
        return cls(**payload, batch_digest=content_digest(payload))


class SourceConnector(Protocol):
    def fetch(self, source: KnowledgeSource, cursor: str | None) -> SourceBatch: ...


class SourceConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, SourceConnector] = {}

    def register(self, source_type: str, connector: SourceConnector) -> None:
        key = _text(source_type, "source_type")
        if key in self._connectors:
            raise ContinuousIngestionError(
                f"source connector already registered: {key}"
            )
        self._connectors[key] = connector

    def get(self, source_type: str) -> SourceConnector:
        try:
            return self._connectors[source_type]
        except KeyError as exc:
            raise ContinuousIngestionError(
                f"source connector not registered: {source_type}"
            ) from exc


@dataclass(frozen=True)
class IngestionRun:
    run_id: str
    tenant_id: str
    source_id: str
    source_revision: str
    cursor_from: str | None
    cursor_to: str
    record_count: int
    records_digest: str
    source_snapshot_digest: str
    batch_digest: str
    actor: str
    status: str
    run_digest: str

    @classmethod
    def succeeded(
        cls, source: KnowledgeSource, batch: SourceBatch, *, actor: str
    ) -> "IngestionRun":
        if batch.cursor_from != source.cursor:
            raise ContinuousIngestionError(
                "connector batch cursor does not match registered source cursor"
            )
        identity = content_digest(
            {
                "tenant_id": source.tenant_id,
                "source_id": source.source_id,
                "source_revision": source.revision_id,
                "cursor_from": source.cursor,
                "cursor_to": batch.cursor_to,
                "batch_digest": batch.batch_digest,
            }
        )
        payload = {
            "run_id": f"ingest-{identity.split(':', 1)[-1][:24]}",
            "tenant_id": source.tenant_id,
            "source_id": source.source_id,
            "source_revision": source.revision_id,
            "cursor_from": source.cursor,
            "cursor_to": batch.cursor_to,
            "record_count": len(batch.records),
            "records_digest": content_digest(batch.records),
            "source_snapshot_digest": content_digest(batch.source_snapshot),
            "batch_digest": batch.batch_digest,
            "actor": _text(actor, "actor"),
            "status": "succeeded",
        }
        return cls(**payload, run_digest=content_digest(payload))

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(self.__dict__)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IngestionRun":
        return cls(**dict(value))


class SqliteContinuousIngestionRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_sources (tenant_id TEXT, source_id TEXT, revision_id TEXT, payload TEXT, PRIMARY KEY (tenant_id, source_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS ingestion_runs (run_id TEXT PRIMARY KEY, tenant_id TEXT, source_id TEXT, run_digest TEXT, payload TEXT)"
            )
            db.execute("PRAGMA user_version=1")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def register_source(self, source: KnowledgeSource) -> KnowledgeSource:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM knowledge_sources WHERE tenant_id=? AND source_id=?",
                (source.tenant_id, source.source_id),
            ).fetchone()
            if row:
                current = self._source(json.loads(row[0]))
                if current.revision_id != source.revision_id:
                    raise ContinuousIngestionError(
                        f"knowledge source already exists with different revision: {source.source_id}"
                    )
                return current
            db.execute(
                "INSERT INTO knowledge_sources VALUES (?, ?, ?, ?)",
                (
                    source.tenant_id,
                    source.source_id,
                    source.revision_id,
                    canonical_json(source.to_dict()),
                ),
            )
        return source

    def get_source(self, source_id: str, *, tenant_id: str) -> KnowledgeSource:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM knowledge_sources WHERE tenant_id=? AND source_id=?",
                (tenant_id, source_id),
            ).fetchone()
        if not row:
            raise ContinuousIngestionError(f"knowledge source not found: {source_id}")
        return self._source(json.loads(row[0]))

    def commit(self, source: KnowledgeSource, run: IngestionRun) -> IngestionRun:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM ingestion_runs WHERE run_id=?", (run.run_id,)
            ).fetchone()
            if row:
                existing = IngestionRun.from_dict(json.loads(row[0]))
                if existing.run_digest != run.run_digest:
                    raise ContinuousIngestionError("ingestion run digest conflict")
                db.commit()
                return existing
            current = self.get_source(source.source_id, tenant_id=source.tenant_id)
            if current.cursor != run.cursor_from:
                raise ContinuousIngestionError("source cursor advanced concurrently")
            advanced = replace(source, cursor=run.cursor_to)
            db.execute(
                "INSERT INTO ingestion_runs VALUES (?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.tenant_id,
                    run.source_id,
                    run.run_digest,
                    canonical_json(run.to_dict()),
                ),
            )
            db.execute(
                "UPDATE knowledge_sources SET payload=? WHERE tenant_id=? AND source_id=?",
                (
                    canonical_json(advanced.to_dict()),
                    source.tenant_id,
                    source.source_id,
                ),
            )
            db.commit()
            return run
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_run(self, run_id: str, *, tenant_id: str) -> IngestionRun:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM ingestion_runs WHERE run_id=? AND tenant_id=?",
                (run_id, tenant_id),
            ).fetchone()
        if not row:
            raise ContinuousIngestionError(f"ingestion run not found: {run_id}")
        return IngestionRun.from_dict(json.loads(row[0]))

    def verify_all(self) -> dict[str, Any]:
        errors = []
        with self._connect() as db:
            sources = db.execute("SELECT payload FROM knowledge_sources").fetchall()
            runs = db.execute(
                "SELECT run_digest, payload FROM ingestion_runs"
            ).fetchall()
        for stored_digest, raw in runs:
            run = IngestionRun.from_dict(json.loads(raw))
            payload = {k: v for k, v in run.to_dict().items() if k != "run_digest"}
            if (
                run.run_digest != stored_digest
                or content_digest(payload) != run.run_digest
            ):
                errors.append(run.run_id)
        return {
            "valid": not errors,
            "source_count": len(sources),
            "run_count": len(runs),
            "errors": errors,
        }

    @staticmethod
    def _source(value: Mapping[str, Any]) -> KnowledgeSource:
        return KnowledgeSource(**dict(value))


class ContinuousIngestionService:
    def __init__(
        self,
        repository: SqliteContinuousIngestionRepository,
        connectors: SourceConnectorRegistry,
    ) -> None:
        self.repository, self.connectors = repository, connectors

    def ingest_once(
        self, source_id: str, *, tenant_id: str, actor: str
    ) -> IngestionRun:
        source = self.repository.get_source(source_id, tenant_id=tenant_id)
        batch = self.connectors.get(source.source_type).fetch(source, source.cursor)
        run = IngestionRun.succeeded(source, batch, actor=actor)
        return self.repository.commit(source, run)
