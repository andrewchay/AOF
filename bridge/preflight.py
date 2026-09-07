# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Preflight checks for AOF runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from bridge.errors import PreflightError


def _check_cognee_root(spec: dict, issues: list[str]) -> str | None:
    cognee_cfg = spec.get("cognee") or {}
    cognee_root = cognee_cfg.get("root")
    if cognee_root:
        p = Path(cognee_root)
        if not p.exists():
            issues.append(f"cognee.root 不存在: {cognee_root}")
            return None
        if not p.is_dir():
            issues.append(f"cognee.root 不是目录: {cognee_root}")
            return None
        return str(p)
    return None


def _check_cognee_importable(cognee_root: str | None, issues: list[str]) -> None:
    if not cognee_root:
        return

    added = False
    if cognee_root not in sys.path:
        sys.path.insert(0, cognee_root)
        added = True

    try:
        import cognee  # type: ignore  # noqa: F401
    except ModuleNotFoundError as e:
        issues.append(f"cognee 依赖缺失: {e.name}")
    except Exception as e:
        issues.append(f"cognee 导入失败: {e.__class__.__name__}: {e}")
    finally:
        if added:
            try:
                sys.path.remove(cognee_root)
            except ValueError:
                pass


def preflight_checks_for_cognify(spec: dict, require_api_key: bool = True) -> list[str]:
    issues: list[str] = []

    cognee_root = _check_cognee_root(spec, issues)
    _check_cognee_importable(cognee_root, issues)

    ontology = spec.get("ontology") or {}
    ontology_file = ontology.get("file")
    if ontology_file and not Path(ontology_file).exists():
        issues.append(f"ontology.file 不存在: {ontology_file}")

    if require_api_key and not os.environ.get("LLM_API_KEY"):
        issues.append("缺少环境变量 LLM_API_KEY")

    return issues


def ensure_preflight_for_cognify(spec: dict, require_api_key: bool = True) -> None:
    issues = preflight_checks_for_cognify(spec, require_api_key=require_api_key)
    if issues:
        raise PreflightError("Preflight 失败:\n- " + "\n- ".join(issues))


def preflight_checks_for_add(
    spec: dict,
    data_paths: list[str] | None = None,
    require_api_key: bool = True,
) -> list[str]:
    issues: list[str] = []

    cognee_root = _check_cognee_root(spec, issues)
    _check_cognee_importable(cognee_root, issues)

    for p in data_paths or []:
        pp = Path(p)
        if not pp.exists():
            issues.append(f"数据路径不存在: {p}")

    if require_api_key and not os.environ.get("LLM_API_KEY"):
        issues.append("缺少环境变量 LLM_API_KEY")

    return issues


def ensure_preflight_for_add(
    spec: dict,
    data_paths: list[str] | None = None,
    require_api_key: bool = True,
) -> None:
    issues = preflight_checks_for_add(spec, data_paths=data_paths, require_api_key=require_api_key)
    if issues:
        raise PreflightError("Preflight(add) 失败:\n- " + "\n- ".join(issues))
