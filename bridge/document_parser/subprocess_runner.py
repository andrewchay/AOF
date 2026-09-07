# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""子进程运行器：让主进程（AOF venv）调用隔离解释器执行 worker_entry。

阶段 0 PoC 确认各引擎依赖不能共享 venv（transformers 4.x vs 5.x 冲突），因此文档解析
统一走「主进程编排 + 隔离解释器执行」。本模块负责：

1. 定位隔离解释器路径（配置 / 环境变量 / 默认部署路径）。
2. 用 subprocess 调 ``worker_entry.py`` 解析文件，把 stdout 单行 JSON 解析回 ``ParsedDoc``。
3. 子进程失败时抛 ``EngineUnavailableError``，由 core 层降级。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from bridge.document_parser.engine_base import EngineUnavailableError, ParsedDoc

# worker_entry.py 的绝对路径（本文件同目录的 worker/ 下）
WORKER_ENTRY = Path(__file__).resolve().parent / "worker" / "worker_entry.py"

# 各引擎的隔离解释器默认候选（依次探测）
_DEFAULT_PYTHONS: dict[str, list[str]] = {
    "docling": [
        "/tmp/aof_poc_venv/bin/python",  # PoC venv (3.12 + docling)
        "{home}/.aof/poc-venv/bin/python",
    ],
    "mineru": [
        "/tmp/aof_mineru_venv/bin/python",  # PoC mineru venv (3.12)
        "{home}/.aof/mineru-venv/bin/python",
    ],
}


def _resolve_interpreter(engine: str) -> str | None:
    # 1) 显式环境变量 AOF_DOCLING_PYTHON / AOF_MINERU_PYTHON
    env_key = f"AOF_{engine.upper()}_PYTHON"
    env = os.environ.get(env_key)
    if env and Path(env).exists():
        return env
    # 2) 默认候选
    home = str(Path.home())
    for cand in _DEFAULT_PYTHONS.get(engine, []):
        resolved = cand.format(home=home)
        if Path(resolved).exists():
            return resolved
    # 3) PATH 中的 python（兜底，可能缺引擎库）
    return shutil.which("python3")


def run_engine_worker(
    engine: str,
    path: Path,
    *,
    lang: str = "zh",
    timeout: int = 300,
) -> ParsedDoc:
    """在隔离解释器执行 worker_entry，返回 ParsedDoc。

    Raises:
        EngineUnavailableError: 隔离解释器不可用或 worker_entry 缺失。
    """
    python_bin = _resolve_interpreter(engine)
    if not python_bin:
        raise EngineUnavailableError(
            f"引擎 {engine} 的隔离解释器不可用。请设置 AOF_{engine.upper()}_PYTHON 指向可用的 python。"
        )
    if not WORKER_ENTRY.exists():
        raise EngineUnavailableError(f"worker_entry 缺失: {WORKER_ENTRY}")

    cmd = [
        python_bin,
        str(WORKER_ENTRY),
        "--engine",
        engine,
        "--path",
        str(path.resolve()),  # 绝对路径，避免 worker cwd 差异
        "--lang",
        lang,
        "--timeout",
        str(timeout),
    ]

    env = os.environ.copy()
    # 确保 worker 能 import 隔离 venv 里的引擎库；追加 worker 目录到 PYTHONPATH 不必要（脚本自包含）
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 30,
            env=env,
            cwd=str(Path(__file__).resolve().parent),
        )
    except subprocess.TimeoutExpired as e:
        raise EngineUnavailableError(
            f"引擎 {engine} 子进程超时（>{timeout}s）: {path.name}"
        ) from e
    except OSError as e:
        raise EngineUnavailableError(f"无法启动引擎 {engine} 子进程: {e}") from e

    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "")[-500:]
        raise EngineUnavailableError(
            f"引擎 {engine} 解析失败 exit={proc.returncode}: {stderr_tail or 'no stderr'}"
        )

    payload = _parse_worker_json(proc.stdout, engine, path)
    return _to_parsed_doc(payload, engine, path)


def _parse_worker_json(stdout: str, engine: str, path: Path) -> dict[str, Any]:
    """解析 worker 的 stdout 单行 JSON。"""
    try:
        payload = json.loads(stdout.strip().splitlines()[-1])
    except json.JSONDecodeError as e:
        raise EngineUnavailableError(
            f"引擎 {engine} 输出非 JSON: {str(e)[:200]}; raw={stdout[:200]!r}"
        ) from e
    if not isinstance(payload, dict):
        raise EngineUnavailableError(f"引擎 {engine} 输出结构异常: {type(payload)}")
    if payload.get("ok") is False:
        raise EngineUnavailableError(f"引擎 {engine} 解析失败: {payload.get('error')}")
    return payload


def _to_parsed_doc(payload: dict, engine: str, source: Path) -> ParsedDoc:
    return ParsedDoc(
        content=payload.get("content") or "",
        source_path=payload.get("source_path") or str(source),
        mime_type=_guess_mime(source),
        pages=payload.get("pages"),
        tables_count=payload.get("tables_count"),
        languages=payload.get("languages") or [],
        engine=payload.get("engine") or engine,
        parsed_at=payload.get("parsed_at"),
        structured={"tables": payload.get("tables") or []},
    )


def _guess_mime(path: Path) -> str | None:
    import mimetypes

    mime, _ = mimetypes.guess_type(str(path))
    return mime
