#!/usr/bin/env python3
"""Markdown 导出器 - 将 AOF/Cognee 知识图谱导出为人类可读的 Markdown 页面.

本模块受 GBrain 的 compiled_truth + timeline 模式启发，让 AOF 的知识库
不再只是黑盒图数据库，而是可以导出为可阅读、可编辑、可版本控制的 Markdown.

使用示例:
    exporter = MarkdownExporter()
    result = await exporter.export(
        dataset_id="my_dataset",
        output_dir="./brain_mirror/",
    )

    # 便捷函数
    result = await export_dataset_to_markdown(
        dataset_id="my_dataset",
        output_dir="./brain_mirror/",
    )
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import importlib.util
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class ExportResult:
    """导出结果统计."""
    output_dir: Path
    pages_exported: int = 0
    pages_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    files_created: list[Path] = field(default_factory=list)
    mode: str = "unknown"  # "graph" | "dataset" | "mixed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "pages_exported": self.pages_exported,
            "pages_skipped": self.pages_skipped,
            "errors": self.errors,
            "files_created": [str(p) for p in self.files_created],
            "mode": self.mode,
        }


class MarkdownExporter:
    """AOF Markdown 导出器.

    将 Cognee 知识图谱导出为 GBrain 风格的 Markdown 页面：
    - Frontmatter: 元数据（类型、标签、来源等）
    - Compiled Truth: 当前最佳理解的汇总
    - Timeline: 追加-only 的证据/事件轨迹
    """

    def __init__(self, cognee_root: str | None = None):
        self.cognee_root = cognee_root
        self._cognee_available = self._check_cognee()

    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用."""
        return importlib.util.find_spec("cognee") is not None

    def _import_cognee(self):
        """导入 cognee 模块."""
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        if self.cognee_root:
            root = str(Path(self.cognee_root).resolve())
            if root not in sys.path:
                sys.path.insert(0, root)
        import cognee  # type: ignore
        return cognee

    @staticmethod
    def _slugify(text: str) -> str:
        """将文本转换为安全文件名/目录名."""
        text = text.strip().lower()
        text = re.sub(r"[^a-z0-9_\-\/\. ]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        return text or "untitled"

    @staticmethod
    def _sanitize_filename(text: str, max_len: int = 80) -> str:
        """生成安全的 Markdown 文件名."""
        safe = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff ]+", "_", text.strip())
        safe = re.sub(r"_+", "_", safe).strip("_")
        if len(safe) > max_len:
            safe = safe[:max_len]
        safe = safe or "untitled"
        return f"{safe}.md"

    @staticmethod
    def _escape_frontmatter(value: Any) -> str:
        """安全地格式化 frontmatter 值."""
        if value is None:
            return ""
        if isinstance(value, str):
            # 如果包含特殊字符，用引号包裹
            if any(c in value for c in [":", "#", "\n", '"']):
                escaped = value.replace('"', '\\"')
                return f'"{escaped}"'
            return value
        if isinstance(value, (list, tuple)):
            items = [MarkdownExporter._escape_frontmatter(v) for v in value]
            return f"[{', '.join(items)}]"
        return str(value)

    async def _try_get_graph_data(
        self,
        dataset_id: Optional[str] = None,
        dataset_name: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        """尝试从 Cognee 获取图数据（nodes + edges）.

        由于 Cognee API 可能变化，这里尝试多种方式获取数据.
        如果全部失败，返回 None 以便调用方回退到 dataset 模式.
        """
        cognee = self._import_cognee()

        # 方式 1: 尝试通过底层 graph engine 获取（部分 Cognee 版本支持）
        try:
            nodes = await self._get_nodes_from_cognee(cognee, dataset_id, dataset_name)
            edges = await self._get_edges_from_cognee(cognee, dataset_id, dataset_name)
            if nodes:
                return nodes, edges
        except Exception:
            pass

        # 方式 2: 尝试使用 search API 获取图完成结果并重构
        try:
            nodes, edges = await self._get_graph_via_search(cognee, dataset_id, dataset_name)
            if nodes:
                return nodes, edges
        except Exception:
            pass

        return None

    async def _get_nodes_from_cognee(
        self,
        cognee,
        dataset_id: Optional[str],
        dataset_name: Optional[str],
    ) -> list[dict[str, Any]]:
        """尝试从 Cognee 获取节点列表."""
        nodes: list[dict[str, Any]] = []

        # 尝试使用 Cognee 内部 API 获取图数据
        try:
            from cognee.infrastructure.databases.graph import get_graph_engine  # type: ignore
            graph_engine = await get_graph_engine()

            # 优先检查 Kuzu/通用 Cypher 查询
            if hasattr(graph_engine, "query"):
                try:
                    result = await graph_engine.query("MATCH (n:Node) RETURN n.id, n.name, n.type, n.properties")
                    for record in result:
                        if len(record) == 4:
                            node_id, name, ntype, props_str = record
                            props = {}
                            if props_str:
                                try:
                                    import json
                                    props = json.loads(props_str)
                                except Exception:
                                    pass
                            nodes.append(self._normalize_node({
                                "id": node_id,
                                "name": name,
                                "type": ntype,
                                "properties": props,
                                **props
                            }))
                        else:
                            node = record[0] if isinstance(record, (list, tuple)) else record
                            nodes.append(self._normalize_node(node))
                except Exception:
                    pass

            # 如果没有成功获取节点，尝试原本的方法
            if not nodes:
                if hasattr(graph_engine, "get_nodes"):
                    try:
                        raw_nodes = await graph_engine.get_nodes()
                        for node in raw_nodes:
                            nodes.append(self._normalize_node(node))
                    except Exception:
                        pass
                elif hasattr(graph_engine, "list_nodes"):
                    raw_nodes = await graph_engine.list_nodes()
                    for node in raw_nodes:
                        nodes.append(self._normalize_node(node))
                else:
                    # 尝试 Cypher / 原生查询
                    try:
                        result = await graph_engine.query("MATCH (n) RETURN n LIMIT 10000")
                        for record in result:
                            node = record.get("n", record)
                            nodes.append(self._normalize_node(node))
                    except Exception:
                        pass
        except Exception:
            pass

        return nodes

    async def _get_edges_from_cognee(
        self,
        cognee,
        dataset_id: Optional[str],
        dataset_name: Optional[str],
    ) -> list[dict[str, Any]]:
        """尝试从 Cognee 获取边列表."""
        edges: list[dict[str, Any]] = []

        try:
            from cognee.infrastructure.databases.graph import get_graph_engine  # type: ignore
            graph_engine = await get_graph_engine()

            # 优先使用 Kuzu/通用 Cypher 查询
            if hasattr(graph_engine, "query"):
                try:
                    result = await graph_engine.query("MATCH (a:Node)-[r:EDGE]->(b:Node) RETURN a.id, b.id, r.relationship_name, r.properties")
                    for record in result:
                        if len(record) == 4:
                            source_id, target_id, rel_name, props_str = record
                            props = {}
                            if props_str:
                                try:
                                    import json
                                    props = json.loads(props_str)
                                except Exception:
                                    pass
                            edges.append(self._normalize_edge({
                                "source": source_id,
                                "target": target_id,
                                "relation": rel_name,
                                "properties": props,
                                **props
                            }))
                except Exception:
                    pass

            # 回落到原有的 get_edges
            if not edges:
                if hasattr(graph_engine, "get_edges"):
                    try:
                        raw_edges = await graph_engine.get_edges()
                        for edge in raw_edges:
                            edges.append(self._normalize_edge(edge))
                    except Exception:
                        pass
                elif hasattr(graph_engine, "list_edges"):
                    raw_edges = await graph_engine.list_edges()
                    for edge in raw_edges:
                        edges.append(self._normalize_edge(edge))
                else:
                    try:
                        result = await graph_engine.query(
                            "MATCH (a)-[r]->(b) RETURN a, r, b LIMIT 10000"
                        )
                        for record in result:
                            edge = record.get("r", {})
                            edge["source"] = self._extract_node_id(record.get("a"))
                            edge["target"] = self._extract_node_id(record.get("b"))
                            edges.append(self._normalize_edge(edge))
                    except Exception:
                        pass
        except Exception:
            pass

        return edges

    async def _get_graph_via_search(
        self,
        cognee,
        dataset_id: Optional[str],
        dataset_name: Optional[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """通过 search API 获取图数据并重构 nodes/edges."""
        from cognee.modules.search.types import SearchType  # type: ignore

        datasets = []
        if dataset_id:
            datasets.append(dataset_id)
        elif dataset_name:
            datasets.append(dataset_name)

        nodes_map: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []

        try:
            # 获取摘要信息
            summaries = await cognee.search(
                query_type=SearchType.SUMMARIES,
                query_text="*",
                top_k=1000,
                datasets=datasets or None,
            )
            for item in summaries if isinstance(summaries, list) else [summaries]:
                node = self._normalize_node(item)
                nid = node.get("id") or node.get("name") or str(hash(str(item)))
                nodes_map[nid] = node
        except Exception:
            pass

        try:
            # 获取图完成结果（通常包含关系信息）
            graph_results = await cognee.search(
                query_type=SearchType.GRAPH_COMPLETION,
                query_text="list all entities and relationships",
                top_k=1000,
                datasets=datasets or None,
            )
            for item in graph_results if isinstance(graph_results, list) else [graph_results]:
                # 尝试解析三元组
                triplet = self._extract_triplet(item)
                if triplet:
                    source, relation, target = triplet
                    sid = source.get("id") or source.get("name")
                    tid = target.get("id") or target.get("name")
                    if sid:
                        nodes_map[sid] = self._normalize_node(source)
                    if tid:
                        nodes_map[tid] = self._normalize_node(target)
                    if sid and tid and relation:
                        edges.append({
                            "source": sid,
                            "target": tid,
                            "relation": relation,
                        })
                else:
                    node = self._normalize_node(item)
                    nid = node.get("id") or node.get("name")
                    if nid:
                        nodes_map[nid] = node
        except Exception:
            pass

        return list(nodes_map.values()), edges

    def _normalize_node(self, raw: Any) -> dict[str, Any]:
        """将各种格式的原始节点统一为字典."""
        if isinstance(raw, dict):
            node = dict(raw)
        else:
            # 尝试提取属性
            node = {"raw": str(raw)}
            if hasattr(raw, "id"):
                node["id"] = str(raw.id)
            if hasattr(raw, "name"):
                node["name"] = str(raw.name)
            if hasattr(raw, "label"):
                node["label"] = str(raw.label)
            if hasattr(raw, "properties"):
                props = raw.properties
                if isinstance(props, dict):
                    node.update(props)
                else:
                    try:
                        node.update(dict(props))
                    except Exception:
                        pass

        # 统一关键字段
        node.setdefault("id", node.get("node_id", node.get("name", str(hash(str(raw))))))
        node.setdefault("name", node.get("label", node.get("title", node.get("id"))))
        node.setdefault("type", node.get("label", node.get("node_type", "entity")))
        return node

    def _normalize_edge(self, raw: Any) -> dict[str, Any]:
        """将各种格式的原始边统一为字典."""
        if isinstance(raw, dict):
            edge = dict(raw)
        else:
            edge = {"raw": str(raw)}
            if hasattr(raw, "source"):
                edge["source"] = self._extract_node_id(raw.source)
            if hasattr(raw, "target"):
                edge["target"] = self._extract_node_id(raw.target)
            if hasattr(raw, "type"):
                edge["relation"] = str(raw.type)
            if hasattr(raw, "relation"):
                edge["relation"] = str(raw.relation)
            if hasattr(raw, "properties"):
                props = raw.properties
                if isinstance(props, dict):
                    edge.update(props)

        edge.setdefault("relation", edge.get("type", edge.get("label", "related_to")))
        return edge

    def _extract_node_id(self, raw: Any) -> str:
        """从节点对象中提取 ID."""
        if isinstance(raw, dict):
            return str(raw.get("id", raw.get("name", json.dumps(raw, ensure_ascii=False))))
        if hasattr(raw, "id"):
            return str(raw.id)
        if hasattr(raw, "name"):
            return str(raw.name)
        return str(raw)

    def _extract_triplet(self, raw: Any) -> Optional[tuple[dict, str, dict]]:
        """从搜索结果中提取 (source, relation, target) 三元组."""
        if not isinstance(raw, dict):
            return None
        text = raw.get("text", raw.get("content", raw.get("result", "")))
        if not isinstance(text, str):
            return None

        # 简单的三元组解析: "A -> relation -> B" 或 "A -[relation]-> B"
        patterns = [
            r"(.+?)\s*[-–—]+\s*\[?(.+?)\]?\s*[-–—]+\s*>",
            r"(.+?)\s+->\s+(.+?)\s+->\s+(.+)",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                parts = m.groups()
                if len(parts) == 2:
                    return ({"name": parts[0].strip()}, parts[1].strip(), {"name": parts[1].strip()})
                if len(parts) == 3:
                    return ({"name": parts[0].strip()}, parts[1].strip(), {"name": parts[2].strip()})
        return None

    async def _fallback_to_dataset_data(
        self,
        dataset_id: str | None,
        dataset_name: str | None,
    ) -> list[dict[str, Any]]:
        """回退模式：从 dataset 原始数据生成 Markdown 页面."""
        from bridge.dataset_manager import DatasetManager

        manager = DatasetManager()

        # 如果未指定 dataset，尝试列出所有数据集
        if not dataset_id and not dataset_name:
            datasets = await manager.list_datasets()
            if not datasets:
                return []
            dataset_id = datasets[0].id

        # 解析 target_id：如果是 name，先查找对应的 UUID
        target_id = dataset_id or dataset_name or ""
        import uuid
        try:
            uuid.UUID(target_id)
        except ValueError:
            # target_id 可能是 dataset_name，需要查找对应 id
            datasets = await manager.list_datasets()
            found = next((d for d in datasets if d.name == target_id), None)
            if found:
                target_id = found.id
            else:
                return []

        # 获取数据项列表
        try:
            data_items = await manager.list_dataset_data(target_id)
        except Exception:
            return []

        pages = []
        for item in data_items:
            page = {
                "id": str(item.id),
                "name": item.name or str(item.id),
                "type": "source",
                "mime_type": item.mime_type or "unknown",
                "created_at": str(item.created_at) if item.created_at else None,
                "raw_data_location": item.raw_data_location,
                "compiled_truth": f"原始数据文件：{item.name}",
                "timeline": [{
                    "date": str(item.created_at) if item.created_at else datetime.now(timezone.utc).isoformat(),
                    "event": f"数据项被摄取到数据集 {target_id}",
                }],
            }
            pages.append(page)

        return pages

    def _build_frontmatter(self, node: dict[str, Any]) -> str:
        """构建 YAML frontmatter."""
        lines = ["---"]

        # 核心字段
        node_type = node.get("type", "entity")
        title = node.get("name", node.get("title", "Untitled"))
        node_id = node.get("id", "")

        lines.append(f'type: {self._escape_frontmatter(node_type)}')
        lines.append(f'title: {self._escape_frontmatter(title)}')
        lines.append(f'aof_id: {self._escape_frontmatter(node_id)}')

        # 标签
        tags = []
        if "label" in node and node["label"] != node_type:
            tags.append(str(node["label"]))
        if "tags" in node and isinstance(node["tags"], list):
            tags.extend(str(t) for t in node["tags"])
        if tags:
            lines.append(f'tags: {self._escape_frontmatter(list(set(tags)))}')

        # 其他元数据（过滤掉已使用的和大型字段）
        skip_keys = {"id", "name", "title", "type", "label", "tags",
                     "compiled_truth", "timeline", "raw", "text", "content"}
        for key, value in node.items():
            if key in skip_keys:
                continue
            if isinstance(value, (dict, list)) and len(str(value)) > 200:
                continue
            if value is None:
                continue
            lines.append(f'{key}: {self._escape_frontmatter(value)}')

        lines.append("---")
        return "\n".join(lines)

    def _build_compiled_truth(
        self,
        node: dict[str, Any],
        edges: list[dict[str, Any]],
    ) -> str:
        """构建 compiled truth 段落."""
        parts = []

        # 描述
        desc = node.get("description", node.get("summary", node.get("text", "")))
        if desc and isinstance(desc, str) and len(desc) < 2000:
            parts.append(desc.strip())
            parts.append("")

        # 核心属性表格（可选）
        props = []
        for key, value in node.items():
            if key in {"id", "name", "title", "type", "label", "tags",
                       "compiled_truth", "timeline", "raw", "description", "summary", "text"}:
                continue
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                continue
            props.append((key, str(value)))

        if props:
            parts.append("**关键属性：**")
            for k, v in props[:10]:
                parts.append(f"- {k}: {v}")
            parts.append("")

        # 相关实体（出边 + 入边）
        node_id = node.get("id", "")
        related_out = []
        related_in = []
        for edge in edges:
            if edge.get("source") == node_id:
                related_out.append((edge.get("relation", "related_to"), edge.get("target", "")))
            if edge.get("target") == node_id:
                related_in.append((edge.get("source", ""), edge.get("relation", "related_to")))

        if related_out:
            parts.append("** outgoing 关系：**")
            for rel, target in related_out[:15]:
                parts.append(f"- {rel} → {target}")
            parts.append("")

        if related_in:
            parts.append("** incoming 关系：**")
            for source, rel in related_in[:15]:
                parts.append(f"- {source} → {rel}")
            parts.append("")

        return "\n".join(parts).strip()

    def _build_timeline(self, node: dict[str, Any]) -> str:
        """构建 timeline 段落."""
        events = []

        # 如果节点自带 timeline
        if "timeline" in node and isinstance(node["timeline"], list):
            for ev in node["timeline"]:
                if isinstance(ev, dict):
                    date = ev.get("date", ev.get("timestamp", "未知时间"))
                    text = ev.get("event", ev.get("text", ev.get("summary", "")))
                    events.append((str(date), str(text)))
                elif isinstance(ev, str):
                    events.append(("", ev))

        # 从属性中提取时间线索
        time_fields = ["created_at", "updated_at", "ingested_at", "timestamp", "date"]
        for field in time_fields:
            if field in node and node[field]:
                events.append((str(node[field]), f"记录 {field}"))

        if not events:
            return ""

        # 去重并排序
        seen = set()
        unique_events = []
        for date, text in events:
            key = f"{date}|{text}"
            if key not in seen and text:
                seen.add(key)
                unique_events.append((date, text))

        # 按日期排序（简单字符串排序）
        unique_events.sort(key=lambda x: x[0])

        lines = []
        for date, text in unique_events:
            prefix = f"- {date}: " if date else "- "
            lines.append(f"{prefix}{text}")

        return "\n".join(lines)

    def _node_to_markdown(
        self,
        node: dict[str, Any],
        edges: list[dict[str, Any]],
    ) -> str:
        """将单个节点转换为完整的 Markdown 内容."""
        frontmatter = self._build_frontmatter(node)
        compiled_truth = self._build_compiled_truth(node, edges)
        timeline = self._build_timeline(node)

        parts = [frontmatter, "", compiled_truth]

        if timeline:
            parts.extend(["", "---", "", timeline])

        return "\n".join(parts) + "\n"

    async def export(
        self,
        output_dir: str | Path,
        dataset_id: Optional[str] = None,
        dataset_name: Optional[str] = None,
        dry_run: bool = False,
    ) -> ExportResult:
        """执行导出.

        Args:
            output_dir: 输出目录
            dataset_id: 数据集 ID
            dataset_name: 数据集名称（与 dataset_id 二选一）
            dry_run: 是否只预览不写入文件

        Returns:
            ExportResult 导出统计
        """
        output_path = Path(output_dir).resolve()
        result = ExportResult(output_dir=output_path)

        if not dataset_id and not dataset_name:
            result.errors.append("必须提供 dataset_id 或 dataset_name")
            return result

        # 1. 尝试图模式
        graph_data = None
        if self._cognee_available:
            try:
                graph_data = await self._try_get_graph_data(dataset_id, dataset_name)
            except Exception as e:
                result.errors.append(f"图模式获取失败: {e}")

        pages_to_write: list[tuple[Path, str]] = []

        if graph_data:
            nodes, edges = graph_data
            result.mode = "graph"

            # 按类型分组
            for node in nodes:
                node_type = self._slugify(node.get("type", "entity"))
                dir_path = output_path / node_type
                filename = self._sanitize_filename(node.get("name", node.get("id", "untitled")))
                file_path = dir_path / filename

                content = self._node_to_markdown(node, edges)
                pages_to_write.append((file_path, content))
        else:
            # 2. 回退到 dataset 原始数据模式
            result.mode = "dataset"
            try:
                pages = await self._fallback_to_dataset_data(dataset_id, dataset_name)
                for page in pages:
                    node_type = self._slugify(page.get("type", "source"))
                    dir_path = output_path / node_type
                    filename = self._sanitize_filename(page.get("name", page.get("id", "untitled")))
                    file_path = dir_path / filename

                    # 构造临时 node 和空 edges
                    node = {k: v for k, v in page.items()}
                    content = self._node_to_markdown(node, [])
                    pages_to_write.append((file_path, content))
            except Exception as e:
                result.errors.append(f"Dataset 回退模式失败: {e}")

        # 写入文件
        for file_path, content in pages_to_write:
            if not dry_run:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    # 处理文件名冲突
                    counter = 1
                    original_path = file_path
                    while file_path.exists():
                        stem = original_path.stem
                        suffix = original_path.suffix
                        file_path = original_path.parent / f"{stem}_{counter}{suffix}"
                        counter += 1

                    file_path.write_text(content, encoding="utf-8")
                    result.files_created.append(file_path)
                    result.pages_exported += 1
                except Exception as e:
                    result.errors.append(f"写入 {file_path} 失败: {e}")
                    result.pages_skipped += 1
            else:
                result.pages_exported += 1

        return result


# 便捷函数
async def export_dataset_to_markdown(
    output_dir: str | Path,
    dataset_id: Optional[str] = None,
    dataset_name: Optional[str] = None,
    dry_run: bool = False,
    cognee_root: Optional[str] = None,
) -> ExportResult:
    """便捷函数：导出数据集为 Markdown.

    示例:
        result = await export_dataset_to_markdown(
            dataset_id="my_dataset_uuid",
            output_dir="./brain_mirror/",
        )
        print(f"导出完成: {result.pages_exported} 页")
    """
    exporter = MarkdownExporter(cognee_root=cognee_root)
    return await exporter.export(
        output_dir=output_dir,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        dry_run=dry_run,
    )
