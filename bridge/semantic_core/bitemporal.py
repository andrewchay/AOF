# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Tenant-isolated bitemporal Object/Fact state with evidence snapshots."""

from __future__ import annotations
from bridge.persistence.sqlite_support import managed_sqlite_connection

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonical import canonical_data, canonical_json, content_digest


class BitemporalError(ValueError):
    """Raised when fact history or point-in-time semantics are invalid."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BitemporalError(f"{field} must be a non-empty string")
    return value.strip()


def _instant(value: Any, field: str) -> str:
    raw = _text(value, field).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise BitemporalError(f"{field} must be an ISO-8601 instant") from exc
    if parsed.tzinfo is None:
        raise BitemporalError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


class BitemporalObjectStore:
    """Version Fact knowledge independently from the time each Fact is valid."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fact_versions (
                    tenant_id TEXT NOT NULL,
                    fact_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    object_type_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    field TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    tx_from TEXT NOT NULL,
                    tx_to TEXT,
                    source_json TEXT NOT NULL,
                    action_run_id TEXT,
                    fact_digest TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, fact_id, version)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS fact_versions_snapshot_idx ON fact_versions "
                "(tenant_id, object_type_id, object_id, valid_from, tx_from)"
            )
            connection.execute("PRAGMA user_version=1")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)
    def _connection(self):
        """Transaction + close context manager (W09.01: no leaked connections)."""
        return managed_sqlite_connection(self._connect)


    def assert_fact(
        self,
        *,
        tenant_id: str,
        fact_id: str,
        object_type_id: str,
        object_id: str,
        field: str,
        value: Any,
        valid_from: str,
        valid_to: str | None,
        recorded_at: str,
        source: Mapping[str, Any],
        action_run_id: str | None = None,
    ) -> dict[str, Any]:
        tenant = _text(tenant_id, "tenant_id")
        identity = _text(fact_id, "fact_id")
        object_type = _text(object_type_id, "object_type_id")
        object_key = _text(object_id, "object_id")
        field_name = _text(field, "field")
        valid_start = _instant(valid_from, "valid_from")
        valid_end = _instant(valid_to, "valid_to") if valid_to is not None else None
        if valid_end is not None and valid_end <= valid_start:
            raise BitemporalError("valid_to must be after valid_from")
        transaction_start = _instant(recorded_at, "recorded_at")
        if not isinstance(source, Mapping) or not source:
            raise BitemporalError("source must be a non-empty semantic object")
        normalized_source = canonical_data(source)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT version, object_type_id, object_id, field, tx_from "
                "FROM fact_versions WHERE tenant_id = ? AND fact_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (tenant, identity),
            ).fetchone()
            version = 1
            if row is not None:
                version = int(row[0]) + 1
                if (row[1], row[2], row[3]) != (
                    object_type,
                    object_key,
                    field_name,
                ):
                    raise BitemporalError(
                        "fact_id cannot change object identity or field"
                    )
                if transaction_start <= str(row[4]):
                    raise BitemporalError(
                        "recorded_at must advance transaction time"
                    )
                connection.execute(
                    "UPDATE fact_versions SET tx_to = ? "
                    "WHERE tenant_id = ? AND fact_id = ? AND version = ?",
                    (transaction_start, tenant, identity, version - 1),
                )
            payload = {
                "api_version": "aof.fact-version/v1",
                "tenant_id": tenant,
                "fact_id": identity,
                "version": version,
                "object_type_id": object_type,
                "object_id": object_key,
                "field": field_name,
                "value": canonical_data(value),
                "valid_from": valid_start,
                "valid_to": valid_end,
                "tx_from": transaction_start,
                "tx_to": None,
                "source": normalized_source,
                "action_run_id": (
                    _text(action_run_id, "action_run_id")
                    if action_run_id is not None
                    else None
                ),
            }
            fact = {**payload, "fact_digest": content_digest(payload)}
            connection.execute(
                "INSERT INTO fact_versions "
                "(tenant_id, fact_id, version, object_type_id, object_id, field, "
                "value_json, valid_from, valid_to, tx_from, tx_to, source_json, "
                "action_run_id, fact_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant,
                    identity,
                    version,
                    object_type,
                    object_key,
                    field_name,
                    canonical_json(fact["value"]),
                    valid_start,
                    valid_end,
                    transaction_start,
                    None,
                    canonical_json(normalized_source),
                    fact["action_run_id"],
                    fact["fact_digest"],
                ),
            )
            connection.commit()
            return fact
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def snapshot(
        self,
        *,
        tenant_id: str,
        object_type_id: str,
        object_id: str,
        valid_at: str,
        known_at: str,
    ) -> dict[str, Any]:
        tenant = _text(tenant_id, "tenant_id")
        object_type = _text(object_type_id, "object_type_id")
        object_key = _text(object_id, "object_id")
        valid = _instant(valid_at, "valid_at")
        known = _instant(known_at, "known_at")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT fact_id, version, field, value_json, valid_from, valid_to, "
                "tx_from, tx_to, source_json, action_run_id, fact_digest "
                "FROM fact_versions WHERE tenant_id = ? AND object_type_id = ? "
                "AND object_id = ? AND valid_from <= ? "
                "AND (valid_to IS NULL OR valid_to > ?) AND tx_from <= ? "
                "AND (tx_to IS NULL OR tx_to > ?) ORDER BY field, fact_id, version",
                (tenant, object_type, object_key, valid, valid, known, known),
            ).fetchall()
        by_field: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            fact = self._row(row, tenant, object_type, object_key)
            by_field.setdefault(fact["field"], []).append(fact)
        ambiguous = sorted(field for field, facts in by_field.items() if len(facts) > 1)
        if ambiguous:
            raise BitemporalError(
                f"multiple facts are valid for fields: {', '.join(ambiguous)}"
            )
        values = {field: facts[0]["value"] for field, facts in sorted(by_field.items())}
        evidence = [
            {
                key: fact[key]
                for key in (
                    "fact_id",
                    "version",
                    "field",
                    "valid_from",
                    "valid_to",
                    "tx_from",
                    "tx_to",
                    "source",
                    "action_run_id",
                    "fact_digest",
                )
            }
            for __, facts in sorted(by_field.items())
            for fact in facts
        ]
        payload = {
            "api_version": "aof.object-snapshot/v1",
            "tenant_id": tenant,
            "object_type_id": object_type,
            "object_id": object_key,
            "valid_at": valid,
            "known_at": known,
            "values": values,
            "evidence": evidence,
        }
        return {**payload, "snapshot_digest": content_digest(payload)}

    def schema_version(self) -> int:
        with self._connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def verify_all(self) -> dict[str, Any]:
        errors = []
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT tenant_id, object_type_id, object_id, fact_id, version, field, "
                "value_json, valid_from, valid_to, tx_from, tx_to, source_json, "
                "action_run_id, fact_digest FROM fact_versions"
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"sqlite integrity check failed: {integrity}")
        for row in rows:
            try:
                projected = (
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                    row[13],
                )
                self._row(projected, str(row[0]), str(row[1]), str(row[2]))
            except Exception as exc:
                errors.append(f"fact/{row[0]}/{row[3]}@{row[4]}: {exc}")
        return {
            "valid": not errors,
            "fact_version_count": len(rows),
            "errors": errors,
        }

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

    @staticmethod
    def _row(
        row: tuple[Any, ...], tenant: str, object_type: str, object_id: str
    ) -> dict[str, Any]:
        payload = {
            "api_version": "aof.fact-version/v1",
            "tenant_id": tenant,
            "fact_id": row[0],
            "version": int(row[1]),
            "object_type_id": object_type,
            "object_id": object_id,
            "field": row[2],
            "value": json.loads(row[3]),
            "valid_from": row[4],
            "valid_to": row[5],
            "tx_from": row[6],
            "tx_to": row[7],
            "source": json.loads(row[8]),
            "action_run_id": row[9],
        }
        if row[10] != content_digest({**payload, "tx_to": None}):
            # tx_to is closed later and is intentionally outside immutable fact content.
            raise BitemporalError(f"fact digest mismatch: {row[0]}@{row[1]}")
        return {**payload, "fact_digest": row[10]}
