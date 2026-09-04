"""Thin bridge for invoking Cognee from AOF.

This module maps AOF spec to Cognee calls without re-implementing Cognee logic.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from bridge.errors import CogneeImportError, CognifyExecutionError, OntologyConfigError
from bridge.spec_mapper import map_aof_spec_to_cognee
from bridge.ontology_adapter import apply_ontology


def _import_cognee(cognee_root: str | None = None):
    try:
        if cognee_root:
            root = str(Path(cognee_root).resolve())
            if root not in sys.path:
                sys.path.insert(0, root)
        import cognee  # type: ignore

        return cognee
    except Exception as e:  # pragma: no cover - runtime dependency boundary
        raise CogneeImportError(
            f"无法导入 cognee。请检查 cognee.root 配置与依赖。root={cognee_root}"
        ) from e


def _build_ontology_config(spec: dict[str, Any]):
    """从 spec 构建 cognee ontology config（薄包装，复用 ontology_adapter.apply_ontology）。"""
    try:
        return apply_ontology(spec)
    except OntologyConfigError:
        raise
    except Exception as e:  # pragma: no cover - runtime dependency boundary
        raise OntologyConfigError("构建 ontology config 失败") from e


async def _cognify_with_retry(cognee, kwargs: dict[str, Any], retries: int, backoff_seconds: float):
    attempt = 0
    while True:
        try:
            return await cognee.cognify(**kwargs)
        except Exception:
            if attempt >= retries:
                raise
            await asyncio.sleep(max(0.0, backoff_seconds) * (2**attempt))
            attempt += 1


async def run_cognify_from_spec(spec: dict[str, Any]):
    runtime = spec.get("runtime") or {}
    cognee_cfg = spec.get("cognee") or {}

    cognee = _import_cognee(cognee_cfg.get("root"))

    cognify_args = map_aof_spec_to_cognee(spec)
    config = _build_ontology_config(spec)

    kwargs: dict[str, Any] = {
        "config": config,
        "vector_db_config": runtime.get("vector_db_config"),
        "graph_db_config": runtime.get("graph_db_config"),
        **cognify_args,
    }

    retries = int(runtime.get("retries", 0))
    backoff_seconds = float(runtime.get("backoff_seconds", 1.0))

    try:
        return await _cognify_with_retry(
            cognee=cognee,
            kwargs=kwargs,
            retries=retries,
            backoff_seconds=backoff_seconds,
        )
    except Exception as e:  # pragma: no cover - runtime dependency boundary
        detail = f"{e.__class__.__name__}: {e}"
        raise CognifyExecutionError(
            "cognee.cognify 执行失败"
            f"（retries={retries}, backoff_seconds={backoff_seconds}）; "
            f"root_cause={detail}"
        ) from e
