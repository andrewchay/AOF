"""Governed, cursor-bound enterprise knowledge ingestion contracts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .canonical import canonical_data, canonical_json, content_digest
from .identity import SignedPrincipalVerifier


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
    change_set_digest: str
    actor: str
    status: str
    error: Mapping[str, Any] | None
    run_digest: str

    @classmethod
    def succeeded(
        cls,
        source: KnowledgeSource,
        batch: SourceBatch,
        change_set: "KnowledgeChangeSet",
        *,
        actor: str,
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
            "change_set_digest": change_set.change_set_digest,
            "actor": _text(actor, "actor"),
            "status": "succeeded",
            "error": None,
        }
        return cls(**payload, run_digest=content_digest(payload))

    @classmethod
    def failed(
        cls,
        source: KnowledgeSource,
        *,
        actor: str,
        attempt_id: str,
        error: Exception,
    ) -> "IngestionRun":
        failure = {"type": type(error).__name__, "message": str(error)}
        identity = content_digest(
            {
                "tenant_id": source.tenant_id,
                "source_id": source.source_id,
                "source_revision": source.revision_id,
                "cursor": source.cursor,
                "attempt_id": _text(attempt_id, "attempt_id"),
            }
        )
        empty_digest = content_digest([])
        payload = {
            "run_id": f"ingest-{identity.split(':', 1)[-1][:24]}",
            "tenant_id": source.tenant_id,
            "source_id": source.source_id,
            "source_revision": source.revision_id,
            "cursor_from": source.cursor,
            "cursor_to": source.cursor or "unstarted",
            "record_count": 0,
            "records_digest": empty_digest,
            "source_snapshot_digest": empty_digest,
            "batch_digest": empty_digest,
            "change_set_digest": empty_digest,
            "actor": _text(actor, "actor"),
            "status": "failed",
            "error": failure,
        }
        return cls(**payload, run_digest=content_digest(payload))

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(self.__dict__)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IngestionRun":
        return cls(**dict(value))


@dataclass(frozen=True)
class KnowledgeChangeSet:
    source_id: str
    source_revision: str
    cursor_from: str | None
    cursor_to: str
    added: tuple[Mapping[str, Any], ...]
    updated: tuple[Mapping[str, Any], ...]
    deleted: tuple[Mapping[str, Any], ...]
    schema_drift: Mapping[str, Any]
    summary: Mapping[str, int]
    change_set_digest: str

    @classmethod
    def build(
        cls,
        source: KnowledgeSource,
        batch: SourceBatch,
        previous: Mapping[str, Mapping[str, Any]],
        previous_fields: Sequence[str],
    ) -> "KnowledgeChangeSet":
        identity_field = str(source.config.get("identity_field", "id"))
        current: dict[str, Mapping[str, Any]] = {}
        for record in batch.records:
            entity_id = record.get(identity_field)
            if not isinstance(entity_id, str) or not entity_id.strip():
                raise ContinuousIngestionError(
                    f"record identity field is missing: {identity_field}"
                )
            if entity_id in current:
                raise ContinuousIngestionError(
                    f"duplicate entity identity: {entity_id}"
                )
            current[entity_id] = record
        added = tuple(
            {"entity_id": key, "record": current[key]}
            for key in sorted(current.keys() - previous.keys())
        )
        updated = tuple(
            {
                "entity_id": key,
                "before_digest": content_digest(previous[key]),
                "record": current[key],
            }
            for key in sorted(current.keys() & previous.keys())
            if content_digest(current[key]) != content_digest(previous[key])
        )
        deleted = ()
        if source.config.get("snapshot_mode") == "full":
            deleted = tuple(
                {"entity_id": key, "before_digest": content_digest(previous[key])}
                for key in sorted(previous.keys() - current.keys())
            )
        fields = sorted({str(key) for record in current.values() for key in record})
        schema_drift = {
            "added_fields": sorted(set(fields) - set(previous_fields)),
            "removed_fields": sorted(set(previous_fields) - set(fields)),
        }
        if not previous:
            schema_drift = {"added_fields": [], "removed_fields": []}
        summary = {
            "added": len(added),
            "updated": len(updated),
            "deleted": len(deleted),
        }
        payload = {
            "source_id": source.source_id,
            "source_revision": source.revision_id,
            "cursor_from": batch.cursor_from,
            "cursor_to": batch.cursor_to,
            "added": added,
            "updated": updated,
            "deleted": deleted,
            "schema_drift": schema_drift,
            "summary": summary,
        }
        return cls(**payload, change_set_digest=content_digest(payload))

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(self.__dict__)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "KnowledgeChangeSet":
        payload = dict(value)
        for field in ("added", "updated", "deleted"):
            payload[field] = tuple(payload[field])
        return cls(**payload)


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
            db.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_change_sets (run_id TEXT PRIMARY KEY, tenant_id TEXT, change_set_digest TEXT, payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS source_entities (tenant_id TEXT, source_id TEXT, entity_id TEXT, payload TEXT, PRIMARY KEY (tenant_id, source_id, entity_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS source_schemas (tenant_id TEXT, source_id TEXT, fields_json TEXT, PRIMARY KEY (tenant_id, source_id))"
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

    def prepare_change_set(
        self, source: KnowledgeSource, batch: SourceBatch
    ) -> KnowledgeChangeSet:
        with self._connect() as db:
            rows = db.execute(
                "SELECT entity_id, payload FROM source_entities WHERE tenant_id=? AND source_id=?",
                (source.tenant_id, source.source_id),
            ).fetchall()
            schema = db.execute(
                "SELECT fields_json FROM source_schemas WHERE tenant_id=? AND source_id=?",
                (source.tenant_id, source.source_id),
            ).fetchone()
        previous = {str(row[0]): json.loads(row[1]) for row in rows}
        fields = json.loads(schema[0]) if schema else []
        return KnowledgeChangeSet.build(source, batch, previous, fields)

    def commit(
        self,
        source: KnowledgeSource,
        run: IngestionRun,
        change_set: KnowledgeChangeSet,
        batch: SourceBatch,
    ) -> IngestionRun:
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
                "INSERT INTO knowledge_change_sets VALUES (?, ?, ?, ?)",
                (
                    run.run_id,
                    run.tenant_id,
                    change_set.change_set_digest,
                    canonical_json(change_set.to_dict()),
                ),
            )
            identity_field = str(source.config.get("identity_field", "id"))
            if source.config.get("snapshot_mode") == "full":
                db.execute(
                    "DELETE FROM source_entities WHERE tenant_id=? AND source_id=?",
                    (source.tenant_id, source.source_id),
                )
            for record in batch.records:
                entity_id = str(record[identity_field])
                db.execute(
                    "INSERT OR REPLACE INTO source_entities VALUES (?, ?, ?, ?)",
                    (
                        source.tenant_id,
                        source.source_id,
                        entity_id,
                        canonical_json(record),
                    ),
                )
            fields = sorted({str(key) for record in batch.records for key in record})
            db.execute(
                "INSERT OR REPLACE INTO source_schemas VALUES (?, ?, ?)",
                (source.tenant_id, source.source_id, canonical_json(fields)),
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

    def commit_failure(self, run: IngestionRun) -> IngestionRun:
        if run.status != "failed":
            raise ContinuousIngestionError("commit_failure requires a failed run")
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM ingestion_runs WHERE run_id=?", (run.run_id,)
            ).fetchone()
            if row:
                existing = IngestionRun.from_dict(json.loads(row[0]))
                if existing.run_digest != run.run_digest:
                    raise ContinuousIngestionError(
                        "failed ingestion run digest conflict"
                    )
                return existing
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
        return run

    def list_sources(self, *, tenant_id: str) -> list[KnowledgeSource]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM knowledge_sources WHERE tenant_id=? ORDER BY source_id",
                (tenant_id,),
            ).fetchall()
        return [self._source(json.loads(row[0])) for row in rows]

    def list_runs(
        self, *, tenant_id: str, source_id: str | None = None
    ) -> list[IngestionRun]:
        query = "SELECT payload FROM ingestion_runs WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if source_id is not None:
            query += " AND source_id=?"
            params.append(source_id)
        query += " ORDER BY rowid DESC"
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [IngestionRun.from_dict(json.loads(row[0])) for row in rows]

    def get_run(self, run_id: str, *, tenant_id: str) -> IngestionRun:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM ingestion_runs WHERE run_id=? AND tenant_id=?",
                (run_id, tenant_id),
            ).fetchone()
        if not row:
            raise ContinuousIngestionError(f"ingestion run not found: {run_id}")
        return IngestionRun.from_dict(json.loads(row[0]))

    def get_change_set(self, run_id: str, *, tenant_id: str) -> KnowledgeChangeSet:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM knowledge_change_sets WHERE run_id=? AND tenant_id=?",
                (run_id, tenant_id),
            ).fetchone()
        if not row:
            raise ContinuousIngestionError(f"knowledge change set not found: {run_id}")
        return KnowledgeChangeSet.from_dict(json.loads(row[0]))

    def verify_all(self) -> dict[str, Any]:
        errors = []
        with self._connect() as db:
            sources = db.execute("SELECT payload FROM knowledge_sources").fetchall()
            runs = db.execute(
                "SELECT run_digest, payload FROM ingestion_runs"
            ).fetchall()
            changes = db.execute(
                "SELECT change_set_digest, payload FROM knowledge_change_sets"
            ).fetchall()
        for stored_digest, raw in runs:
            run = IngestionRun.from_dict(json.loads(raw))
            payload = {k: v for k, v in run.to_dict().items() if k != "run_digest"}
            if (
                run.run_digest != stored_digest
                or content_digest(payload) != run.run_digest
            ):
                errors.append(run.run_id)
        for stored_digest, raw in changes:
            change_set = KnowledgeChangeSet.from_dict(json.loads(raw))
            payload = {
                key: value
                for key, value in change_set.to_dict().items()
                if key != "change_set_digest"
            }
            if (
                stored_digest != change_set.change_set_digest
                or content_digest(payload) != stored_digest
            ):
                errors.append(change_set.source_id)
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
        self,
        source_id: str,
        *,
        tenant_id: str,
        actor: str,
        attempt_id: str = "default",
    ) -> IngestionRun:
        source = self.repository.get_source(source_id, tenant_id=tenant_id)
        try:
            batch = self.connectors.get(source.source_type).fetch(source, source.cursor)
        except Exception as exc:
            return self.repository.commit_failure(
                IngestionRun.failed(
                    source, actor=actor, attempt_id=attempt_id, error=exc
                )
            )
        change_set = self.repository.prepare_change_set(source, batch)
        run = IngestionRun.succeeded(source, batch, change_set, actor=actor)
        return self.repository.commit(source, run, change_set, batch)


class ContinuousIngestionControlPlane:
    """Signed transport-neutral boundary for source registration and ingestion."""

    def __init__(
        self,
        repository: SqliteContinuousIngestionRepository,
        connectors: SourceConnectorRegistry,
        verifier: SignedPrincipalVerifier,
    ) -> None:
        self.repository = repository
        self.connectors = connectors
        self.verifier = verifier

    def register(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        actor = principal.actor_for("source_register")
        config = payload.get("config")
        if not isinstance(config, Mapping):
            raise ContinuousIngestionError("config must be a semantic object")
        source = KnowledgeSource.create(
            source_id=_text(payload.get("source_id"), "source_id"),
            tenant_id=principal.tenant_id,
            source_type=_text(payload.get("source_type"), "source_type"),
            owner=actor,
            config=config,
        )
        return self.repository.register_source(source).to_dict()

    def ingest(
        self, source_id: str, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        actor = principal.actor_for("ingest")
        return (
            ContinuousIngestionService(self.repository, self.connectors)
            .ingest_once(
                source_id,
                tenant_id=principal.tenant_id,
                actor=actor,
                attempt_id=_text(payload.get("attempt_id"), "attempt_id"),
            )
            .to_dict()
        )

    def list_sources(self, *, headers: Mapping[str, str]) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        principal.actor_for("read")
        sources = self.repository.list_sources(tenant_id=principal.tenant_id)
        return {
            "sources": [source.to_dict() for source in sources],
            "count": len(sources),
        }

    def list_runs(
        self, *, source_id: str | None, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        principal.actor_for("read")
        runs = self.repository.list_runs(
            tenant_id=principal.tenant_id, source_id=source_id
        )
        return {"runs": [run.to_dict() for run in runs], "count": len(runs)}

    def get_run(self, run_id: str, *, headers: Mapping[str, str]) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        principal.actor_for("read")
        run = self.repository.get_run(run_id, tenant_id=principal.tenant_id)
        payload = run.to_dict()
        if run.status == "succeeded":
            payload["change_set"] = self.repository.get_change_set(
                run_id, tenant_id=principal.tenant_id
            ).to_dict()
        return payload
