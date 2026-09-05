"""训练数据生成器 - 数据加载器.

提供从 GraphBackend 和 DatasetManager 加载数据的统一接口.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..storage.base import GraphBackend, Node, Edge, Triple

logger = logging.getLogger(__name__)


@dataclass
class GraphData:
    """从图谱加载的数据集合."""
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    triples: list[Triple] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentData:
    """从文档加载的数据集合."""
    items: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class GraphLoader:
    """图谱数据加载器.

    通过 GraphBackend 接口统一读取节点、边和三元组数据.
    """

    def __init__(self, backend: GraphBackend):
        self.backend = backend

    async def load_all(
        self,
        node_limit: Optional[int] = None,
        edge_limit: Optional[int] = None,
    ) -> GraphData:
        """加载完整的图谱数据.

        Args:
            node_limit: 最大加载节点数
            edge_limit: 最大加载边数

        Returns:
            GraphData 包含节点、边和统计信息
        """
        data = GraphData()

        try:
            # 加载节点
            nodes = await self.backend.get_nodes(limit=node_limit or 10000)
            data.nodes = nodes
            logger.info(f"Loaded {len(nodes)} nodes from graph")
        except Exception as e:
            logger.warning(f"Failed to load nodes: {e}")

        try:
            # 加载边
            edges = await self.backend.get_edges(limit=edge_limit or 50000)
            data.edges = edges
            logger.info(f"Loaded {len(edges)} edges from graph")
        except Exception as e:
            logger.warning(f"Failed to load edges: {e}")

        # 尝试构建三元组
        data.triples = self._build_triples(data.nodes, data.edges)

        # 获取统计信息
        try:
            stats = await self.backend.get_statistics()
            data.statistics = stats.to_dict() if hasattr(stats, "to_dict") else vars(stats)
        except Exception as e:
            logger.warning(f"Failed to get graph statistics: {e}")
            data.statistics = {
                "node_count": len(data.nodes),
                "edge_count": len(data.edges),
            }

        return data

    async def load_nodes_by_label(
        self,
        label: str,
        limit: int = 1000,
    ) -> list[Node]:
        """按标签加载节点."""
        try:
            # 尝试使用 Cypher 查询
            query = f"MATCH (n:{label}) RETURN n LIMIT {limit}"
            results = await self.backend.execute_cypher(query)
            nodes = []
            for record in results:
                node_data = record.get("n", record)
                if isinstance(node_data, dict):
                    node_id = node_data.get("id", "")
                    properties = {k: v for k, v in node_data.items() if k != "id"}
                    nodes.append(Node(id=node_id, labels=[label], properties=properties))
            return nodes
        except Exception as e:
            logger.warning(f"Failed to load nodes by label '{label}': {e}")
            return []

    async def load_neighbors(
        self,
        node_id: str,
        depth: int = 1,
        limit: int = 50,
    ) -> tuple[list[Node], list[Edge]]:
        """加载节点的邻居子图.

        Returns:
            (neighbor_nodes, connecting_edges)
        """
        try:
            return await self.backend.get_neighbors(node_id, depth=depth, limit=limit)
        except Exception as e:
            logger.warning(f"Failed to load neighbors for {node_id}: {e}")
            return [], []

    async def load_paths(
        self,
        source_id: str,
        target_id: str,
        max_length: int = 4,
    ) -> list[Any]:
        """加载两点之间的路径."""
        try:
            return await self.backend.find_paths(source_id, target_id, max_length=max_length)
        except Exception as e:
            logger.warning(f"Failed to load paths from {source_id} to {target_id}: {e}")
            return []

    @staticmethod
    def _build_triples(nodes: list[Node], edges: list[Edge]) -> list[Triple]:
        """从节点和边构建三元组."""
        node_map = {n.id: n for n in nodes}
        triples = []
        for edge in edges:
            source = node_map.get(edge.source_id)
            target = node_map.get(edge.target_id)
            if source and target:
                triples.append(Triple(
                    subject=source,
                    predicate=edge.relation_type,
                    object=target,
                    edge_properties=edge.properties,
                ))
        return triples


class DocumentLoader:
    """文档数据加载器.

    通过 DatasetManager 加载原始文档内容和元数据.
    当 DatasetManager 不可用时，降级为直接读取本地文件.
    """

    def __init__(self, dataset_manager: Optional[Any] = None):
        self.dataset_manager = dataset_manager

    async def load(
        self,
        dataset_name: str,
        max_items: Optional[int] = None,
    ) -> DocumentData:
        """加载文档数据.

        Args:
            dataset_name: 数据集名称
            max_items: 最大加载文档数

        Returns:
            DocumentData 包含文档项和文本 chunks
        """
        data = DocumentData()

        if self.dataset_manager is None:
            logger.warning("DatasetManager not available, returning empty document data")
            return data

        try:
            items = await self.dataset_manager.list_dataset_data(dataset_name)
            if max_items:
                items = items[:max_items]
            data.items = items
            logger.info(f"Loaded {len(items)} document items from dataset '{dataset_name}'")

            # 提取文本 chunks
            data.chunks = self._extract_chunks(items)
        except Exception as e:
            logger.warning(f"Failed to load documents for '{dataset_name}': {e}")

        return data

    @staticmethod
    def _extract_chunks(items: list[dict[str, Any]]) -> list[str]:
        """从文档项中提取文本 chunks."""
        chunks = []
        for item in items:
            # 尝试多种字段名
            text = (
                item.get("text")
                or item.get("content")
                or item.get("chunk")
                or item.get("body")
                or ""
            )
            if text and isinstance(text, str) and len(text.strip()) > 10:
                chunks.append(text.strip())
            # 也尝试从 metadata 中提取
            metadata = item.get("metadata") or {}
            if isinstance(metadata, dict):
                meta_text = metadata.get("summary") or metadata.get("description") or ""
                if meta_text and len(meta_text.strip()) > 10:
                    chunks.append(meta_text.strip())
        return chunks
