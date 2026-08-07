#!/usr/bin/env python3
"""OKF Knowledge Service - REST 与 MCP 共享的 OKF 知识包消费逻辑.

将 OKF 知识包的读取/搜索/体检逻辑从 mcp_server.py 抽离为共享服务，
让 FastAPI REST 端点与 MCP Server 复用同一套知识消费实现，避免行为漂移。

能力:
- 知识包目录解析（bundle_dir 参数 -> AOF_OKF_DIR -> spec 配置 -> 默认）
- 读 index.md（渐进式披露目录）
- 读单个 Concept（含目录穿越防护）
- 按 type/title/tag/正文搜索 Concept
- 运行结构体检（Lint）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def default_bundle_dir(project_root: Path, spec: dict[str, Any] | None = None) -> Path:
    """解析默认知识包目录。优先级：AOF_OKF_DIR > spec.okf.dir > ./okf_bundle.

    Args:
        project_root: 项目根目录（用于相对路径解析与默认值）
        spec: 可选的 spec 配置 dict
    """
    env_dir = os.environ.get("AOF_OKF_DIR")
    if env_dir:
        p = Path(env_dir)
        return p if p.is_absolute() else (project_root / p)
    if spec:
        okf_dir = (spec.get("okf") or {}).get("dir")
        if okf_dir:
            p = Path(okf_dir)
            return p if p.is_absolute() else (project_root / p)
    return project_root / "okf_bundle"


def bundle_dir_from_args(
    args: dict[str, Any],
    project_root: Path,
    spec: dict[str, Any] | None = None,
) -> Path:
    """解析知识包目录：优先参数 bundle_dir，其次默认逻辑."""
    explicit = args.get("bundle_dir")
    if explicit:
        p = Path(explicit)
        return p if p.is_absolute() else (project_root / p)
    return default_bundle_dir(project_root, spec)


def resolve_concept_path(bundle: Path, path: str) -> Path | None:
    """安全解析 Concept 路径：拒绝目录穿越（含 ..）逃出 bundle.

    兼容 index/search 返回的去 .md 后缀路径（如 person/Alice）。
    返回 bundle 内已确认存在的文件，否则 None。
    """
    if not path:
        return None
    raw = Path(path)
    if raw.is_absolute():
        return None
    # 严格拒绝任何 .. 段或空段（路径穿越防护）
    if any(part in ("..", "") for part in raw.parts):
        return None
    candidate = bundle / raw
    if candidate.is_dir() or not candidate.suffix:
        candidate_md = bundle / Path(str(raw) + ".md")
        return candidate_md if _is_safe_file(candidate_md, bundle) else None
    return candidate if _is_safe_file(candidate, bundle) else None


def _is_safe_file(candidate: Path, bundle: Path) -> bool:
    """确认 candidate 是 bundle 内的 .md 文件（双保险防穿越）."""
    try:
        candidate.resolve().relative_to(bundle.resolve())
    except ValueError:
        return False
    return candidate.is_file() and candidate.suffix == ".md"


def read_index(bundle: Path) -> dict[str, Any]:
    """读取知识包 index.md（渐进式披露目录）."""
    index_file = bundle / "index.md"
    if not index_file.is_file():
        return {"bundle_dir": str(bundle), "exists": False, "error": f"知识包目录缺少 index.md: {bundle}"}
    return {
        "bundle_dir": str(bundle),
        "exists": True,
        "index_md": index_file.read_text(encoding="utf-8"),
    }


def get_concept(bundle: Path, path: str) -> dict[str, Any]:
    """按 path 读取单个 Concept 完整内容."""
    if not path:
        return {"error": "缺少 path 参数", "exists": False}
    target = resolve_concept_path(bundle, path)
    if not target:
        return {"error": f"Concept 不存在或路径非法: {path}", "path": path, "exists": False}
    rel = target.relative_to(bundle).as_posix()
    return {
        "bundle_dir": str(bundle),
        "path": rel,
        "exists": True,
        "concept": target.read_text(encoding="utf-8"),
    }


def search_concepts(
    bundle: Path,
    query: Optional[str] = None,
    node_type: Optional[str] = None,
    title: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    """按 type/title/tag/正文文本搜索知识包内 Concept."""
    from tools.knowledge_lint import _parse_frontmatter

    query = (query or "").strip().lower()
    node_type = (node_type or "").strip().lower()
    title = (title or "").strip().lower()
    tag = (tag or "").strip().lower()

    if not bundle.is_dir():
        return {"bundle_dir": str(bundle), "error": "知识包目录不存在", "count": 0, "results": []}

    matches: list[dict[str, Any]] = []
    for md in sorted(bundle.rglob("*.md")):
        if md.name in ("index.md", "log.md"):
            continue
        try:
            meta, body = _parse_frontmatter(md.read_text(encoding="utf-8"))
        except OSError:
            continue
        meta_type = str(meta.get("type", "")).lower()
        meta_title = str(meta.get("title", "")).lower()
        meta_tags = [str(t).lower() for t in meta.get("tags", [])] if isinstance(meta.get("tags", []), list) else []
        text_blob = " ".join([meta_title, meta_type, " ".join(meta_tags), body.lower()])

        if node_type and node_type not in meta_type:
            continue
        if title and title not in meta_title:
            continue
        if tag and not any(tag in t for t in meta_tags):
            continue
        if query and query not in text_blob:
            continue

        score = 0
        if node_type and node_type == meta_type:
            score += 16
        if title and meta_title.startswith(title):
            score += 8
        if query and query in meta_title:
            score += 4
        if tag and tag in meta_tags:
            score += 2

        rel = md.relative_to(bundle).as_posix()
        desc = str(meta.get("description", ""))[:200]
        matches.append({
            "path": rel,
            "title": meta.get("title", md.stem),
            "type": meta.get("type", ""),
            "tags": meta.get("tags", []),
            "description": desc,
            "score": score,
        })

    matches.sort(key=lambda m: (-m["score"], m["title"]))
    return {
        "bundle_dir": str(bundle),
        "count": len(matches[:limit]),
        "results": matches[:limit],
    }


def lint_bundle(bundle: Path) -> dict[str, Any]:
    """对知识包运行结构体检（断链/重复/口径冲突）."""
    from tools.knowledge_lint import OKFLinter

    return OKFLinter().lint(bundle).to_dict()


def list_bundles(root: Path) -> dict[str, Any]:
    """列出知识包根目录下的所有知识包（含 index.md 的子目录）."""
    root = root.resolve()
    if not root.is_dir():
        return {"root": str(root), "count": 0, "bundles": []}
    bundles = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "index.md").is_file():
            from tools.knowledge_lint import _parse_frontmatter
            meta, _ = _parse_frontmatter((child / "index.md").read_text(encoding="utf-8"))
            bundles.append({
                "name": child.name,
                "path": str(child),
                "title": meta.get("title", child.name),
                "concepts": sum(1 for _ in child.rglob("*.md")),
            })
    return {"root": str(root), "count": len(bundles), "bundles": bundles}
