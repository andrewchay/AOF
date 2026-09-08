"""W08.04 — PostgreSQL-backed IAM store (same facade contract as SQLite).

SqliteIamStore's Mapping/Sequence facade contract is preserved exactly:
managers (RBACManager/TenantManager) consume users/roles/assignments/
tenants without knowing which backend is underneath. PG adaptations:
- ON CONFLICT upserts (PG-native)
- dict_row results via psycopg
- JSON payload stored as TEXT (same as SQLite path)
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, MutableMapping, MutableSequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bridge.persistence.sqlite_iam_store import IamStoreError, _decode, _encode

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
    seq BIGSERIAL PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iam_tenants (
    key TEXT PRIMARY KEY,
    slug TEXT UNIQUE,
    payload TEXT NOT NULL
);
"""


class _PgMappingView(MutableMapping):
    def __init__(self, store: "PostgresIamStore", table: str, key_fn, natural_key_fn=None) -> None:
        self._store = store
        self._table = table
        self._key_fn = key_fn
        self._natural_key_fn = natural_key_fn

    def __setitem__(self, key: str, obj: Any) -> None:
        payload = json.dumps(_encode(obj), ensure_ascii=False)
        with self._store._connect() as conn:
            try:
                if self._natural_key_fn is not None:
                    column, value = self._natural_key_fn(obj)
                    conn.execute(
                        f"INSERT INTO {self._table}(key, {column}, payload) VALUES (%s, %s, %s) "
                        "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload",
                        (key, value, payload),
                    )
                else:
                    conn.execute(
                        f"INSERT INTO {self._table}(key, payload) VALUES (%s, %s) "
                        "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload",
                        (key, payload),
                    )
            except psycopg.IntegrityError as exc:
                raise IamStoreError(f"{self._table} natural-key conflict: {exc}") from exc

    def __getitem__(self, key: str) -> Any:
        row = self._fetch_one(key)
        if row is None:
            raise KeyError(key)
        return _decode(json.loads(row))

    def get(self, key: str, default: Any = None) -> Any:
        row = self._fetch_one(key)
        return _decode(json.loads(row)) if row is not None else default

    def __delitem__(self, key: str) -> None:
        with self._store._connect() as conn:
            cur = conn.execute(f"DELETE FROM {self._table} WHERE key = %s", (key,))
            if cur.rowcount == 0:
                raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        with self._store._connect() as conn:
            for row in conn.execute(f"SELECT key FROM {self._table}").fetchall():  # type: ignore[call-overload,index,override,assignment]
                yield row["key"]  # type: ignore[call-overload,index,override,assignment,operator]

    def __len__(self) -> int:
        with self._store._connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {self._table}").fetchone()["count"]  # type: ignore[call-overload,index,override,assignment]

    def values(self) -> list[Any]:  # type: ignore[call-overload,index,override,assignment,operator]
        with self._store._connect() as conn:
            rows = conn.execute(f"SELECT payload FROM {self._table}").fetchall()  # type: ignore[call-overload,index,override,assignment]
        return [_decode(json.loads(r["payload"])) for r in rows]  # type: ignore[call-overload,index,override,assignment,operator]

    def _fetch_one(self, key: str) -> str | None:
        with self._store._connect() as conn:
            row = conn.execute(
                f"SELECT payload FROM {self._table} WHERE key = %s", (key,)
            ).fetchone()  # type: ignore[call-overload,index,override,assignment]
        return row["payload"] if row else None  # type: ignore[call-overload,index,override,assignment,operator]


class _PgSequenceView(MutableSequence):
    def __init__(self, store: "PostgresIamStore") -> None:
        self._store = store

    def __len__(self) -> int:
        with self._store._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM iam_assignments").fetchone()["count"]  # type: ignore[call-overload,index,override,assignment]

    def __iter__(self) -> Iterator[Any]:
        with self._store._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM iam_assignments ORDER BY seq"
            ).fetchall()  # type: ignore[call-overload,index,override,assignment]
        for r in rows:
            yield _decode(json.loads(r["payload"]))  # type: ignore[call-overload,index,override,assignment,operator]

    def __getitem__(self, index: int) -> Any:  # type: ignore[call-overload,index,override,assignment,operator]
        return list(self)[index]

    def __setitem__(self, index, value) -> None:
        if isinstance(index, slice):
            with self._store._connect() as conn:
                conn.execute("DELETE FROM iam_assignments")
                for obj in value:
                    conn.execute(
                        "INSERT INTO iam_assignments(payload) VALUES (%s)",
                        (json.dumps(_encode(obj), ensure_ascii=False),),
                    )
            return
        raise NotImplementedError("single-item assignment is not supported")

    def __delitem__(self, index: int) -> None:  # type: ignore[call-overload,index,override,assignment,operator]
        rows = list(self)
        del rows[index]
        self[:] = rows

    def append(self, obj: Any) -> None:
        with self._store._connect() as conn:
            conn.execute(
                "INSERT INTO iam_assignments(payload) VALUES (%s)",
                (json.dumps(_encode(obj), ensure_ascii=False),),
            )

    def insert(self, index: int, value: Any) -> None:
        raise NotImplementedError


class PostgresIamStore:
    """Durable IAM store on PostgreSQL (W08.04)."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get(
            "AOF_IAM_PG_DSN", "postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control"
        )
        self._init_schema()

    def _connect(self) -> psycopg.Connection:
        connection = psycopg.connect(self.dsn)
        connection.row_factory = dict_row  # type: ignore[assignment]
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(_SCHEMA)
            connection.commit()

    @property
    def users(self) -> _PgMappingView:
        return _PgMappingView(
            self, "iam_users",
            key_fn=lambda u: u.id,
            natural_key_fn=lambda u: ("username", u.username),
        )

    @property
    def roles(self) -> _PgMappingView:
        return _PgMappingView(self, "iam_roles", key_fn=lambda r: r.id)

    @property
    def assignments(self) -> _PgSequenceView:
        return _PgSequenceView(self)

    @property
    def tenants(self) -> _PgMappingView:
        return _PgMappingView(
            self, "iam_tenants",
            key_fn=lambda t: t.id,
            natural_key_fn=lambda t: ("slug", t.slug),
        )
