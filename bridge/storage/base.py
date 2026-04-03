"""存储层基础抽象

定义图存储后端的统一接口和数据模型。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class Node:
    """图节点"""
    id: str
    labels: List[str] = field(default_factory=list)  # 类型标签
    properties: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        # 确保 id 是字符串
        self.id = str(self.id)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取属性"""
        return self.properties.get(key, default)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "labels": self.labels,
            "properties": self.properties,
        }


@dataclass
class Edge:
    """图边"""
    id: Optional[str] = None
    source_id: str = ""
    target_id: str = ""
    relation_type: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        self.source_id = str(self.source_id)
        self.target_id = str(self.target_id)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "properties": self.properties,
        }


@dataclass
class Triple:
    """三元组 (Subject - Predicate -> Object)"""
    subject: Node
    predicate: str
    object: Node
    edge_properties: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject.to_dict(),
            "predicate": self.predicate,
            "object": self.object.to_dict(),
            "edge_properties": self.edge_properties,
        }


@dataclass
class Path:
    """图路径"""
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    
    @property
    def length(self) -> int:
        """路径长度（边数）"""
        return len(self.edges)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "length": self.length,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }


@dataclass
class PageRankResult:
    """PageRank 结果"""
    node_id: str
    score: float
    node: Optional[Node] = None


@dataclass
class Community:
    """社区检测结果"""
    community_id: int
    members: List[str]  # 节点 ID 列表
    size: int = 0
    
    def __post_init__(self):
        self.size = len(self.members)


@dataclass
class GraphStatistics:
    """图谱统计信息"""
    node_count: int = 0
    edge_count: int = 0
    node_types: Dict[str, int] = field(default_factory=dict)
    edge_types: Dict[str, int] = field(default_factory=dict)


class GraphBackend(ABC):
    """图存储后端抽象基类
    
    所有存储后端必须实现这些接口。
    """
    
    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}
        self._connected = False
    
    @property
    def connected(self) -> bool:
        return self._connected
    
    @abstractmethod
    async def connect(self) -> bool:
        """建立连接"""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass
    
    # ========== 数据写入 ==========
    
    @abstractmethod
    async def add_node(self, node: Node) -> bool:
        """添加节点"""
        pass
    
    @abstractmethod
    async def add_edge(self, edge: Edge) -> bool:
        """添加边"""
        pass
    
    @abstractmethod
    async def add_triples(self, triples: List[Triple]) -> int:
        """批量添加三元组
        
        Returns:
            成功添加的三元组数量
        """
        pass
    
    @abstractmethod
    async def merge_node(self, node: Node) -> Node:
        """合并节点（存在则更新，不存在则创建）"""
        pass
    
    # ========== 数据查询 ==========
    
    @abstractmethod
    async def get_node(self, node_id: str) -> Optional[Node]:
        """根据 ID 获取节点"""
        pass
    
    @abstractmethod
    async def get_neighbors(
        self,
        node_id: str,
        direction: str = "both",  # "in", "out", "both"
        edge_types: Optional[List[str]] = None,
        limit: int = 100
    ) -> List[Node]:
        """获取邻居节点"""
        pass
    
    @abstractmethod
    async def get_edges(
        self,
        source_id: Optional[str] = None,
        target_id: Optional[str] = None,
        relation_type: Optional[str] = None,
    ) -> List[Edge]:
        """获取边"""
        pass
    
    # ========== 路径查询 ==========
    
    @abstractmethod
    async def find_paths(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 5,
        direction: str = "out",
    ) -> List[Path]:
        """查找路径"""
        pass
    
    @abstractmethod
    async def shortest_path(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 10,
    ) -> Optional[Path]:
        """最短路径"""
        pass
    
    # ========== 图算法 ==========
    
    @abstractmethod
    async def pagerank(
        self,
        top_k: int = 100,
        max_iterations: int = 20,
        damping: float = 0.85,
    ) -> List[PageRankResult]:
        """PageRank 算法"""
        pass
    
    @abstractmethod
    async def community_detection(
        self,
        algorithm: str = "louvain",  # "louvain", "label_propagation"
    ) -> List[Community]:
        """社区检测"""
        pass
    
    # ========== 统计信息 ==========
    
    @abstractmethod
    async def get_statistics(self) -> GraphStatistics:
        """获取图谱统计"""
        pass
    
    @abstractmethod
    async def count_nodes(self, label: Optional[str] = None) -> int:
        """统计节点数"""
        pass
    
    @abstractmethod
    async def count_edges(self, relation_type: Optional[str] = None) -> int:
        """统计边数"""
        pass
    
    # ========== 查询执行 ==========
    
    @abstractmethod
    async def execute_cypher(self, query: str, parameters: Optional[Dict] = None) -> List[Dict]:
        """执行原生查询（Cypher 或 nGQL）"""
        pass
    
    # ========== 数据删除 ==========
    
    @abstractmethod
    async def delete_node(self, node_id: str) -> bool:
        """删除节点"""
        pass
    
    @abstractmethod
    async def delete_edge(self, source_id: str, target_id: str, relation_type: str) -> bool:
        """删除边"""
        pass
    
    @abstractmethod
    async def clear(self) -> bool:
        """清空数据（危险操作）"""
        pass
    
    # ========== 事务支持（可选）==========
    
    async def begin_transaction(self) -> Any:
        """开始事务（可选实现）"""
        raise NotImplementedError("Transaction not supported")
    
    async def commit_transaction(self, tx: Any) -> None:
        """提交事务（可选实现）"""
        raise NotImplementedError("Transaction not supported")
    
    async def rollback_transaction(self, tx: Any) -> None:
        """回滚事务（可选实现）"""
        raise NotImplementedError("Transaction not supported")


class BatchInserter:
    """批量插入助手
    
    自动批量提交以提高性能。
    """
    
    def __init__(
        self,
        backend: GraphBackend,
        batch_size: int = 1000,
        auto_commit: bool = True,
    ):
        self.backend = backend
        self.batch_size = batch_size
        self.auto_commit = auto_commit
        
        self._buffer: List[Triple] = []
        self._total_inserted = 0
    
    async def add(self, triple: Triple) -> None:
        """添加三元组到缓冲区"""
        self._buffer.append(triple)
        
        if len(self._buffer) >= self.batch_size and self.auto_commit:
            await self.flush()
    
    async def flush(self) -> int:
        """手动刷新缓冲区"""
        if not self._buffer:
            return 0
        
        count = await self.backend.add_triples(self._buffer)
        self._total_inserted += count
        self._buffer.clear()
        
        return count
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.flush()
    
    @property
    def total_inserted(self) -> int:
        return self._total_inserted
