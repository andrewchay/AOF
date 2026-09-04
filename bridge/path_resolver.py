#!/usr/bin/env python3
"""路径解析器 - 支持 AOF 的两仓分离（Runtime vs Knowledge Repo）.

AOF 明确区分：
- project_root: AOF 工具/runtime 的安装目录
- knowledge_repo: 用户知识库的存放目录（原始文档、Markdown 等）

本模块负责将相对路径解析为绝对路径，优先使用 knowledge_repo 作为基准.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_data_path(
    path: str | Path,
    spec: dict[str, Any],
) -> Path:
    """解析数据路径.

    规则：
    1. 如果路径已经是绝对路径，直接返回
    2. 如果 spec 中定义了 knowledge_repo，以其为基准解析
    3. 否则以 project_root 为基准解析
    4. 如果都没有，以当前工作目录解析

    Args:
        path: 原始路径（可能相对）
        spec: AOF 配置 spec

    Returns:
        绝对路径
    """
    p = Path(path)
    if p.is_absolute():
        return p.resolve()

    knowledge_repo = spec.get("knowledge_repo")
    if knowledge_repo:
        return (Path(knowledge_repo) / p).resolve()

    project_root = spec.get("project_root")
    if project_root:
        return (Path(project_root) / p).resolve()

    return p.resolve()


def resolve_result_path(
    path: str | Path,
    spec: dict[str, Any],
) -> Path:
    """解析结果/日志输出路径.

    规则：
    1. 绝对路径直接返回
    2. 相对路径以 project_root 为基准（结果应写入 runtime 目录，而非 knowledge_repo）
    """
    p = Path(path)
    if p.is_absolute():
        return p.resolve()

    project_root = spec.get("project_root")
    if project_root:
        return (Path(project_root) / p).resolve()

    return p.resolve()


def get_knowledge_repo(spec: dict[str, Any]) -> Path | None:
    """获取知识库根目录."""
    kr = spec.get("knowledge_repo")
    return Path(kr).resolve() if kr else None


def get_project_root(spec: dict[str, Any]) -> Path | None:
    """获取项目根目录."""
    pr = spec.get("project_root")
    return Path(pr).resolve() if pr else None
