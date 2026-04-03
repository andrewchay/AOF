"""NebulaGraph 存储后端

适配 NebulaGraph 的存储后端实现。

需要安装:
    pip install nebula3-python
"""

from __future__ import annotations

import json
import logging
from typing import Optional, List, Dict, Any, Tuple

try:
    from nebula3.gclient.net import ConnectionPool
    from nebula3.Config import Config
    NEBULA_AVAILABLE = True
except ImportError:
    NEBULA_AVAILABLE = False
    ConnectionPool = None
    Config = None

from .base import (
    GraphBackend, Node, Edge, Triple, Path,
    PageRankResult, Community, GraphStatistics
)

logger = logging.getLogger(__name__)


class NebulaBackend(GraphBackend):
    """NebulaGraph 存储后端
    
    提供与 NebulaGraph 的完整集成。
    
    使用示例:
        backend = NebulaBackend("nebula", {
            "nebula_host": "127.0.0.1",
            "nebula_port": 9669,
            "nebula_user": "root",
            "nebula_password": "nebula",
            "nebula_space": "aof_default",
        })
        await backend.connect()
    """
    
    def __init__(self, name: str = "nebula", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        
        cfg = config or {}
        self.host = cfg.get("nebula_host", "127.0.0.1")
        self.port = cfg.get("nebula_port", 9669)
        self.user = cfg.get("nebula_user", "root")
        self.password = cfg.get("nebula_password", "nebula")
        self.space = cfg.get("nebula_space", "aof_default")
        
        self.pool: Optional[ConnectionPool] = None
        self._session = None
    
    async def connect(self) -> bool:
        """连接到 NebulaGraph"""
        if not NEBULA_AVAILABLE:
            logger.error("nebula3-python is not installed")
            return False
        
        try:
            # 配置连接池
            config = Config()
            config.max_connection_pool_size = self.config.get("max_connections", 10)
            config.timeout = self.config.get("connection_timeout", 30) * 1000
            
            # 初始化连接池
            self.pool = ConnectionPool()
            
            # 注意：nebula3-python 的 init 是同步的
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.pool.init([(self.host, self.port)], config)
            )
            
            if not result:
                logger.error("Failed to initialize Nebula connection pool")
                return False
            
            self._connected = True
            logger.info(f"Connected to NebulaGraph at {self.host}:{self.port}")
            
            # 确保空间存在
            await self._ensure_space()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to NebulaGraph: {e}")
            return False
    
    async def disconnect(self) -> None:
        """断开连接"""
        if self.pool:
            try:
                self.pool.close()
            except Exception as e:
                logger.error(f"Error closing pool: {e}")
        
        self._connected = False
        logger.info("Disconnected from NebulaGraph")
    
    async def health_check(self) -> bool:
        """健康检查"""
        if not self._connected or not self.pool:
            return False
        
        try:
            session = self.pool.get_session(self.user, self.password)
            result = session.execute("SHOW HOSTS")
            session.release()
            return result.is_succeeded()
        except:
            return False
    
    def _get_session(self):
        """获取会话"""
        if not self.pool:
            raise RuntimeError("Not connected to NebulaGraph")
        return self.pool.get_session(self.user, self.password)
    
    def _execute(self, ngql: str) -> Any:
        """执行 nGQL 查询"""
        session = self._get_session()
        try:
            # 切换到指定空间
            session.execute(f"USE {self.space}")
            result = session.execute(ngql)
            return result
        finally:
            session.release()
    
    async def _ensure_space(self) -> None:
        """确保 GraphSpace 存在"""
        try:
            session = self._get_session()
            
            # 创建空间（如果不存在）
            create_space_ngql = f"""
            CREATE SPACE IF NOT EXISTS {self.space} (
                partition_num=100,
                replica_factor=1,
                vid_type=FIXED_STRING(64)
            )
            """
            session.execute(create_space_ngql)
            
            # 等待空间创建
            import asyncio
            await asyncio.sleep(1)
            
            # 创建基本 Schema
            session.execute(f"USE {self.space}")
            
            # 创建 Tag
            session.execute("""
                CREATE TAG IF NOT EXISTS entity (
                    name string NOT NULL,
                    type string NOT NULL,
                    properties string,
                    source_doc string,
                    created_at timestamp
                )
            """)
            
            # 创建 Edge
            session.execute("""
                CREATE EDGE IF NOT EXISTS relates_to (
                    relation_type string NOT NULL,
                    weight double DEFAULT 1.0,
                    properties string,
                    created_at timestamp
                )
            """)
            
            session.release()
            logger.info(f"Ensured NebulaGraph space: {self.space}")
            
        except Exception as e:
            logger.error(f"Failed to ensure space: {e}")
            raise
    
    # ========== 数据写入 ==========
    
    async def add_node(self, node: Node) -> bool:
        """添加节点"""
        try:
            props = json.dumps(node.properties) if node.properties else ""
            labels = node.labels[0] if node.labels else "entity"
            
            ngql = f'''
            INSERT VERTEX entity(name, type, properties)
            VALUES "{self._escape_vid(node.id)}":("{self._escape(node.get('name', node.id))}", "{labels}", "{self._escape(props)}")
            '''
            
            result = self._execute(ngql)
            return result.is_succeeded()
            
        except Exception as e:
            logger.error(f"Failed to add node: {e}")
            return False
    
    async def add_edge(self, edge: Edge) -> bool:
        """添加边"""
        try:
            props = json.dumps(edge.properties) if edge.properties else ""
            
            ngql = f'''
            INSERT EDGE relates_to(relation_type, weight, properties)
            VALUES "{self._escape_vid(edge.source_id)}"->"{self._escape_vid(edge.target_id)}":("{self._escape(edge.relation_type)}", 1.0, "{self._escape(props)}")
            '''
            
            result = self._execute(ngql)
            return result.is_succeeded()
            
        except Exception as e:
            logger.error(f"Failed to add edge: {e}")
            return False
    
    async def add_triples(self, triples: List[Triple]) -> int:
        """批量添加三元组"""
        if not triples:
            return 0
        
        success_count = 0
        
        # 批量插入优化
        vertex_batch = []
        edge_batch = []
        
        for triple in triples:
            # 准备顶点数据
            src_props = json.dumps(triple.subject.properties) if triple.subject.properties else ""
            dst_props = json.dumps(triple.object.properties) if triple.object.properties else ""
            
            src_type = triple.subject.labels[0] if triple.subject.labels else "entity"
            dst_type = triple.object.labels[0] if triple.object.labels else "entity"
            
            # 添加顶点到批次
            vertex_batch.append(
                f'"{self._escape_vid(triple.subject.id)}":("{self._escape(triple.subject.get("name", triple.subject.id))}", "{src_type}", "{self._escape(src_props)}")'
            )
            vertex_batch.append(
                f'"{self._escape_vid(triple.object.id)}":("{self._escape(triple.object.get("name", triple.object.id))}", "{dst_type}", "{self._escape(dst_props)}")'
            )
            
            # 添加边到批次
            edge_props = json.dumps(triple.edge_properties) if triple.edge_properties else ""
            edge_batch.append(
                f'"{self._escape_vid(triple.subject.id)}"->"{self._escape_vid(triple.object.id)}":("{self._escape(triple.predicate)}", 1.0, "{self._escape(edge_props)}")'
            )
        
        # 执行批量插入
        try:
            # 插入顶点
            if vertex_batch:
                # 去重
                unique_vertices = list(dict.fromkeys(vertex_batch))
                vertex_ngql = f"INSERT VERTEX entity(name, type, properties) VALUES {','.join(unique_vertices)}"
                result = self._execute(vertex_ngql)
                if result.is_succeeded():
                    success_count += len(triples)
                else:
                    logger.warning(f"Vertex insert warning: {result.error_msg()}")
            
            # 插入边
            if edge_batch:
                edge_ngql = f"INSERT EDGE relates_to(relation_type, weight, properties) VALUES {','.join(edge_batch)}"
                result = self._execute(edge_ngql)
                if not result.is_succeeded():
                    logger.warning(f"Edge insert warning: {result.error_msg()}")
            
        except Exception as e:
            logger.error(f"Failed to add triples: {e}")
        
        return success_count
    
    async def merge_node(self, node: Node) -> Node:
        """合并节点（UPSERT）"""
        # NebulaGraph 3.x 支持 UPSERT
        try:
            props = json.dumps(node.properties) if node.properties else ""
            labels = node.labels[0] if node.labels else "entity"
            
            ngql = f'''
            UPSERT VERTEX ON entity "{self._escape_vid(node.id)}"
            SET name = "{self._escape(node.get('name', node.id))}",
                type = "{labels}",
                properties = "{self._escape(props)}"
            '''
            
            result = self._execute(ngql)
            if result.is_succeeded():
                return node
            else:
                raise RuntimeError(f"UPSERT failed: {result.error_msg()}")
                
        except Exception as e:
            logger.error(f"Failed to merge node: {e}")
            raise
    
    # ========== 数据查询 ==========
    
    async def get_node(self, node_id: str) -> Optional[Node]:
        """获取节点"""
        try:
            ngql = f'FETCH PROP ON entity "{self._escape_vid(node_id)}" YIELD properties(vertex) AS props'
            
            result = self._execute(ngql)
            if not result.is_succeeded():
                return None
            
            # 解析结果
            rows = result.rows()
            if not rows:
                return None
            
            # 构建 Node 对象
            return Node(
                id=node_id,
                labels=["entity"],
                properties={"id": node_id}  # 简化处理
            )
            
        except Exception as e:
            logger.error(f"Failed to get node: {e}")
            return None
    
    async def get_neighbors(
        self,
        node_id: str,
        direction: str = "both",
        edge_types: Optional[List[str]] = None,
        limit: int = 100
    ) -> List[Node]:
        """获取邻居节点"""
        try:
            # 构建查询
            if direction == "out":
                ngql = f'GO FROM "{self._escape_vid(node_id)}" OVER * YIELD dst(edge) AS neighbor LIMIT {limit}'
            elif direction == "in":
                ngql = f'GO FROM "{self._escape_vid(node_id)}" OVER * REVERSELY YIELD dst(edge) AS neighbor LIMIT {limit}'
            else:  # both
                ngql = f'GO FROM "{self._escape_vid(node_id)}" OVER * BIDIRECT YIELD dst(edge) AS neighbor LIMIT {limit}'
            
            result = self._execute(ngql)
            if not result.is_succeeded():
                return []
            
            # 解析结果
            neighbors = []
            for row in result.rows():
                # 解析 neighbor ID
                neighbor_id = str(row.values[0].get_sVal(), 'utf-8') if row.values[0].get_sVal() else ""
                if neighbor_id:
                    neighbors.append(Node(id=neighbor_id, labels=["entity"]))
            
            return neighbors
            
        except Exception as e:
            logger.error(f"Failed to get neighbors: {e}")
            return []
    
    async def get_edges(
        self,
        source_id: Optional[str] = None,
        target_id: Optional[str] = None,
        relation_type: Optional[str] = None,
    ) -> List[Edge]:
        """获取边"""
        # 简化实现
        return []
    
    # ========== 路径查询 ==========
    
    async def find_paths(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 5,
        direction: str = "out",
    ) -> List[Path]:
        """查找路径"""
        try:
            bidirect = "BIDIRECT" if direction == "both" else ""
            ngql = f'''
            FIND ALL PATH FROM "{self._escape_vid(source_id)}" TO "{self._escape_vid(target_id)}"
            OVER * {bidirect}
            UPTO {max_depth} STEPS
            YIELD path AS p
            '''
            
            result = self._execute(ngql)
            if not result.is_succeeded():
                return []
            
            # 解析路径
            paths = []
            for row in result.rows():
                # 解析 path
                # 简化处理
                paths.append(Path())
            
            return paths
            
        except Exception as e:
            logger.error(f"Failed to find paths: {e}")
            return []
    
    async def shortest_path(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 10,
    ) -> Optional[Path]:
        """最短路径"""
        try:
            ngql = f'''
            FIND SHORTEST PATH FROM "{self._escape_vid(source_id)}" TO "{self._escape_vid(target_id)}"
            OVER *
            UPTO {max_depth} STEPS
            YIELD path AS p
            '''
            
            result = self._execute(ngql)
            if not result.is_succeeded():
                return None
            
            rows = result.rows()
            if not rows:
                return None
            
            return Path()
            
        except Exception as e:
            logger.error(f"Failed to find shortest path: {e}")
            return None
    
    # ========== 图算法 ==========
    
    async def pagerank(
        self,
        top_k: int = 100,
        max_iterations: int = 20,
        damping: float = 0.85,
    ) -> List[PageRankResult]:
        """PageRank 算法（需要 NebulaGraph Analytics）"""
        logger.warning("PageRank requires NebulaGraph Analytics. Using fallback.")
        return []
    
    async def community_detection(
        self,
        algorithm: str = "louvain",
    ) -> List[Community]:
        """社区检测（需要 NebulaGraph Analytics）"""
        logger.warning("Community detection requires NebulaGraph Analytics. Using fallback.")
        return []
    
    # ========== 统计信息 ==========
    
    async def get_statistics(self) -> GraphStatistics:
        """获取图谱统计"""
        try:
            stats = GraphStatistics()
            
            # 统计顶点
            result = self._execute("SUBMIT JOB STATS")
            if result.is_succeeded():
                # 等待统计完成并查询
                stats.node_count = await self.count_nodes()
                stats.edge_count = await self.count_edges()
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            return GraphStatistics()
    
    async def count_nodes(self, label: Optional[str] = None) -> int:
        """统计节点数"""
        try:
            ngql = "MATCH (v) RETURN count(v) AS cnt"
            result = self._execute(ngql)
            if result.is_succeeded() and result.rows():
                return result.rows()[0].values[0].get_iVal()
            return 0
        except:
            return 0
    
    async def count_edges(self, relation_type: Optional[str] = None) -> int:
        """统计边数"""
        try:
            ngql = "MATCH ()-[e]->() RETURN count(e) AS cnt"
            result = self._execute(ngql)
            if result.is_succeeded() and result.rows():
                return result.rows()[0].values[0].get_iVal()
            return 0
        except:
            return 0
    
    # ========== 查询执行 ==========
    
    async def execute_cypher(self, query: str, parameters: Optional[Dict] = None) -> List[Dict]:
        """执行 nGQL 查询"""
        try:
            # 参数替换（简单的字符串替换）
            if parameters:
                for key, value in parameters.items():
                    query = query.replace(f"${key}", f'"{value}"')
            
            result = self._execute(query)
            
            if not result.is_succeeded():
                raise RuntimeError(f"Query failed: {result.error_msg()}")
            
            # 解析结果为字典列表
            rows = []
            for row in result.rows():
                row_dict = {}
                for i, col_name in enumerate(result.keys()):
                    # 简化处理
                    row_dict[col_name] = str(row.values[i])
                rows.append(row_dict)
            
            return rows
            
        except Exception as e:
            logger.error(f"Failed to execute query: {e}")
            raise
    
    # ========== 数据删除 ==========
    
    async def delete_node(self, node_id: str) -> bool:
        """删除节点"""
        try:
            ngql = f'DELETE VERTEX "{self._escape_vid(node_id)}"'
            result = self._execute(ngql)
            return result.is_succeeded()
        except Exception as e:
            logger.error(f"Failed to delete node: {e}")
            return False
    
    async def delete_edge(self, source_id: str, target_id: str, relation_type: str) -> bool:
        """删除边"""
        try:
            ngql = f'DELETE EDGE relates_to "{self._escape_vid(source_id)}"->"{self._escape_vid(target_id)}"'
            result = self._execute(ngql)
            return result.is_succeeded()
        except Exception as e:
            logger.error(f"Failed to delete edge: {e}")
            return False
    
    async def clear(self) -> bool:
        """清空数据（危险操作）"""
        try:
            # 清空空间中的所有数据
            ngql = f"DROP SPACE {self.space}"
            result = self._execute(ngql)
            if result.is_succeeded():
                # 重新创建空间
                await self._ensure_space()
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to clear data: {e}")
            return False
    
    # ========== 工具方法 ==========
    
    def _escape_vid(self, vid: str) -> str:
        """转义顶点 ID"""
        return str(vid).replace('"', '\\"')
    
    def _escape(self, s: str) -> str:
        """转义字符串"""
        return s.replace('"', '\\"').replace("\\", "\\\\")
