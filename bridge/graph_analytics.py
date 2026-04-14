#!/usr/bin/env python3
"""图谱分析模块 - 提供知识图谱的指标计算和分析。

本模块提供：
- PageRank 节点重要性
- 社区检测（Louvain, Label Propagation）
- 中心性分析（度中心性、介数中心性、接近中心性）
- 图谱统计（节点/边数、密度、直径等）
- 路径分析（最短路径、连通性）
- 可视化数据生成

使用示例:
    # 分析数据集
    analyzer = GraphAnalytics(dataset_name="my_docs")
    
    # 计算所有指标
    metrics = await analyzer.compute_all_metrics()
    
    # PageRank
    pagerank = await analyzer.pagerank(top_k=20)
    
    # 社区检测
    communities = await analyzer.detect_communities(algorithm="louvain")
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class CommunityAlgorithm(str, Enum):
    """社区检测算法。"""
    LOUVAIN = "louvain"
    LABEL_PROPAGATION = "label_propagation"
    GREEDY_MODULARITY = "greedy_modularity"


class CentralityType(str, Enum):
    """中心性类型。"""
    DEGREE = "degree"           # 度中心性
    BETWEENNESS = "betweenness"  # 介数中心性
    CLOSENESS = "closeness"      # 接近中心性
    EIGENVECTOR = "eigenvector"  # 特征向量中心性
    PAGERANK = "pagerank"        # PageRank


@dataclass
class NodeMetric:
    """节点指标。"""
    node_id: str
    node_type: Optional[str] = None
    label: Optional[str] = None
    pagerank: float = 0.0
    degree: int = 0
    in_degree: int = 0
    out_degree: int = 0
    betweenness: float = 0.0
    closeness: float = 0.0
    community: Optional[int] = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "label": self.label,
            "pagerank": round(self.pagerank, 6),
            "degree": self.degree,
            "in_degree": self.in_degree,
            "out_degree": self.out_degree,
            "betweenness": round(self.betweenness, 6) if self.betweenness else 0,
            "closeness": round(self.closeness, 6) if self.closeness else 0,
            "community": self.community,
        }


@dataclass
class GraphStatistics:
    """图谱统计信息。"""
    node_count: int = 0
    edge_count: int = 0
    density: float = 0.0
    avg_degree: float = 0.0
    max_degree: int = 0
    min_degree: int = 0
    connected_components: int = 0
    diameter: Optional[int] = None
    avg_path_length: Optional[float] = None
    clustering_coefficient: Optional[float] = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "density": round(self.density, 6),
            "avg_degree": round(self.avg_degree, 2),
            "max_degree": self.max_degree,
            "min_degree": self.min_degree,
            "connected_components": self.connected_components,
            "diameter": self.diameter,
            "avg_path_length": round(self.avg_path_length, 2) if self.avg_path_length else None,
            "clustering_coefficient": round(self.clustering_coefficient, 6) if self.clustering_coefficient else None,
        }


@dataclass
class Community:
    """社区信息。"""
    community_id: int
    node_count: int
    nodes: list[str]
    density: float = 0.0
    avg_pagerank: float = 0.0
    top_nodes: list[NodeMetric] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "community_id": self.community_id,
            "node_count": self.node_count,
            "nodes": self.nodes[:50],  # 最多返回 50 个节点
            "density": round(self.density, 6),
            "avg_pagerank": round(self.avg_pagerank, 6),
            "top_nodes": [n.to_dict() for n in self.top_nodes[:10]],
        }


@dataclass
class PathResult:
    """路径分析结果。"""
    source: str
    target: str
    path: list[str]
    length: int
    exists: bool = True


@dataclass
class GraphMetrics:
    """完整图谱指标。"""
    dataset_name: str
    computed_at: datetime = field(default_factory=datetime.now)
    statistics: GraphStatistics = field(default_factory=GraphStatistics)
    top_nodes: list[NodeMetric] = field(default_factory=list)
    communities: list[Community] = field(default_factory=list)
    centrality_summary: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "computed_at": self.computed_at.isoformat(),
            "statistics": self.statistics.to_dict(),
            "top_nodes": [n.to_dict() for n in self.top_nodes[:50]],
            "communities": [c.to_dict() for c in self.communities[:20]],
            "centrality_summary": self.centrality_summary,
        }


class GraphAnalytics:
    """图谱分析器。"""
    
    def __init__(
        self,
        dataset_name: str,
        cache_dir: Optional[Path] = None,
    ):
        self.dataset_name = dataset_name
        self.cache_dir = cache_dir or Path.home() / ".aof" / "graph_analytics"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cognee_available = self._check_cognee()
        self._graph: Optional[Any] = None  # NetworkX graph
    
    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        return importlib.util.find_spec("cognee") is not None
    
    async def _load_graph(self) -> Optional[Any]:
        """从 Cognee 加载图谱为 NetworkX 格式。"""
        if not self._cognee_available:
            return None
        
        if self._graph is not None:
            return self._graph
        
        try:
            import networkx as nx
            
            # 这里应该调用 Cognee 的 API 获取图谱数据
            # 由于 Cognee API 可能变化，这里提供一个通用框架
            
            # 创建有向图
            G = nx.DiGraph()
            
            # 尝试从 Cognee 获取数据
            try:
                # 获取图谱数据（这里需要根据实际 Cognee API 调整）
                # 暂时创建一个示例结构
                nodes = await self._get_nodes_from_cognee()
                edges = await self._get_edges_from_cognee()
                
                for node in nodes:
                    G.add_node(
                        node["id"],
                        label=node.get("label"),
                        node_type=node.get("type"),
                    )
                
                for edge in edges:
                    G.add_edge(
                        edge["source"],
                        edge["target"],
                        relation=edge.get("relation"),
                    )
                
            except Exception as e:
                # 如果失败，返回空图
                print(f"Failed to load from Cognee: {e}")
                pass
            
            self._graph = G
            return G
            
        except ImportError:
            raise ImportError(
                "networkx is required for graph analytics. "
                "Install with: pip install networkx"
            )
    
    async def _get_nodes_from_cognee(self) -> list[dict[str, Any]]:
        """从 Cognee 获取节点。"""
        nodes: list[dict[str, Any]] = []
        try:
            from cognee.infrastructure.databases.graph import get_graph_engine
            graph_engine = await get_graph_engine()

            if hasattr(graph_engine, "query"):
                result = await graph_engine.query("MATCH (n) RETURN n LIMIT 10000")
                if result:
                    for record in result:
                        node_dict = record[0] if isinstance(record, (list, tuple)) else record.get("n", record)
                        if not isinstance(node_dict, dict):
                            continue
                        node_id = str(node_dict.get("id", ""))
                        if not node_id:
                            continue
                        nodes.append({
                            "id": node_id,
                            "label": node_dict.get("name") or node_id,
                            "type": node_dict.get("type") or "unknown",
                        })
        except Exception:
            pass
        return nodes
    
    async def _get_edges_from_cognee(self) -> list[dict[str, Any]]:
        """从 Cognee 获取边。"""
        edges: list[dict[str, Any]] = []
        try:
            from cognee.infrastructure.databases.graph import get_graph_engine
            graph_engine = await get_graph_engine()

            if hasattr(graph_engine, "query"):
                result = await graph_engine.query("MATCH (a)-[r]->(b) RETURN a.id AS source, b.id AS target LIMIT 10000")
                if result:
                    for record in result:
                        if isinstance(record, (list, tuple)) and len(record) >= 2:
                            source, target = record[0], record[1]
                        elif isinstance(record, dict):
                            source = record.get("source")
                            target = record.get("target")
                        else:
                            continue
                        if source and target:
                            edges.append({
                                "source": str(source),
                                "target": str(target),
                                "relation": "related_to",
                            })
        except Exception:
            pass
        return edges
    
    async def compute_statistics(self) -> GraphStatistics:
        """
        计算基础图谱统计。
        
        Returns:
            统计信息
        """
        G = await self._load_graph()
        
        if G is None or len(G) == 0:
            return GraphStatistics()
        
        try:
            import networkx as nx
            
            stats = GraphStatistics()
            stats.node_count = G.number_of_nodes()
            stats.edge_count = G.number_of_edges()
            
            if stats.node_count > 0:
                # 密度
                if G.is_directed():
                    max_edges = stats.node_count * (stats.node_count - 1)
                else:
                    max_edges = stats.node_count * (stats.node_count - 1) // 2
                
                stats.density = stats.edge_count / max_edges if max_edges > 0 else 0
                
                # 度统计
                degrees = [d for n, d in G.degree()]
                stats.avg_degree = sum(degrees) / len(degrees) if degrees else 0
                stats.max_degree = max(degrees) if degrees else 0
                stats.min_degree = min(degrees) if degrees else 0
                
                # 连通分量
                if G.is_directed():
                    stats.connected_components = nx.number_weakly_connected_components(G)
                else:
                    stats.connected_components = nx.number_connected_components(G)
                
                # 聚类系数（仅对无向图）
                try:
                    if not G.is_directed():
                        stats.clustering_coefficient = nx.average_clustering(G)
                except Exception:
                    pass
                
                # 直径和平均路径长度（对大图可能很慢）
                if stats.node_count < 1000 and stats.connected_components == 1:
                    try:
                        if G.is_directed():
                            H = G.to_undirected()
                        else:
                            H = G
                        stats.diameter = nx.diameter(H)
                        stats.avg_path_length = nx.average_shortest_path_length(H)
                    except Exception:
                        pass
            
            return stats
            
        except Exception as e:
            print(f"Error computing statistics: {e}")
            return GraphStatistics()
    
    async def pagerank(
        self,
        alpha: float = 0.85,
        max_iter: int = 100,
        top_k: int = 20,
    ) -> list[NodeMetric]:
        """
        计算 PageRank。
        
        Args:
            alpha: 阻尼系数
            max_iter: 最大迭代次数
            top_k: 返回前 k 个节点
            
        Returns:
            节点 PageRank 列表
        """
        G = await self._load_graph()
        
        if G is None or len(G) == 0:
            return []
        
        try:
            import networkx as nx
            
            # 计算 PageRank
            pr = nx.pagerank(G, alpha=alpha, max_iter=max_iter)
            
            # 转换为 NodeMetric
            nodes = []
            for node_id, score in pr.items():
                node_data = G.nodes.get(node_id, {})
                metric = NodeMetric(
                    node_id=str(node_id),
                    node_type=node_data.get("node_type"),
                    label=node_data.get("label"),
                    pagerank=score,
                    degree=G.degree(node_id),
                    in_degree=G.in_degree(node_id) if G.is_directed() else G.degree(node_id),
                    out_degree=G.out_degree(node_id) if G.is_directed() else 0,
                )
                nodes.append(metric)
            
            # 按 PageRank 排序
            nodes.sort(key=lambda x: x.pagerank, reverse=True)
            
            return nodes[:top_k]
            
        except Exception as e:
            print(f"Error computing PageRank: {e}")
            return []
    
    async def compute_centrality(
        self,
        centrality_type: CentralityType,
        top_k: int = 20,
    ) -> list[NodeMetric]:
        """
        计算中心性指标。
        
        Args:
            centrality_type: 中心性类型
            top_k: 返回前 k 个节点
            
        Returns:
            节点中心性列表
        """
        G = await self._load_graph()
        
        if G is None or len(G) == 0:
            return []
        
        try:
            import networkx as nx
            
            # 选择算法
            if centrality_type == CentralityType.DEGREE:
                scores = nx.degree_centrality(G)
            elif centrality_type == CentralityType.BETWEENNESS:
                scores = nx.betweenness_centrality(G, k=min(100, len(G)))
            elif centrality_type == CentralityType.CLOSENESS:
                scores = nx.closeness_centrality(G)
            elif centrality_type == CentralityType.EIGENVECTOR:
                scores = nx.eigenvector_centrality(G, max_iter=1000, tol=1e-06)
            elif centrality_type == CentralityType.PAGERANK:
                return await self.pagerank(top_k=top_k)
            else:
                return []
            
            # 转换为 NodeMetric
            nodes = []
            for node_id, score in scores.items():
                node_data = G.nodes.get(node_id, {})
                metric = NodeMetric(
                    node_id=str(node_id),
                    node_type=node_data.get("node_type"),
                    label=node_data.get("label"),
                    degree=G.degree(node_id),
                )
                
                # 根据类型设置值
                if centrality_type == CentralityType.BETWEENNESS:
                    metric.betweenness = score
                elif centrality_type == CentralityType.CLOSENESS:
                    metric.closeness = score
                else:
                    metric.pagerank = score  # 使用 pagerank 字段存储其他中心性
                
                nodes.append(metric)
            
            # 排序
            if centrality_type == CentralityType.BETWEENNESS:
                nodes.sort(key=lambda x: x.betweenness, reverse=True)
            elif centrality_type == CentralityType.CLOSENESS:
                nodes.sort(key=lambda x: x.closeness, reverse=True)
            else:
                nodes.sort(key=lambda x: x.pagerank, reverse=True)
            
            return nodes[:top_k]
            
        except Exception as e:
            print(f"Error computing centrality: {e}")
            return []
    
    async def detect_communities(
        self,
        algorithm: CommunityAlgorithm = CommunityAlgorithm.LOUVAIN,
        resolution: float = 1.0,
    ) -> list[Community]:
        """
        检测社区。
        
        Args:
            algorithm: 社区检测算法
            resolution: 分辨率参数（Louvain）
            
        Returns:
            社区列表
        """
        G = await self._load_graph()
        
        if G is None or len(G) == 0:
            return []
        
        try:
            import networkx as nx
            
            # 转换为无向图（社区检测通常需要）
            if G.is_directed():
                H = G.to_undirected()
            else:
                H = G
            
            # 选择算法
            if algorithm == CommunityAlgorithm.LOUVAIN:
                try:
                    import community as community_louvain
                    partition = community_louvain.best_partition(H, resolution=resolution)
                except ImportError:
                    # 如果没有 python-louvain，使用贪心模块度
                    communities = nx.community.greedy_modularity_communities(H)
                    partition = {}
                    for i, comm in enumerate(communities):
                        for node in comm:
                            partition[node] = i
            
            elif algorithm == CommunityAlgorithm.LABEL_PROPAGATION:
                communities = nx.community.label_propagation_communities(H)
                partition = {}
                for i, comm in enumerate(communities):
                    for node in comm:
                        partition[node] = i
            
            elif algorithm == CommunityAlgorithm.GREEDY_MODULARITY:
                communities = nx.community.greedy_modularity_communities(H)
                partition = {}
                for i, comm in enumerate(communities):
                    for node in comm:
                        partition[node] = i
            
            else:
                return []
            
            # 计算 PageRank 用于社区排名
            pagerank_dict = nx.pagerank(G) if len(G) < 10000 else {}
            
            # 构建社区
            community_dict: dict[int, list[str]] = {}
            for node_id, comm_id in partition.items():
                if comm_id not in community_dict:
                    community_dict[comm_id] = []
                community_dict[comm_id].append(str(node_id))
            
            communities = []
            for comm_id, nodes in community_dict.items():
                # 计算社区密度
                subgraph = H.subgraph(nodes)
                density = nx.density(subgraph) if len(nodes) > 1 else 0
                
                # 计算平均 PageRank
                avg_pagerank = sum(pagerank_dict.get(n, 0) for n in nodes) / len(nodes) if nodes else 0
                
                # 获取 Top 节点
                top_nodes = []
                for node_id in nodes[:20]:  # 只处理前 20 个
                    node_data = G.nodes.get(node_id, {})
                    metric = NodeMetric(
                        node_id=node_id,
                        node_type=node_data.get("node_type"),
                        label=node_data.get("label"),
                        pagerank=pagerank_dict.get(node_id, 0),
                        degree=G.degree(node_id),
                        community=comm_id,
                    )
                    top_nodes.append(metric)
                
                top_nodes.sort(key=lambda x: x.pagerank, reverse=True)
                
                community = Community(
                    community_id=comm_id,
                    node_count=len(nodes),
                    nodes=nodes,
                    density=density,
                    avg_pagerank=avg_pagerank,
                    top_nodes=top_nodes[:10],
                )
                communities.append(community)
            
            # 按节点数排序
            communities.sort(key=lambda x: x.node_count, reverse=True)
            
            return communities
            
        except Exception as e:
            print(f"Error detecting communities: {e}")
            return []
    
    async def find_shortest_path(
        self,
        source: str,
        target: str,
    ) -> PathResult:
        """
        查找最短路径。
        
        Args:
            source: 起点节点 ID
            target: 终点节点 ID
            
        Returns:
            路径结果
        """
        G = await self._load_graph()
        
        if G is None or len(G) == 0:
            return PathResult(source=source, target=target, path=[], length=0, exists=False)
        
        try:
            import networkx as nx
            
            if source not in G or target not in G:
                return PathResult(source=source, target=target, path=[], length=0, exists=False)
            
            try:
                path = nx.shortest_path(G, source=source, target=target)
                return PathResult(
                    source=source,
                    target=target,
                    path=path,
                    length=len(path) - 1,
                    exists=True,
                )
            except nx.NetworkXNoPath:
                return PathResult(source=source, target=target, path=[], length=0, exists=False)
                
        except Exception as e:
            print(f"Error finding path: {e}")
            return PathResult(source=source, target=target, path=[], length=0, exists=False)
    
    async def compute_all_metrics(self) -> GraphMetrics:
        """
        计算所有指标。
        
        Returns:
            完整图谱指标
        """
        metrics = GraphMetrics(dataset_name=self.dataset_name)
        
        # 基础统计
        metrics.statistics = await self.compute_statistics()
        
        # PageRank Top 节点
        metrics.top_nodes = await self.pagerank(top_k=50)
        
        # 社区检测
        metrics.communities = await self.detect_communities()
        
        # 为每个节点分配社区
        community_map = {}
        for comm in metrics.communities:
            for node_id in comm.nodes:
                community_map[node_id] = comm.community_id
        
        for node in metrics.top_nodes:
            node.community = community_map.get(node.node_id)
        
        # 中心性汇总
        metrics.centrality_summary = {
            "max_pagerank": max((n.pagerank for n in metrics.top_nodes), default=0),
            "avg_pagerank": sum(n.pagerank for n in metrics.top_nodes) / len(metrics.top_nodes) if metrics.top_nodes else 0,
            "community_count": len(metrics.communities),
            "largest_community_size": max((c.node_count for c in metrics.communities), default=0),
        }
        
        return metrics
    
    def export_metrics(self, metrics: GraphMetrics, output_dir: Path) -> dict[str, Path]:
        """
        导出指标到文件。
        
        Args:
            metrics: 图谱指标
            output_dir: 输出目录
            
        Returns:
            导出的文件路径
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        files = {}
        
        # 导出完整指标
        metrics_file = output_dir / f"{self.dataset_name}_metrics.json"
        metrics_file.write_text(
            json.dumps(metrics.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        files["metrics"] = metrics_file
        
        # 导出节点列表（CSV）
        try:
            import csv
            nodes_file = output_dir / f"{self.dataset_name}_nodes.csv"
            with open(nodes_file, "w", newline="", encoding="utf-8") as f:
                if metrics.top_nodes:
                    writer = csv.DictWriter(f, fieldnames=metrics.top_nodes[0].to_dict().keys())
                    writer.writeheader()
                    for node in metrics.top_nodes:
                        writer.writerow(node.to_dict())
            files["nodes_csv"] = nodes_file
        except Exception:
            pass
        
        # 导出为 GEXF（Gephi 格式）
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            G = loop.run_until_complete(self._load_graph())
            
            if G is not None:
                import networkx as nx
                
                # 添加社区信息到节点
                for node in metrics.top_nodes:
                    if node.node_id in G:
                        G.nodes[node.node_id]["pagerank"] = node.pagerank
                        G.nodes[node.node_id]["community"] = node.community
                
                gexf_file = output_dir / f"{self.dataset_name}.gexf"
                nx.write_gexf(G, str(gexf_file))
                files["gexf"] = gexf_file
        except Exception:
            pass
        
        return files


# 便捷函数
async def analyze_graph(
    dataset_name: str,
    compute_communities: bool = True,
) -> GraphMetrics:
    """便捷函数：分析图谱。
    
    Args:
        dataset_name: 数据集名称
        compute_communities: 是否计算社区
        
    Returns:
        图谱指标
    """
    analyzer = GraphAnalytics(dataset_name)
    return await analyzer.compute_all_metrics()


async def get_pagerank(
    dataset_name: str,
    top_k: int = 20,
) -> list[NodeMetric]:
    """便捷函数：获取 PageRank。
    
    Args:
        dataset_name: 数据集名称
        top_k: 返回前 k 个
        
    Returns:
        PageRank 列表
    """
    analyzer = GraphAnalytics(dataset_name)
    return await analyzer.pagerank(top_k=top_k)


async def detect_communities(
    dataset_name: str,
    algorithm: str = "louvain",
) -> list[Community]:
    """便捷函数：检测社区。
    
    Args:
        dataset_name: 数据集名称
        algorithm: 算法名称
        
    Returns:
        社区列表
    """
    analyzer = GraphAnalytics(dataset_name)
    return await analyzer.detect_communities(CommunityAlgorithm(algorithm))
