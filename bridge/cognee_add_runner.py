# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Thin bridge for invoking cognee.add from AOF."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from bridge.document_parser.ingest_helper import (
    log_parse_context,
    maybe_parse_local_file,
)
from bridge.errors import AddExecutionError, CogneeImportError

logger = logging.getLogger(__name__)


# 兼容旧名：cognee_add_runner._maybe_parse_local_file → ingest_helper.maybe_parse_local_file
_maybe_parse_local_file = maybe_parse_local_file

# cognee 穿透注入的环境变量（若被手动设置，说明调用方绕过 spec.ontology）
_ONTOLOGY_ENV_VARS = ("ONTOLOGY_FILE_PATH", "ONTOLOGY_RESOLVER", "ONTOLOGY_MATCHING_STRATEGY")


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


def _check_ontology_adherence(spec: dict[str, Any]) -> dict[str, Any]:
    """检查调用方对 AOF 官方 ontology 配置的遵循情况（不抛错，仅记录结构化诊断）。

    目的：防止"绕过 AOF 官方逻辑、直接以环境变量注入 ontology"。ontology 应在
    ``spec.ontology.file`` 中声明，并在 **cognify** 阶段经 ``ontology_adapter`` 注入；
    add 阶段不消费 ontology（仅在此校验配置有效性，避免误用）。

    Returns:
        {"ontology_spec": bool, "file_exists": bool|None, "via_env": bool,
         "used_env_vars": list[str]}
    """
    ontology = spec.get("ontology") or {}
    ontology_file = ontology.get("file")
    via_env = [v for v in _ONTOLOGY_ENV_VARS if os.environ.get(v)]
    file_exists = None
    if ontology_file:
        file_exists = Path(str(ontology_file)).exists()

    # 1) spec 配了 ontology 但文件不存在 → 警告（add 阶段不生效，cognify 会拦截）
    if ontology_file and not file_exists:
        logger.warning("[ontology] spec.ontology.file 不存在: %s （add 阶段不消费 ontology，cognify 阶段将硬校验）", ontology_file)

    # 2) 检测到环境变量注入但 spec 未声明 → 穿透警告
    if via_env and not ontology_file:
        logger.warning(
            "[ontology] 检测到环境变量注入 %s，建议改走 spec.ontology（否则 AOF 的预检/装配层无法感知）",
            via_env,
        )
    # 3) spec 与环境变量都设置了，且不一致 → 提示以 spec 为准
    elif via_env and ontology_file:
        logger.warning(
            "[ontology] spec.ontology.file 与环境变量 %s 并存，AOF 将以 spec.ontology 为准（走可审计链路）",
            via_env,
        )

    return {
        "ontology_spec": bool(ontology_file),
        "file_exists": file_exists,
        "via_env": bool(via_env),
        "used_env_vars": via_env,
    }


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

    # ontology 遵循性诊断（add 阶段不消费 ontology，仅校验与警告穿透）
    _check_ontology_adherence(spec)

    # 解析层：本地文件先归一化再 add（设计稿 §5.1）
    data_to_add, parser_ctx = _maybe_parse_local_file(data)
    log_parse_context(parser_ctx, caller="cognee_add_runner", filename=str(data))

    try:
        return await _add_with_retry(
            cognee=cognee,
            data=data_to_add,
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
