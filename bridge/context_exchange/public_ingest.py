# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Replayable, rights-aware ingestion of public source records."""

from __future__ import annotations
from bridge.persistence.sqlite_support import managed_sqlite_connection

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core.canonical import canonical_json, content_digest

from .contracts import ContextExchangeError


class PublicIngestError(ContextExchangeError):
    """Raised when a public source batch cannot be retained or replayed safely."""


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PublicIngestError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class PublicSourceRights:
    license_id: str
    redistribution: str
    source_terms_url: str | None = None

    @classmethod
    def create(cls, *, license_id: str, redistribution: str, source_terms_url: str | None = None) -> "PublicSourceRights":
        if redistribution not in {"allowed", "restricted", "unknown"}:
            raise PublicIngestError("redistribution must be allowed, restricted, or unknown")
        return cls(_required(license_id, "license_id"), redistribution, source_terms_url.strip() if isinstance(source_terms_url, str) else None)

    def to_dict(self) -> dict[str, Any]:
        return {"license_id": self.license_id, "redistribution": self.redistribution, "source_terms_url": self.source_terms_url}


@dataclass(frozen=True)
class PublicSourceRecord:
    source_id: str
    source_url: str
    title: str
    content: str
    observed_at: str
    rights: PublicSourceRights
    etag: str | None = None
    last_modified: str | None = None

    @property
    def content_hash(self) -> str:
        return content_digest({"source_url": self.source_url, "content": self.content})

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id, "source_url": self.source_url, "title": self.title,
            "content": self.content, "observed_at": self.observed_at, "rights": self.rights.to_dict(),
            "etag": self.etag, "last_modified": self.last_modified, "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class PublicSourceBatch:
    source_key: str
    records: tuple[PublicSourceRecord, ...]
    fetched_at: str
    next_cursor: str | None
    complete: bool


class SqlitePublicSourceRepository:
    """Immutable raw records plus a separately maintained source watermark."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS public_source_records (
                    source_key TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (source_key, source_id, content_hash)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS public_source_watermarks (
                    source_key TEXT PRIMARY KEY,
                    cursor TEXT,
                    watermark TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def ingest(self, batch: PublicSourceBatch) -> tuple[PublicSourceRecord, ...]:
        source_key = _required(batch.source_key, "source_key")
        fetched_at = _required(batch.fetched_at, "fetched_at")
        inserted = []
        with self._connection() as connection:
            for record in batch.records:
                payload = canonical_json(record.to_dict())
                current = connection.execute(
                    "SELECT 1 FROM public_source_records WHERE source_key = ? AND source_id = ? AND content_hash = ?",
                    (source_key, record.source_id, record.content_hash),
                ).fetchone()
                if current is None:
                    connection.execute(
                        "INSERT INTO public_source_records (source_key, source_id, content_hash, payload, fetched_at) VALUES (?, ?, ?, ?, ?)",
                        (source_key, record.source_id, record.content_hash, payload, fetched_at),
                    )
                    inserted.append(record)
            status = "complete" if batch.complete else "incomplete"
            watermark = fetched_at if batch.complete else self._existing_watermark(connection, source_key)
            connection.execute(
                "INSERT INTO public_source_watermarks (source_key, cursor, watermark, status, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(source_key) DO UPDATE SET cursor = excluded.cursor, watermark = excluded.watermark, status = excluded.status, updated_at = excluded.updated_at",
                (source_key, batch.next_cursor, watermark, status, fetched_at),
            )
        return tuple(inserted)

    def replay(self, *, source_key: str, source_id: str) -> tuple[PublicSourceRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM public_source_records WHERE source_key = ? AND source_id = ? ORDER BY fetched_at, content_hash",
                (source_key, source_id),
            ).fetchall()
        return tuple(self._record_from_payload(row[0]) for row in rows)

    def watermark(self, source_key: str) -> Mapping[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT cursor, watermark, status, updated_at FROM public_source_watermarks WHERE source_key = ?", (source_key,)
            ).fetchone()
        if row is None:
            return None
        return {"source_key": source_key, "cursor": row[0], "watermark": row[1], "status": row[2], "updated_at": row[3]}

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
    def _connection(self):
        """Transaction + close context manager (W09.01: no leaked connections)."""
        return managed_sqlite_connection(self._connect)


    @staticmethod
    def _existing_watermark(connection: sqlite3.Connection, source_key: str) -> str:
        row = connection.execute("SELECT watermark FROM public_source_watermarks WHERE source_key = ?", (source_key,)).fetchone()
        return row[0] if row is not None else "0"

    @staticmethod
    def _record_from_payload(payload: str) -> PublicSourceRecord:
        value = json.loads(payload)
        rights = value["rights"]
        record = PublicSourceRecord(
            source_id=value["source_id"], source_url=value["source_url"], title=value["title"],
            content=value["content"], observed_at=value["observed_at"],
            rights=PublicSourceRights.create(**rights), etag=value["etag"], last_modified=value["last_modified"],
        )
        if record.content_hash != value["content_hash"]:
            raise PublicIngestError("stored public source record integrity check failed")
        return record


class PublicSourceIngestor:
    """Audited ingestion; licensing is recorded now and enforced at publication later."""

    def __init__(self, *, repository: SqlitePublicSourceRepository, decision_store: DecisionProvenanceStore) -> None:
        self.repository = repository
        self.decision_store = decision_store

    def ingest(self, batch: PublicSourceBatch, *, actor: str, rationale: str) -> tuple[PublicSourceRecord, ...]:
        inserted = self.repository.ingest(batch)
        if inserted:
            self.decision_store.record(
                agent_id=actor, decision_type="public_source_ingested",
                conclusion=f"ingested {len(inserted)} immutable public source record(s)", rationale=rationale,
                evidence=[{"id": record.source_id, "type": "public_source", "uri": record.source_url, "content_hash": record.content_hash, "metadata": {"rights": record.rights.to_dict(), "observed_at": record.observed_at}} for record in inserted],
                policies=["policy:public-source-ingest-v1"], tags=["public-source", batch.source_key],
                metadata={"source_key": batch.source_key, "complete": batch.complete, "next_cursor": batch.next_cursor},
            )
        return inserted


def require_redistributable(record: PublicSourceRecord) -> None:
    if record.rights.redistribution != "allowed":
        raise PublicIngestError("public release requires a source with allowed redistribution")
