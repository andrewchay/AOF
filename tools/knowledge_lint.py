#!/usr/bin/env python3
"""OKF Knowledge Lint - 对 OKF/LLM Wiki 知识包做结构体检.

对标 Karpathy LLM Wiki 的 Lint 环节：知识包是 Agent 消费的输入，结构健康至关重要。
本工具对导出的 OKF 知识包目录做三类体检：
- 断链检测：指向不存在文件的空链接。
- 重复检测：title(+type) 重复、内容 hash 完全重复的 Concept。
- 口径冲突检测：同一 type+title 下，同名属性取值不一致的启发式提示。

使用示例:
    linter = OKFLinter()
    report = linter.lint("./okf_bundle/")
    for issue in report.issues:
        print(issue)

命令行:
    python -m tools.knowledge_lint ./okf_bundle/
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LintIssue:
    """单个 Lint 问题."""
    severity: str  # "error" | "warning"
    category: str  # "broken_link" | "duplicate" | "consistency"
    message: str
    file: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "file": self.file,
        }


@dataclass
class LintReport:
    """Lint 体检报告."""
    root: Path
    issues: list[LintIssue] = field(default_factory=list)
    concepts_scanned: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "concepts_scanned": self.concepts_scanned,
            "error_count": sum(1 for i in self.issues if i.severity == "error"),
            "warning_count": sum(1 for i in self.issues if i.severity == "warning"),
            "issues": [i.to_dict() for i in self.issues],
        }


# frontmatter 解析 ------------------------------------------------

def _parse_frontmatter(content: str) -> tuple[dict[str, object], str]:
    """宽容解析 YAML frontmatter（容错消费模型：解析失败返回空 dict，不抛错）."""
    if not content.startswith("---\n"):
        return {}, content
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", content, re.DOTALL)
    if not m:
        return {}, content
    raw_meta, body = m.group(1), m.group(2)
    meta: dict[str, object] = {}
    for line in raw_meta.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        meta[key] = _parse_scalar(value)
    return meta, body


def _parse_scalar(value: str) -> object:
    """解析 frontmatter 标量 / 列表."""
    value = value.strip()
    # 括号包裹的引号/裸项列表，如 ["eng", "person"] 或 [a, b]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if inner == "":
            return []
        items = []
        for raw in inner.split(","):
            item = raw.strip()
            if len(item) >= 2 and item[0] == '"' and item[-1] == '"':
                item = item[1:-1]
            elif len(item) >= 2 and item[0] == "'" and item[-1] == "'":
                item = item[1:-1]
            items.append(item)
        return items
    # 引号包裹
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    # 布尔 / 数字
    if value in ("true", "false"):
        return value == "true"
    try:
        return int(value)
    except ValueError:
        pass
    return value


LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


class OKFLinter:
    """OKF 知识包结构体检器."""

    def lint(self, root: str | Path) -> LintReport:
        """对整个知识包目录执行体检."""
        root = Path(root).resolve()
        report = LintReport(root=root)
        if not root.is_dir():
            report.issues.append(LintIssue("error", "broken_link", f"知识包目录不存在: {root}", str(root)))
            return report

        md_files = sorted(root.rglob("*.md"))
        report.concepts_scanned = len(md_files)
        if not md_files:
            report.issues.append(LintIssue("warning", "duplicate", "知识包中没有 .md 文件"))
            return report

        self._check_broken_links(root, md_files, report)
        self._check_duplicates(root, md_files, report)
        self._check_consistency(root, md_files, report)
        return report

    # ---- 断链 ----

    def _check_broken_links(
        self,
        root: Path,
        md_files: list[Path],
        report: LintReport,
    ) -> None:
        # 用"去掉 .md 后缀的规范键"做集合，兼容 index 中不带 .md 的链接
        existing = set()
        for p in md_files:
            bare = p.relative_to(root).as_posix()
            existing.add(bare)
            if bare.endswith(".md"):
                existing.add(bare[:-3])
        for p in md_files:
            try:
                content = p.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            for _, target in LINK_PATTERN.findall(content):
                target = target.strip()
                if not target or target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                # OKF 知识包约定链接相对 root；兼容相对当前文件目录的写法。
                resolved = _resolve_okf_path("", target) or _resolve_okf_path(
                    Path(rel).parent.as_posix(), target
                )
                if resolved and resolved not in existing:
                    report.issues.append(
                        LintIssue("error", "broken_link",
                                  f"断链: `{target}` -> `{resolved}` 不存在", rel)
                    )

    # ---- 重复 ----

    def _check_duplicates(
        self,
        root: Path,
        md_files: list[Path],
        report: LintReport,
    ) -> None:
        title_seen: dict[tuple[str, str], Path] = {}
        hash_seen: dict[str, Path] = {}
        for p in md_files:
            if p.name in ("index.md", "log.md"):
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, _ = _parse_frontmatter(content)
            rel = p.relative_to(root).as_posix()
            key = (str(meta.get("type", "")), str(meta.get("title", "")))
            if key[1] and key in title_seen:
                report.issues.append(
                    LintIssue("warning", "duplicate",
                              f"重复 title+type: `{key[1]}` (type={key[0]}) 与 `{title_seen[key]}`", rel)
                )
            elif key[1]:
                title_seen[key] = p

            digest = hashlib.blake2b(content.encode("utf-8")).hexdigest()
            if digest in hash_seen:
                report.issues.append(
                    LintIssue("warning", "duplicate",
                              f"内容完全重复: `{rel}` 与 `{hash_seen[digest]}`", rel)
                )
            else:
                hash_seen[digest] = p

    # ---- 口径冲突 ----

    def _check_consistency(
        self,
        root: Path,
        md_files: list[Path],
        report: LintReport,
    ) -> None:
        """同一 type+title 下，同名属性取值不一致则提示."""
        groups: dict[tuple[str, str], list[tuple[Path, dict[str, object]]]] = {}
        for p in md_files:
            if p.name in ("index.md", "log.md"):
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, _ = _parse_frontmatter(content)
            key = (str(meta.get("type", "")), str(meta.get("title", "")))
            if not key[1]:
                continue
            groups.setdefault(key, []).append((p, meta))

        for key, items in groups.items():
            if len(items) < 2:
                continue
            # 找同名属性值冲突
            conflicts = _find_property_conflicts(items)
            for prop in conflicts:
                vals = ", ".join(f"{v[1]}" for v in conflicts[prop])
                report.issues.append(
                    LintIssue("warning", "consistency",
                              f"属性 `{prop}` 在 concept `{key[1]}` 下取值不一致: {vals}",
                              str(key))
                )


def _find_property_conflicts(
    items: list[tuple[Path, dict[str, object]]],
) -> dict[str, list[tuple[str, str]]]:
    """找出同一概念下同名属性取值不一致的字段."""
    prop_map: dict[str, list[str]] = {}
    for _, meta in items:
        for k, v in meta.items():
            if k in {"type", "title", "description", "tags", "timestamp", "resource", "aof_id"}:
                continue
            if v is None or isinstance(v, list):
                continue
            prop_map.setdefault(str(k), []).append(str(v))
    conflicts: dict[str, list[tuple[str, str]]] = {}
    for k, vals in prop_map.items():
        unique = set(vals)
        if len(unique) > 1:
            conflicts[k] = [(v, _first_file_for_value(items, k, v)) for v in unique]
    return conflicts


def _first_file_for_value(
    items: list[tuple[Path, dict[str, object]]],
    key: str,
    value: str,
) -> str:
    for p, meta in items:
        if str(meta.get(key)) == value:
            return p.name
    return ""


def _resolve_okf_path(from_dir: str, target: str) -> str | None:
    """解析 OKF 相对链接为相对知识包 root 的规范化 posix 路径（纯逻辑，不依赖磁盘）.

    - 以 `/` 开头 → 视为相对 root 的绝对路径。
    - 否则 → 相对当前 Concept 所在目录。
    """
    target = target.strip()
    # 兼容 wiki 风格 `[[alias|target]]` / `[[target]]`
    if target.startswith("[[") and target.endswith("]]"):
        inner = target[2:-2]
        target = inner.split("|")[-1].strip() if "|" in inner else inner
        target = target.lstrip("/")

    if not target:
        return None

    if target.startswith("/"):
        raw = target.lstrip("/")
    elif from_dir:
        raw = f"{from_dir}/{target}"
    else:
        raw = target

    parts = raw.split("/")
    stack: list[str] = []
    for part in parts:
        if not part or part == ".":
            continue
        if part == "..":
            if stack:
                stack.pop()
            continue
        stack.append(part)
    return "/".join(stack) if stack else None


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python -m tools.knowledge_lint <okf_bundle_dir>", file=sys.stderr)
        return 2
    root = sys.argv[1]
    report = OKFLinter().lint(root)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 1 if _any_error(report) else 0


def _any_error(report: LintReport) -> bool:
    return any(i.severity == "error" for i in report.issues)


if __name__ == "__main__":
    raise SystemExit(main())
