#!/usr/bin/env python3
"""OKF Export Exporter - 将 AOF/Cognee 知识图谱导出为 OKF (Open Knowledge Format) 兼容的 LLM Wiki 知识包.

本模块受以下理念启发：
- Andrej Karpathy 的 LLM Wiki："先编译，后查询"，知识编译为相互链接的 Markdown 知识库，Agent 只在结构化知识内检索。
- Google OKF (Open Knowledge Format)：原子化 Concept + YAML frontmatter + index.md(渐进式披露) + log.md(审计) + 容错消费模型。

产出的是一个 Agent-Ready 的知识包：
- 原子化 Concept：每个实体/概念一个 Markdown 文件，按 type 分目录（MECE）。
- 标准 frontmatter：type/title/description/resource/tags/timestamp。
- 交叉链接：Concept 间关系用相对 Markdown 链接表达。
- index.md：渐进式披露总目录，Agent 先读全局再下钻，避免 context 爆炸。
- log.md：append-only 审计日志，记录知识包变更演进。

使用示例:
    exporter = OKFExporter()
    result = await exporter.export(
        dataset_id="my_dataset",
        output_dir="./okf_bundle/",
    )

    # 便捷函数
    result = await export_dataset_to_okf(
        dataset_id="my_dataset",
        output_dir="./okf_bundle/",
    )
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 检查可选的 MarkdownExporter 复用（惰性，避免循环导入）
_markdown_exporter = None


def _get_markdown_exporter(cognee_root: str | None = None):
    """惰性构造并缓存 MarkdownExporter，复用其图数据拉取与文件名清洗逻辑."""
    global _markdown_exporter
    if _markdown_exporter is None:
        from exporters.markdown_exporter import MarkdownExporter
        _markdown_exporter = MarkdownExporter(cognee_root=cognee_root)
    return _markdown_exporter


@dataclass
class OKFExportResult:
    """OKF 知识包导出结果统计."""
    output_dir: Path
    concepts_exported: int = 0
    files_created: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    mode: str = "unknown"  # "graph" | "dataset"
    index_created: bool = False
    log_created: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "concepts_exported": self.concepts_exported,
            "files_created": [str(p) for p in self.files_created],
            "errors": self.errors,
            "mode": self.mode,
            "index_created": self.index_created,
            "log_created": self.log_created,
        }


class OKFExporter:
    """AOF OKF / LLM Wiki 导出器.

    将 Cognee 知识图谱导出为 OKF 兼容知识包：
    - 原子化 Concept（按 type 分目录）
    - 标准 YAML frontmatter
    - 交叉链接 + index.md 渐进式披露 + log.md 审计
    - 容错消费模型（缺字段不退化为异常）
    """

    def __init__(self, cognee_root: str | None = None):
        self.cognee_root = cognee_root
        # 复用 MarkdownExporter 的图数据拉取与文件名清洗能力
        self._delegate = _get_markdown_exporter(cognee_root)

    # ---------- 取数（复用 MarkdownExporter） ----------

    async def _load_graph_data(
        self,
        dataset_id: Optional[str],
        dataset_name: Optional[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        """复用 MarkdownExporter 的图数据拉取逻辑."""
        if not self._delegate._cognee_available:
            return None
        try:
            return await self._delegate._try_get_graph_data(dataset_id, dataset_name)
        except Exception as e:  # noqa: BLE001 - 容错消费模型
            logger.warning("图数据拉取失败，回退: %s", e)
            return None

    async def _load_dataset_pages(
        self,
        dataset_id: Optional[str],
        dataset_name: Optional[str],
    ) -> list[dict[str, Any]]:
        """复用 MarkdownExporter 的 dataset 回退数据."""
        try:
            return await self._delegate._fallback_to_dataset_data(dataset_id, dataset_name)
        except Exception as e:  # noqa: BLE001
            logger.warning("dataset 回退数据拉取失败: %s", e)
            return []

    # ---------- OKF frontmatter ----------

    def _build_frontmatter(self, node: dict[str, Any]) -> str:
        """构建 OKF 标准 YAML frontmatter.

        必填: type; 高频推荐: title/description/resource/tags/timestamp.
        """
        lines = ["---"]

        node_type = str(node.get("type", "entity"))
        title = str(node.get("name", node.get("title", "Untitled")))
        node_id = str(node.get("id", ""))

        lines.append(f"type: {self._okf_quote(node_type)}")
        lines.append(f"title: {self._okf_quote(title)}")

        # description: 优先取已有描述字段，缺省则退化为 title（容错）
        desc = node.get("description", node.get("summary", node.get("text", "")))
        if isinstance(desc, str) and desc.strip():
            lines.append(f"description: {self._okf_quote(desc.strip())}")
        elif node.get("compiled_truth"):
            lines.append(f"description: {self._okf_quote(node['compiled_truth'])}")

        # resource: OKF 推荐，指向外部资产 URI，缺省用 aof_id
        if node_id:
            lines.append(f"resource: {self._okf_quote(f'aof://{node_id}')}")

        # tags
        tags: list[str] = []
        if "tags" in node and isinstance(node["tags"], list):
            tags.extend(str(t) for t in node["tags"])
        if node_type and node_type != "entity":
            tags.append(node_type)
        if node.get("label") and str(node.get("label")) != node_type:
            tags.append(str(node["label"]))
        if tags:
            lines.append(f"tags: {self._okf_list(list(dict.fromkeys(tags)))}")

        # timestamp (OKF 时间戳，用 UTC ISO 格式)
        ts = node.get("timestamp", node.get("updated_at", node.get("created_at")))
        if ts:
            lines.append(f"timestamp: {self._okf_quote(str(ts))}")
        else:
            lines.append(
                f"timestamp: {self._okf_quote(datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))}"
            )

        # 其余标量元数据（跳过已使用和大型字段）
        skip = {"id", "name", "title", "type", "label", "tags", "description", "summary",
                "text", "compiled_truth", "timeline", "raw", "content", "timestamp",
                "created_at", "updated_at", "resource"}
        for key, value in node.items():
            if key in skip:
                continue
            if value is None:
                continue
            if isinstance(value, (dict, list)) and len(str(value)) > 200:
                continue
            lines.append(f"{key}: {self._okf_quote(str(value))}")

        lines.append("---")
        return "\n".join(lines)

    @staticmethod
    def _okf_quote(value: str) -> str:
        """OKF frontmatter 值格式化：含特殊字符时用引号包裹."""
        if any(c in value for c in [":", "#", "\n", '"', "[", "]", "{", "}", "- ", "?"]):
            escaped = value.replace('"', '\\"')
            return f'"{escaped}"'
        return value

    @staticmethod
    def _okf_list(items: list[str]) -> str:
        return "[" + ", ".join(f'"{i}"' for i in items) + "]"

    # ---------- Concept 正文 ----------

    def _build_concept_body(
        self,
        node: dict[str, Any],
        edges: list[dict[str, Any]],
        node_index: dict[str, dict[str, Any]],
    ) -> str:
        """构建 Concept 正文，含交叉链接（复用 MarkdownExporter 的 compiled_truth 基础逻辑）."""
        body_parts: list[str] = []

        # 描述段
        desc = node.get("description", node.get("summary", node.get("text", "")))
        if isinstance(desc, str) and desc.strip():
            body_parts.append(desc.strip())
            body_parts.append("")

        # 关键属性表格
        props = []
        for key, value in node.items():
            if key in {"id", "name", "title", "type", "label", "tags", "description", "summary",
                       "text", "compiled_truth", "timeline", "raw", "content", "timestamp",
                       "created_at", "updated_at", "resource"}:
                continue
            if value is None or isinstance(value, (dict, list)):
                continue
            props.append((key, str(value)))
        if props:
            body_parts.append("## Properties")
            for k, v in props[:12]:
                body_parts.append(f"- **{k}**: {v}")
            body_parts.append("")

        # 交叉链接：关系（outgoing/incoming）→ 指向其他 Concept 的相对路径
        node_id = node.get("id", "")
        rel_links = self._build_relation_links(node_id, edges, node_index)

        if rel_links["out"]:
            body_parts.append("## Relations (out)")
            body_parts.extend(rel_links["out"])
            body_parts.append("")
        if rel_links["in"]:
            body_parts.append("## Relations (in)")
            body_parts.extend(rel_links["in"])
            body_parts.append("")

        return "\n".join(body_parts).strip()

    def _build_relation_links(
        self,
        node_id: str,
        edges: list[dict[str, Any]],
        node_index: dict[str, dict[str, Any]],
    ) -> dict[str, list[str]]:
        """把关系转成指向其他 Concept 的相对 Markdown 链接.

        node_index: {node_id: {"path": "type/file.md", "title": "..."}}
        """
        out, inn = [], []
        for edge in edges:
            rel = str(edge.get("relation", "related_to"))
            if edge.get("source") == node_id:
                target_id = str(edge.get("target", ""))
                link = self._relation_link(target_id, rel, node_index)
                if link:
                    out.append(f"- {rel}: {link}")
            elif edge.get("target") == node_id:
                source_id = str(edge.get("source", ""))
                link = self._relation_link(source_id, rel, node_index)
                if link:
                    inn.append(f"- {rel}: {link}")
        return {"out": out, "in": inn}

    def _relation_link(
        self,
        other_id: str,
        rel: str,
        node_index: dict[str, dict[str, Any]],
    ) -> str | None:
        """生成指向其他 Concept 的相对链接；找不到时退化为纯文本（容错）."""
        info = node_index.get(other_id)
        if not info:
            return None
        path = info.get("path", "")
        title = info.get("title", other_id)
        if not path:
            return None
        return f"[{title}]({path})"

    # ---------- 保留文件：index.md / log.md ----------

    def _build_index_md(
        self,
        by_type: dict[str, list[tuple[str, str, str]]],  # type -> [(path, title, description)]
        bundle_title: str,
    ) -> str:
        """构建 index.md 渐进式披露目录."""
        lines = ["---", "type: bundle_index", f"title: {self._okf_quote(bundle_title)}", "---"]
        lines.append("")
        lines.append(f"# {bundle_title}")
        lines.append("")
        lines.append("本目录为知识的渐进式披露入口。Agent 先通读本页获取全局索引，再按需打开具体 Concept。")
        lines.append("")

        for node_type, concepts in sorted(by_type.items()):
            lines.append(f"## {node_type}")
            lines.append("")
            for path, title, desc in concepts:
                bare = path.removesuffix(".md") if path.endswith(".md") else path
                line = f"- [{title}]({bare})"
                if desc:
                    line += f" — {desc}"
                lines.append(line)
            lines.append("")

        return "\n".join(lines).rstrip()

    def _build_log_md(
        self,
        action: str,
        concepts_count: int,
        trigger: str,
    ) -> str:
        """构建/追加 log.md 审计条目（append-only）."""
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        entry = (
            f"## {ts}\n\n"
            f"- **action**: {action}\n"
            f"- **concepts**: {concepts_count}\n"
            f"- **trigger**: {trigger}\n"
        )
        return entry

    # ---------- 主流程 ----------

    async def export(
        self,
        output_dir: str | Path,
        dataset_id: Optional[str] = None,
        dataset_name: Optional[str] = None,
        bundle_title: str | None = None,
        dry_run: bool = False,
    ) -> OKFExportResult:
        """导出 OKF 知识包.

        Args:
            output_dir: 输出目录（将成为知识包 root，含 index.md/log.md）
            dataset_id: 数据集 ID
            dataset_name: 数据集名称（与 dataset_id 二选一）
            bundle_title: 知识包标题（默认取 dataset_name 或 "AOF Knowledge Bundle"）
            dry_run: 只统计不写文件

        Returns:
            OKFExportResult
        """
        output_path = Path(output_dir).resolve()
        result = OKFExportResult(output_dir=output_path)

        if not dataset_id and not dataset_name:
            result.errors.append("必须提供 dataset_id 或 dataset_name")
            return result

        # 1. 图模式
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        graph_data = await self._load_graph_data(dataset_id, dataset_name)
        if graph_data:
            nodes, edges = graph_data
            result.mode = "graph"
        else:
            # 2. dataset 回退
            pages = await self._load_dataset_pages(dataset_id, dataset_name)
            nodes = pages
            result.mode = "dataset"

        if not nodes:
            result.errors.append("未获取到任何知识数据")
            return result

        # 构建 node_index（id -> path/title）用于交叉链接
        title = bundle_title or dataset_name or "AOF Knowledge Bundle"
        node_index: dict[str, dict[str, Any]] = {}
        for node in nodes:
            nid = str(node.get("id", node.get("name", "")))
            if not nid:
                continue
            node_type = self._safe_type_dir(node.get("type", "entity"))
            fname = self._safe_concept_filename(node.get("name", node.get("title", nid)))
            node_index[nid] = {
                "path": f"{node_type}/{fname.removesuffix('.md')}",
                "title": str(node.get("name", node.get("title", nid))),
            }

        # 按 type 分组（去重：同名同 type 合并）
        seen: set[str] = set()
        pages_to_write: list[tuple[Path, str]] = []
        concepts_by_type: dict[str, list[tuple[str, str, str]]] = {}

        for node in nodes:
            nid = str(node.get("id", node.get("name", "")))
            node_type = self._safe_type_dir(node.get("type", "entity"))
            fname = self._safe_concept_filename(node.get("name", node.get("title", nid or "untitled")))
            key = f"{node_type}/{fname}"
            if key in seen:
                continue
            seen.add(key)

            content = self._build_concept_md(node, edges, node_index)
            file_path = output_path / node_type / fname
            pages_to_write.append((file_path, content))

            title_str = str(node.get("name", node.get("title", nid or "untitled")))
            desc = str(node.get("description", node.get("summary", node.get("text", "")))).strip()
            concepts_by_type.setdefault(node_type, []).append(
                (f"{node_type}/{fname.removesuffix('.md')}", title_str, desc)
            )

        # 重建 index.md（此时 concepts_by_type 已填充）+ log.md
        index_content = self._build_index_md(concepts_by_type, title)
        pages_to_write = [
            (output_path / "log.md", self._build_log_md("bundle_init", len(seen), "export")),
            (output_path / "index.md", index_content),
            *pages_to_write,
        ]

        # 写入
        for file_path, content in pages_to_write:
            if dry_run:
                result.concepts_exported += 1
                continue
            try:
                self._write_with_conflict_resolution(file_path, content)
                if file_path.name == "index.md":
                    result.index_created = True
                elif file_path.name == "log.md":
                    result.log_created = True
                result.files_created.append(file_path)
            except Exception as e:  # noqa: BLE001
                result.errors.append(f"写入 {file_path} 失败: {e}")

        result.concepts_exported = len(seen)
        return result

    def _build_concept_md(
        self,
        node: dict[str, Any],
        edges: list[dict[str, Any]],
        node_index: dict[str, dict[str, Any]],
    ) -> str:
        """组装一个完整 Concept 文件内容（frontmatter + body）."""
        frontmatter = self._build_frontmatter(node)
        body = self._build_concept_body(node, edges, node_index)
        return f"{frontmatter}\n\n{body}\n"

    # ---------- 工具 ----------

    @staticmethod
    def _safe_type_dir(node_type: str) -> str:
        """安全 type 目录名."""
        safe = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", str(node_type)).strip("_")
        return safe.lower() or "entity"

    @staticmethod
    def _safe_concept_filename(title: str, max_len: int = 80) -> str:
        """安全 Concept 文件名（带 .md）."""
        safe = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff ]+", "_", str(title))
        safe = re.sub(r"_+", "_", safe).strip("_")
        if len(safe) > max_len:
            safe = safe[:max_len]
        return f"{safe or 'untitled'}.md"

    @staticmethod
    def _write_with_conflict_resolution(file_path: Path, content: str) -> None:
        """写入文件，自动处理路径冲突（追加数字后缀）."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        original = file_path
        counter = 1
        while file_path.exists():
            stem, suffix = original.stem, original.suffix
            file_path = original.parent / f"{stem}_{counter}{suffix}"
            counter += 1
        file_path.write_text(content, encoding="utf-8")


# 便捷函数
async def export_dataset_to_okf(
    output_dir: str | Path,
    dataset_id: Optional[str] = None,
    dataset_name: Optional[str] = None,
    bundle_title: str | None = None,
    dry_run: bool = False,
    cognee_root: Optional[str] = None,
) -> OKFExportResult:
    """便捷函数：导出数据集为 OKF / LLM Wiki 知识包.

    示例:
        result = await export_dataset_to_okf(
            dataset_id="my_dataset_uuid",
            output_dir="./okf_bundle/",
        )
    """
    exporter = OKFExporter(cognee_root=cognee_root)
    return await exporter.export(
        output_dir=output_dir,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        bundle_title=bundle_title,
        dry_run=dry_run,
    )
