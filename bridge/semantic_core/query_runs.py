# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Immutable query runs, durable storage, and detached evidence attestations."""

from __future__ import annotations
from bridge.persistence.sqlite_support import managed_sqlite_connection

import hashlib
import hmac
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .canonical import canonical_data, canonical_json, content_digest


class QueryRunError(ValueError):
    """Raised when a query run is malformed, mutable, or cannot be verified."""


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FIELDS = {
    "api_version",
    "query_run_id",
    "tenant_id",
    "actor",
    "status",
    "request",
    "request_digest",
    "compilation_run_id",
    "compilation_run_digest",
    "release_id",
    "release_digest",
    "plan_digest",
    "policy_report_digest",
    "governed_result",
    "decisions",
    "evidence_package",
    "error",
    "replay_of",
    "recorded_at",
    "run_digest",
    "attestation",
}


def _required(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QueryRunError(f"{field} must be a non-empty string")
    return value.strip()


def _safe_id(value: str, field: str) -> str:
    normalized = _required(value, field)
    if not _SAFE_ID.fullmatch(normalized):
        raise QueryRunError(f"{field} must use safe characters")
    return normalized


def _status(value: str) -> str:
    normalized = _required(value, "status")
    if normalized not in {"succeeded", "failed"}:
        raise QueryRunError(f"unsupported query run status: {normalized}")
    return normalized


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class QueryRun:
    query_run_id: str
    tenant_id: str
    actor: str
    status: str
    request: Mapping[str, Any]
    request_digest: str
    compilation_run_id: str
    compilation_run_digest: str
    release_id: str
    release_digest: str
    plan_digest: str
    policy_report_digest: str
    governed_result: Mapping[str, Any]
    decisions: Mapping[str, Any]
    evidence_package: Mapping[str, Any]
    error: Mapping[str, Any] | None
    replay_of: str | None
    recorded_at: str
    run_digest: str
    attestation: Mapping[str, Any] | None = None

    @classmethod
    def build(
        cls,
        *,
        query_run_id: str,
        tenant_id: str,
        actor: str,
        request: Mapping[str, Any],
        compilation_run_id: str,
        compilation_run_digest: str,
        release_id: str,
        release_digest: str,
        plan_digest: str,
        policy_report_digest: str,
        governed_result: Mapping[str, Any],
        decisions: Mapping[str, Any],
        evidence_package: Mapping[str, Any],
        replay_of: str | None,
        recorded_at: str,
        status: str = "succeeded",
        error: Mapping[str, Any] | None = None,
    ) -> "QueryRun":
        query_run_id = _safe_id(query_run_id, "query_run_id")
        if replay_of is not None:
            replay_of = _safe_id(replay_of, "replay_of")
            if replay_of == query_run_id:
                raise QueryRunError("a query run cannot replay itself")
        normalized_request = canonical_data(dict(request))
        payload = {
            "api_version": "aof.query-run/v1",
            "query_run_id": query_run_id,
            "tenant_id": _required(tenant_id, "tenant_id"),
            "actor": _required(actor, "actor"),
            "status": _status(status),
            "request": normalized_request,
            "request_digest": content_digest(normalized_request),
            "compilation_run_id": _required(compilation_run_id, "compilation_run_id"),
            "compilation_run_digest": _required(
                compilation_run_digest, "compilation_run_digest"
            ),
            "release_id": _required(release_id, "release_id"),
            "release_digest": _required(release_digest, "release_digest"),
            "plan_digest": _required(plan_digest, "plan_digest"),
            "policy_report_digest": _required(
                policy_report_digest, "policy_report_digest"
            ),
            "governed_result": canonical_data(dict(governed_result)),
            "decisions": canonical_data(dict(decisions)),
            "evidence_package": canonical_data(dict(evidence_package)),
            "error": canonical_data(dict(error)) if error is not None else None,
            "replay_of": replay_of,
            "recorded_at": _required(recorded_at, "recorded_at"),
        }
        return cls._from_payload(payload, content_digest(payload), None)

    @classmethod
    def build_failure(
        cls,
        *,
        query_run_id: str,
        tenant_id: str,
        actor: str,
        request: Mapping[str, Any],
        decision_id: str,
        error: Mapping[str, Any],
        recorded_at: str,
    ) -> "QueryRun":
        normalized_request = canonical_data(dict(request))
        normalized_error = canonical_data(dict(error))
        evidence_payload = {
            "api_version": "aof.query-failure-evidence/v1",
            "request_digest": content_digest(normalized_request),
            "failure_decision_id": decision_id,
            "error": normalized_error,
        }
        unresolved = content_digest({"state": "unresolved"})
        return cls.build(
            query_run_id=query_run_id,
            tenant_id=tenant_id,
            actor=actor,
            request=normalized_request,
            compilation_run_id="unresolved",
            compilation_run_digest=unresolved,
            release_id="unresolved",
            release_digest=unresolved,
            plan_digest=unresolved,
            policy_report_digest=unresolved,
            governed_result={"status": "failed", "error": normalized_error},
            decisions={"failure": decision_id},
            evidence_package={
                **evidence_payload,
                "package_digest": content_digest(evidence_payload),
            },
            replay_of=None,
            recorded_at=recorded_at,
            status="failed",
            error=normalized_error,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QueryRun":
        if set(value) != _FIELDS:
            raise QueryRunError("query run fields do not match the v1 contract")
        if value.get("api_version") != "aof.query-run/v1":
            raise QueryRunError("unsupported query run api_version")
        payload = {
            key: canonical_data(item)
            for key, item in value.items()
            if key not in {"run_digest", "attestation"}
        }
        supplied = str(value.get("run_digest", ""))
        if supplied != content_digest(payload):
            raise QueryRunError("run_digest does not match query run content")
        request = payload.get("request")
        if not isinstance(request, Mapping):
            raise QueryRunError("query run request must be an object")
        if payload.get("request_digest") != content_digest(request):
            raise QueryRunError("request_digest does not match query run request")
        attestation = value.get("attestation")
        if attestation is not None and not isinstance(attestation, Mapping):
            raise QueryRunError("query run attestation must be an object")
        return cls._from_payload(payload, supplied, attestation)

    @classmethod
    def _from_payload(
        cls,
        payload: Mapping[str, Any],
        digest: str,
        attestation: Mapping[str, Any] | None,
    ) -> "QueryRun":
        replay_of = payload.get("replay_of")
        return cls(
            query_run_id=_safe_id(str(payload.get("query_run_id", "")), "query_run_id"),
            tenant_id=_required(str(payload.get("tenant_id", "")), "tenant_id"),
            actor=_required(str(payload.get("actor", "")), "actor"),
            status=_status(str(payload.get("status", ""))),
            request=_freeze(payload.get("request", {})),
            request_digest=str(payload.get("request_digest", "")),
            compilation_run_id=str(payload.get("compilation_run_id", "")),
            compilation_run_digest=str(payload.get("compilation_run_digest", "")),
            release_id=str(payload.get("release_id", "")),
            release_digest=str(payload.get("release_digest", "")),
            plan_digest=str(payload.get("plan_digest", "")),
            policy_report_digest=str(payload.get("policy_report_digest", "")),
            governed_result=_freeze(payload.get("governed_result", {})),
            decisions=_freeze(payload.get("decisions", {})),
            evidence_package=_freeze(payload.get("evidence_package", {})),
            error=(
                _freeze(payload["error"])
                if isinstance(payload.get("error"), Mapping)
                else None
            ),
            replay_of=str(replay_of) if replay_of is not None else None,
            recorded_at=str(payload.get("recorded_at", "")),
            run_digest=digest,
            attestation=_freeze(attestation) if attestation is not None else None,
        )

    def with_attestation(self, attestation: Mapping[str, Any]) -> "QueryRun":
        return replace(self, attestation=_freeze(canonical_data(dict(attestation))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.query-run/v1",
            "query_run_id": self.query_run_id,
            "tenant_id": self.tenant_id,
            "actor": self.actor,
            "status": self.status,
            "request": canonical_data(self.request),
            "request_digest": self.request_digest,
            "compilation_run_id": self.compilation_run_id,
            "compilation_run_digest": self.compilation_run_digest,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "plan_digest": self.plan_digest,
            "policy_report_digest": self.policy_report_digest,
            "governed_result": canonical_data(self.governed_result),
            "decisions": canonical_data(self.decisions),
            "evidence_package": canonical_data(self.evidence_package),
            "error": canonical_data(self.error) if self.error is not None else None,
            "replay_of": self.replay_of,
            "recorded_at": self.recorded_at,
            "run_digest": self.run_digest,
            "attestation": canonical_data(self.attestation) if self.attestation else None,
        }


class SqliteQueryRunRepository:
    """Tenant-isolated immutable query-run repository."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS query_runs (
                    tenant_id TEXT NOT NULL,
                    query_run_id TEXT NOT NULL,
                    run_digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, query_run_id)
                )
                """
            )
            connection.execute("PRAGMA user_version=1")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection
    def _connection(self):
        """Transaction + close context manager (W09.01: no leaked connections)."""
        return managed_sqlite_connection(self._connect)


    def put(self, run: QueryRun) -> QueryRun:
        payload = canonical_json(run.to_dict())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM query_runs WHERE tenant_id = ? AND query_run_id = ?",
                (run.tenant_id, run.query_run_id),
            ).fetchone()
            if existing is not None:
                current = QueryRun.from_dict(json.loads(existing[0]))
                if current.run_digest != run.run_digest or current.to_dict() != run.to_dict():
                    raise QueryRunError(
                        f"query run cannot be overwritten: {run.query_run_id}"
                    )
                connection.commit()
                return current
            connection.execute(
                "INSERT INTO query_runs (tenant_id, query_run_id, run_digest, payload) "
                "VALUES (?, ?, ?, ?)",
                (run.tenant_id, run.query_run_id, run.run_digest, payload),
            )
            connection.commit()
            return run
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, query_run_id: str, *, tenant_id: str) -> QueryRun | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT run_digest, payload FROM query_runs "
                "WHERE tenant_id = ? AND query_run_id = ?",
                (tenant_id, query_run_id),
            ).fetchone()
        if row is None:
            return None
        run = QueryRun.from_dict(json.loads(row[1]))
        if run.run_digest != row[0] or run.tenant_id != tenant_id:
            raise QueryRunError("stored query run integrity check failed")
        return run

    def schema_version(self) -> int:
        with self._connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def verify_all(self) -> dict[str, Any]:
        errors = []
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT tenant_id, query_run_id, run_digest, payload FROM query_runs"
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"sqlite integrity check failed: {integrity}")
        for tenant_id, query_run_id, run_digest, payload in rows:
            try:
                run = QueryRun.from_dict(json.loads(payload))
                if run.tenant_id != tenant_id or run.query_run_id != query_run_id:
                    raise QueryRunError("indexed query run identity does not match payload")
                if run.run_digest != run_digest:
                    raise QueryRunError("indexed query run digest does not match payload")
            except Exception as exc:
                errors.append(f"{tenant_id}/{query_run_id}: {exc}")
        return {
            "valid": not errors,
            "query_run_count": len(rows),
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


class HmacQueryEvidenceAttestor:
    """Detached HMAC attestation binding a run to its exact evidence package."""

    def __init__(self, *, key_id: str, secret: bytes) -> None:
        if not key_id.strip() or not secret:
            raise ValueError("key_id and secret are required")
        self.key_id = key_id
        self._secret = secret

    def sign(self, run: QueryRun) -> dict[str, Any]:
        payload = {
            "api_version": "aof.query-evidence-attestation/v1",
            "algorithm": "hmac-sha256",
            "key_id": self.key_id,
            "tenant_id": run.tenant_id,
            "query_run_id": run.query_run_id,
            "query_run_digest": run.run_digest,
            "evidence_package_digest": run.evidence_package.get("package_digest"),
        }
        return {**payload, "signature": self._signature(payload)}

    def verify(self, attestation: Mapping[str, Any], *, run: QueryRun) -> bool:
        signature = attestation.get("signature")
        if not isinstance(signature, str):
            return False
        payload = {key: value for key, value in attestation.items() if key != "signature"}
        expected = {
            "api_version": "aof.query-evidence-attestation/v1",
            "algorithm": "hmac-sha256",
            "key_id": self.key_id,
            "tenant_id": run.tenant_id,
            "query_run_id": run.query_run_id,
            "query_run_digest": run.run_digest,
            "evidence_package_digest": run.evidence_package.get("package_digest"),
        }
        return payload == expected and hmac.compare_digest(
            signature, self._signature(payload)
        )

    def _signature(self, payload: Mapping[str, Any]) -> str:
        return hmac.new(
            self._secret,
            canonical_json(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
