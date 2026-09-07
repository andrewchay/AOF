# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W04.01 — SQLite-backed persistence facade for RBAC/Tenant managers.

Reference-local durable store for the in-memory fallback branch of
RBACManager/TenantManager. Domain objects round-trip through a generic
dataclass/enum/datetime JSON codec; managers keep their exact in-memory
semantics (dict-like and list-like views), while every mutation is
written through to a single SQLite file so that a *new manager instance*
(and therefore a new process) reads back persisted users, roles,
assignments and tenants.

Uniqueness (user username, tenant slug) is enforced by UNIQUE indexes at
the database level, so concurrent cross-process creation cannot double-
insert. All reads/writes use managed_sqlite_connection (transaction +
always-close, W09.01).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Callable, Iterator, MutableMapping, MutableSequence
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from bridge.persistence.sqlite_support import managed_sqlite_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS iam_users (
    key TEXT PRIMARY KEY,
    username TEXT UNIQUE,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iam_roles (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iam_assignments (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iam_tenants (
    key TEXT PRIMARY KEY,
    slug TEXT UNIQUE,
    payload TEXT NOT NULL
);
"""


class IamStoreError(ValueError):
    """Raised when the IAM store cannot persist or retrieve an object."""


# ---------------------------------------------------------------------------
# Generic JSON codec: dataclasses, enums, datetimes, paths, containers.
# ---------------------------------------------------------------------------


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return {"__dt__": value.isoformat()}
    if isinstance(value, Enum):
        return {"__enum__": [type(value).__module__, type(value).__name__, value.value]}
    if isinstance(value, Path):
        return {"__path__": str(value)}
    if isinstance(value, dict):
        return {"__map__": {str(k): _encode(v) for k, v in value.items()}}
    if isinstance(value, (list, tuple)):
        return {"__seq__": [_encode(v) for v in value]}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: _encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {
            "__obj__": [
                type(value).__module__,
                type(value).__name__,
                fields,
            ]
        }
    raise IamStoreError(f"cannot serialize value of type {type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "__dt__" in value:
            return datetime.fromisoformat(value["__dt__"])
        if "__enum__" in value:
            module, name, raw = value["__enum__"]
            import importlib

            enum_cls = getattr(importlib.import_module(module), name)
            return enum_cls(raw)
        if "__path__" in value:
            return Path(value["__path__"])
        if "__map__" in value:
            return {k: _decode(v) for k, v in value["__map__"].items()}
        if "__seq__" in value:
            return [_decode(v) for v in value["__seq__"]]
        if "__obj__" in value:
            module, name, fields = value["__obj__"]
            import importlib

            cls = getattr(importlib.import_module(module), name)
            return cls(**{k: _decode(v) for k, v in fields.items()})
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class _SqliteMappingView(MutableMapping):
    """Dict-like write-through view over one keyed object table."""

    def __init__(
        self,
        store: "SqliteIamStore",
        table: str,
        key_fn: Callable[[Any], str],
        natural_key_fn: Callable[[Any], tuple[str | None, Any]] | None = None,
    ) -> None:
        self._store = store
        self._table = table
        self._key_fn = key_fn
        self._natural_key_fn = natural_key_fn

    def __setitem__(self, key: str, obj: Any) -> None:
        payload = json.dumps(_encode(obj), ensure_ascii=False)
        if self._natural_key_fn is not None:
            column, value = self._natural_key_fn(obj)
            sql = (
                f"INSERT INTO {self._table}(key, {column}, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload"
            )
            params: tuple = (key, value, payload)
        else:
            sql = (
                f"INSERT INTO {self._table}(key, payload) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload"
            )
            params = (key, payload)
        with managed_sqlite_connection(self._store._connect) as conn:
            try:
                conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                raise IamStoreError(
                    f"{self._table} natural-key conflict: {exc}"
                ) from exc

    def __getitem__(self, key: str) -> Any:
        row = self._fetch_one(key)
        if row is None:
            raise KeyError(key)
        return _decode(json.loads(row))

    def get(self, key: str, default: Any = None) -> Any:  # noqa: D105 - Mapping API
        row = self._fetch_one(key)
        return _decode(json.loads(row)) if row is not None else default

    def __delitem__(self, key: str) -> None:
        with managed_sqlite_connection(self._store._connect) as conn:
            cur = conn.execute(f"DELETE FROM {self._table} WHERE key = ?", (key,))
            if cur.rowcount == 0:
                raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        with managed_sqlite_connection(self._store._connect) as conn:
            for row in conn.execute(f"SELECT key FROM {self._table}").fetchall():
                yield row[0]

    def __len__(self) -> int:
        with managed_sqlite_connection(self._store._connect) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {self._table}").fetchone()[0]

    def values(self) -> list[Any]:  # type: ignore[override]
        with managed_sqlite_connection(self._store._connect) as conn:
            rows = conn.execute(f"SELECT payload FROM {self._table}").fetchall()
        return [_decode(json.loads(r[0])) for r in rows]

    def _fetch_one(self, key: str) -> str | None:
        with managed_sqlite_connection(self._store._connect) as conn:
            row = conn.execute(
                f"SELECT payload FROM {self._table} WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None


class _SqliteSequenceView(MutableSequence):
    """List-like write-through view over the assignments table."""

    def __init__(self, store: "SqliteIamStore") -> None:
        self._store = store

    def __len__(self) -> int:
        with managed_sqlite_connection(self._store._connect) as conn:
            return conn.execute("SELECT COUNT(*) FROM iam_assignments").fetchone()[0]

    def __iter__(self) -> Iterator[Any]:
        with managed_sqlite_connection(self._store._connect) as conn:
            rows = conn.execute(
                "SELECT payload FROM iam_assignments ORDER BY seq"
            ).fetchall()
        for r in rows:
            yield _decode(json.loads(r[0]))

    def __getitem__(self, index: int | slice) -> Any:
        rows = list(self)
        return rows[index]

    def __setitem__(self, index: int | slice, value: Any) -> None:
        if isinstance(index, slice):
            # Replace the whole sequence in one transaction (in-place [:] = kept)
            with managed_sqlite_connection(self._store._connect) as conn:
                conn.execute("DELETE FROM iam_assignments")
                for obj in value:
                    conn.execute(
                        "INSERT INTO iam_assignments(payload) VALUES (?)",
                        (json.dumps(_encode(obj), ensure_ascii=False),),
                    )
            return
        raise NotImplementedError("single-item assignment is not supported")

    def __delitem__(self, index: int | slice) -> None:
        rows = list(self)
        del rows[index]
        self[:] = rows

    def append(self, obj: Any) -> None:
        with managed_sqlite_connection(self._store._connect) as conn:
            conn.execute(
                "INSERT INTO iam_assignments(payload) VALUES (?)",
                (json.dumps(_encode(obj), ensure_ascii=False),),
            )

    def insert(self, index: int, value: Any) -> None:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class SqliteIamStore:
    """Durable reference-local store for RBAC/Tenant managers."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with managed_sqlite_connection(self._connect) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @property
    def users(self) -> _SqliteMappingView:
        return _SqliteMappingView(
            self,
            "iam_users",
            key_fn=lambda u: u.id,
            natural_key_fn=lambda u: ("username", u.username),
        )

    @property
    def roles(self) -> _SqliteMappingView:
        return _SqliteMappingView(self, "iam_roles", key_fn=lambda r: r.id)

    @property
    def assignments(self) -> _SqliteSequenceView:
        return _SqliteSequenceView(self)

    @property
    def tenants(self) -> _SqliteMappingView:
        return _SqliteMappingView(
            self,
            "iam_tenants",
            key_fn=lambda t: t.id,
            natural_key_fn=lambda t: ("slug", t.slug),
        )
