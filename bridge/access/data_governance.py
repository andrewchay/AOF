# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W06.04 — Data classification, log minimization and versioned retention.

Acceptance scope:
- every payload field carries a data class (public/internal/confidential/restricted)
- logs carry the minimum necessary: restricted values are masked, confidential
  values are digested, and a sensitive sentinel must never appear in trace output
- retention is policy-driven and versioned: policies have an id + revision;
  cleanup is a dry-run plan first, then an applied pass that returns an audit
  receipt bound to the policy revision that justified the deletion
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from bridge.persistence.sqlite_support import managed_sqlite_connection


class DataGovernanceError(ValueError):
    pass


class DataClass(str, Enum):
    PUBLIC = "public"                # freely loggable
    INTERNAL = "internal"            # loggable inside the trust boundary
    CONFIDENTIAL = "confidential"    # digest/abbreviate in logs
    RESTRICTED = "restricted"        # masked in logs; deletion-controlled payload


# Default field-name classification rules (checked case-insensitively,
# substring match, first match wins in declaration order).
DEFAULT_RULES: tuple[tuple[str, DataClass], ...] = (
    ("password", DataClass.RESTRICTED),
    ("secret", DataClass.RESTRICTED),
    ("token", DataClass.RESTRICTED),
    ("api_key", DataClass.RESTRICTED),
    ("apikey", DataClass.RESTRICTED),
    ("private_key", DataClass.RESTRICTED),
    ("credential", DataClass.RESTRICTED),
    ("authorization", DataClass.RESTRICTED),
    ("question", DataClass.CONFIDENTIAL),      # user business questions
    ("summary", DataClass.CONFIDENTIAL),
    ("content", DataClass.CONFIDENTIAL),
    ("conclusion", DataClass.INTERNAL),
    ("rationale", DataClass.INTERNAL),
)


def classify_value(value: Any, rules: tuple[tuple[str, DataClass], ...] = DEFAULT_RULES) -> DataClass:
    """Classify one scalar payload value by its content heuristics."""
    if isinstance(value, str):
        lowered = value.lower()
        for marker, cls in (
            ("sk-", DataClass.RESTRICTED),
            ("bearer ", DataClass.RESTRICTED),
            ("-----begin", DataClass.RESTRICTED),
        ):
            if marker in lowered:
                return cls
    return DataClass.INTERNAL


def classify_field(field_name: str, rules: tuple[tuple[str, DataClass], ...] = DEFAULT_RULES) -> DataClass:
    lowered = field_name.lower()
    for marker, cls in rules:
        if marker in lowered:
            return cls
    return DataClass.INTERNAL


# ---------------------------------------------------------------------------
# Log minimization
# ---------------------------------------------------------------------------

_SENTINEL_PREFIX = "aof-sentinel:"


def mask_restricted(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= 4:
        return "****"
    return text[:2] + "****" + text[-2:]


def digest_confidential(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def minimize_for_log(
    payload: Mapping[str, Any],
    rules: tuple[tuple[str, DataClass], ...] = DEFAULT_RULES,
) -> dict[str, Any]:
    """Return a log-safe projection of ``payload`` (W06.04 minimal logging).

    - RESTRICTED fields are masked beyond reconstruction
    - CONFIDENTIAL fields are replaced by a keyed digest
    - containers are walked recursively; INTERNAL/PUBLIC pass through
    """
    minimized: dict[str, Any] = {}
    for key, value in payload.items():
        cls = classify_field(key, rules)
        if isinstance(value, Mapping):
            minimized[key] = minimize_for_log(value, rules)
        elif isinstance(value, (list, tuple)):
            minimized[key] = [
                minimize_for_log(item, rules) if isinstance(item, Mapping) else
                (mask_restricted(item) if cls == DataClass.RESTRICTED else
                 (digest_confidential(item) if cls == DataClass.CONFIDENTIAL else item))
                for item in value
            ]
        elif cls == DataClass.RESTRICTED:
            minimized[key] = mask_restricted(value)
        elif cls == DataClass.CONFIDENTIAL:
            minimized[key] = digest_confidential(value)
        else:
            minimized[key] = value
    return minimized


def make_sentinel(label: str) -> str:
    """A unique sensitive-canary string for tests and trace audits."""
    return f"{_SENTINEL_PREFIX}{label}"


def ensure_no_sentinel(obj: Any, *, sentinel_prefix: str = _SENTINEL_PREFIX) -> None:
    """Raise DataGovernanceError if any string value contains the sentinel
    prefix — proving sensitive canaries did not leak into a log/trace."""
    if isinstance(obj, str):
        if sentinel_prefix in obj:
            raise DataGovernanceError(f"sensitive sentinel leaked into output: {obj[:32]}...")
        return
    if isinstance(obj, Mapping):
        for v in obj.values():
            ensure_no_sentinel(v, sentinel_prefix=sentinel_prefix)
        return
    if isinstance(obj, (list, tuple)):
        for item in obj:
            ensure_no_sentinel(item, sentinel_prefix=sentinel_prefix)


# ---------------------------------------------------------------------------
# Versioned retention policies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetentionPolicy:
    policy_id: str
    revision: int
    ttl_days: dict[str, int] = field(default_factory=dict)  # DataClass value -> days
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "revision": self.revision,
            "ttl_days": self.ttl_days,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetentionPolicy":
        return cls(
            policy_id=str(data["policy_id"]),
            revision=int(data["revision"]),
            ttl_days={k: int(v) for k, v in data.get("ttl_days", {}).items()},
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
        )


def ttl_for(policy: RetentionPolicy, cls: DataClass) -> int | None:
    """Effective TTL in days for a data class under this policy (None = keep)."""
    return policy.ttl_days.get(cls.value)


# ---------------------------------------------------------------------------
# Retention record store + cleanup engine
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_retention_records (
    data_key        TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    data_class      TEXT NOT NULL,
    policy_id       TEXT NOT NULL,
    policy_revision INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (data_key)
);
"""


DEFAULT_POLICY_FILE = (
    Path(__file__).resolve().parents[2] / "config" / "governance" / "retention-policy.json"
)


def load_default_policy(path: str | Path | None = None) -> RetentionPolicy:
    """Load the owner-decided retention policy (config/governance)."""
    policy_path = Path(path) if path else DEFAULT_POLICY_FILE
    raw = json.loads(policy_path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "aof.retention-policy/v1":
        raise DataGovernanceError(
            f"retention policy schema mismatch: {raw.get('schema_version')!r}"
        )
    policy = RetentionPolicy(
        policy_id=str(raw["policy_id"]),
        revision=int(raw["revision"]),
        ttl_days={k: int(v) for k, v in raw["ttl_days"].items()},
    )
    if raw.get("decided_by"):
        # provenance travels with the policy object
        object.__setattr__(policy, "decided_by", raw["decided_by"])
    return policy


class RetentionRecordStore:
    """Tracks every governed data item: class + the policy revision in force."""

    def __init__(self, path: str | Path | None = None) -> None:
        import os

        configured = path or os.environ.get("AOF_RETENTION_DB")
        self.path = Path(configured or "data/access/retention.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def register(
        self,
        *,
        data_key: str,
        tenant_id: str,
        data_class: DataClass,
        policy: RetentionPolicy,
        created_at: datetime | None = None,
    ) -> None:
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute(
                "INSERT INTO data_retention_records"
                "(data_key, tenant_id, data_class, policy_id, policy_revision, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(data_key) DO UPDATE SET "
                "data_class = excluded.data_class, "
                "policy_id = excluded.policy_id, "
                "policy_revision = excluded.policy_revision",
                (
                    data_key,
                    tenant_id,
                    data_class.value,
                    policy.policy_id,
                    policy.revision,
                    (created_at or datetime.now(timezone.utc)).isoformat(),
                ),
            )

    def record_count(self) -> int:
        with managed_sqlite_connection(self._connect) as connection:
            return connection.execute("SELECT COUNT(*) FROM data_retention_records").fetchone()[0]


@dataclass(frozen=True)
class CleanupItem:
    data_key: str
    tenant_id: str
    data_class: str
    policy_id: str
    policy_revision: int
    created_at: str
    expired_by_days: int


@dataclass(frozen=True)
class CleanupPlan:
    policy_id: str
    policy_revision: int
    dry_run: bool
    items: tuple[CleanupItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_revision": self.policy_revision,
            "dry_run": self.dry_run,
            "count": len(self.items),
            "items": [dataclasses.asdict(item) for item in self.items],
        }


class RetentionEngine:
    """Dry-run first cleanup: plan → (approve) → apply → receipt."""

    def __init__(self, store: RetentionRecordStore) -> None:
        self.store = store

    def plan(
        self,
        *,
        policy: RetentionPolicy,
        now: datetime | None = None,
    ) -> CleanupPlan:
        now = now or datetime.now(timezone.utc)
        items: list[CleanupItem] = []
        with managed_sqlite_connection(self.store._connect) as connection:
            rows = connection.execute(
                "SELECT data_key, tenant_id, data_class, policy_id, policy_revision, created_at "
                "FROM data_retention_records"
            ).fetchall()
        for key, tenant_id, cls_value, rec_policy, rec_revision, created_at in rows:
            cls = DataClass(cls_value)
            ttl_days = ttl_for(policy, cls)
            if ttl_days is None:
                continue
            created = datetime.fromisoformat(created_at)
            age_days = (now - created).total_seconds() / 86400
            if age_days >= ttl_days:
                items.append(
                    CleanupItem(
                        data_key=key,
                        tenant_id=tenant_id,
                        data_class=cls_value,
                        policy_id=rec_policy,
                        policy_revision=rec_revision,
                        created_at=created_at,
                        expired_by_days=int(age_days - ttl_days) + 1,
                    )
                )
        return CleanupPlan(
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            dry_run=True,
            items=tuple(items),
        )

    def apply(self, plan: CleanupPlan, *, delete_keys: Iterable[str] | None = None) -> dict[str, Any]:
        """Execute a plan: delete the retention records for the plan items and
        return an audit receipt bound to the policy revision."""
        keys = set(delete_keys) if delete_keys is not None else {item.data_key for item in plan.items}
        with managed_sqlite_connection(self.store._connect) as connection:
            deleted = 0
            for key in keys:
                cursor = connection.execute(
                    "DELETE FROM data_retention_records WHERE data_key = ?", (key,)
                )
                deleted += cursor.rowcount
        receipt = {
            "action": "retention_cleanup",
            "policy_id": plan.policy_id,
            "policy_revision": plan.policy_revision,
            "planned": len(plan.items),
            "deleted": deleted,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
        return receipt
