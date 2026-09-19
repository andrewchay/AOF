"""Tenant-owned dataset registry for governed MCP listing.

Cognee's default-user dataset enumeration is global.  This registry is the
explicit ownership proof used by the MCP boundary: a dataset is invisible
unless the authenticated tenant has registered it here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _registry_path(state_root: Path, tenant_id: str) -> Path:
    return state_root / tenant_id / "dataset-ownership.json"


def _read(path: Path, tenant_id: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "tenant_id": tenant_id, "dataset_ids": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"tenant dataset registry is unreadable: {path}") from exc
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("tenant_id") != tenant_id
        or not isinstance(payload.get("dataset_ids"), list)
        or not all(isinstance(item, str) and item for item in payload["dataset_ids"])
    ):
        raise RuntimeError(f"tenant dataset registry is invalid: {path}")
    return payload


def list_owned_dataset_ids(*, state_root: Path, tenant_id: str) -> set[str]:
    """Return only IDs explicitly owned by this tenant; absent registry is empty."""
    return set(_read(_registry_path(state_root, tenant_id), tenant_id)["dataset_ids"])


def register_owned_dataset(*, state_root: Path, tenant_id: str, dataset_id: str) -> None:
    """Persist an ownership proof.  Caller must already have authenticated tenant context."""
    if not dataset_id:
        raise ValueError("dataset_id is required")
    path = _registry_path(state_root, tenant_id)
    payload = _read(path, tenant_id)
    ids = set(payload["dataset_ids"])
    ids.add(dataset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"schema_version": SCHEMA_VERSION, "tenant_id": tenant_id, "dataset_ids": sorted(ids)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)
