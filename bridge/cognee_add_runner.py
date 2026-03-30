"""Thin bridge for invoking cognee.add from AOF."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from bridge.errors import AddExecutionError, CogneeImportError


def _import_cognee(cognee_root: str | None = None):
    try:
        if cognee_root:
            root = str(Path(cognee_root).resolve())
            if root not in sys.path:
                sys.path.insert(0, root)
        import cognee  # type: ignore

        return cognee
    except Exception as e:  # pragma: no cover
        raise CogneeImportError(
            f"无法导入 cognee。请检查 cognee.root 配置与依赖。root={cognee_root}"
        ) from e


def _build_add_kwargs(spec: dict[str, Any]) -> dict[str, Any]:
    dataset_id = spec.get("dataset_id")
    dataset_name = spec.get("dataset")

    kwargs: dict[str, Any] = {}
    if dataset_id:
        kwargs["dataset_id"] = dataset_id
    elif dataset_name:
        kwargs["dataset_name"] = dataset_name

    node_set = spec.get("node_set")
    if node_set:
        kwargs["node_set"] = node_set

    return kwargs


async def _add_with_retry(
    cognee,
    data: Any,
    add_kwargs: dict[str, Any],
    retries: int,
    backoff_seconds: float,
):
    attempt = 0
    while True:
        try:
            return await cognee.add(data, **add_kwargs)
        except Exception:
            if attempt >= retries:
                raise
            await asyncio.sleep(max(0.0, backoff_seconds) * (2**attempt))
            attempt += 1


async def run_add_from_spec(spec: dict[str, Any], data: Any):
    runtime = spec.get("runtime") or {}
    cognee_cfg = spec.get("cognee") or {}

    cognee = _import_cognee(cognee_cfg.get("root"))
    add_kwargs = _build_add_kwargs(spec)

    retries = int(runtime.get("retries", 0))
    backoff_seconds = float(runtime.get("backoff_seconds", 1.0))

    try:
        return await _add_with_retry(
            cognee=cognee,
            data=data,
            add_kwargs=add_kwargs,
            retries=retries,
            backoff_seconds=backoff_seconds,
        )
    except Exception as e:  # pragma: no cover
        detail = f"{e.__class__.__name__}: {e}"
        raise AddExecutionError(
            "cognee.add 执行失败"
            f"（retries={retries}, backoff_seconds={backoff_seconds}）; "
            f"root_cause={detail}"
        ) from e
