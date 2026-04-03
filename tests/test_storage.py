"""存储层单元测试

运行:
    cd /path/to/AOF
    python -m pytest tests/test_storage.py -v
"""

import pytest
from unittest.mock import MagicMock, patch

from bridge.storage.base import Node, Edge, Triple, Path, GraphBackend, BatchInserter
from bridge.storage.factory import StorageFactory, StorageConfig
from bridge.storage.ngql_builder import nGQLBuilder, F, match, go, insert_vertex
from bridge.storage.cypher_to_ngql import CypherToNGQL, convert_get_neighbors


class TestNode:
    """测试 Node 数据类"""
    
    def test_node_creation(self):
        """测试创建节点"""
        node = Node(id="123", labels=["Person"], properties={"name": "Alice"})
        
        assert node.id == "123"
        assert node.labels == ["Person"]
        assert node.properties["name"] == "Alice"
    
    def test_node_id_string_conversion(self):
        """测试 ID 自动转字符串"""
        node = Node(id=123, labels=[])
        assert isinstance(node.id, str)
        assert node.id == "123"
    
    def test_node_get(self):
        """测试 get 方法"""
        node = Node(id="1", properties={"name": "Test", "age": 30})
        
        assert node.get("name") == "Test"
        assert node.get("age") == 30
        assert node.get("nonexistent") is None
        assert node.get("nonexistent", "default") == "default"
    
    def test_node_to_dict(self):
        """测试转字典"""
        node = Node(id="1", labels=["Test"], properties={"a": 1})
        d = node.to_dict()
        
        assert d["id"] == "1"
        assert d["labels"] == ["Test"]
        assert d["properties"]["a"] == 1


class TestEdge:
    """测试 Edge 数据类"""
    
    def test_edge_creation(self):
        """测试创建边"""
        edge = Edge(
            id="e1",
            source_id="a",
            target_id="b",
            relation_type="KNOWS",
            properties={"since": 2020}
        )
        
        assert edge.source_id == "a"
        assert edge.target_id == "b"
        assert edge.relation_type == "KNOWS"
    
    def test_edge_id_conversion(self):
        """测试 ID 转换"""
        edge = Edge(source_id=123, target_id=456, relation_type="TEST")
        assert edge.source_id == "123"
        assert edge.target_id == "456"


class TestTriple:
    """测试 Triple 数据类"""
    
    def test_triple_creation(self):
        """测试创建三元组"""
        subject = Node(id="s1", labels=["Person"])
        obj = Node(id="o1", labels=["Company"])
        
        triple = Triple(
            subject=subject,
            predicate="WORKS_AT",
            object=obj,
            edge_properties={"since": 2020}
        )
        
        assert triple.subject.id == "s1"
        assert triple.predicate == "WORKS_AT"
        assert triple.object.id == "o1"
        assert triple.edge_properties["since"] == 2020


class TestPath:
    """测试 Path 数据类"""
    
    def test_path_length(self):
        """测试路径长度"""
        nodes = [Node(id="a"), Node(id="b"), Node(id="c")]
        edges = [Edge(source_id="a", target_id="b"), Edge(source_id="b", target_id="c")]
        
        path = Path(nodes=nodes, edges=edges)
        assert path.length == 2


class TestStorageFactory:
    """测试存储工厂"""
    
    def test_list_backends(self):
        """测试列出后端"""
        backends = StorageFactory.list_backends()
        assert isinstance(backends, list)
        # 至少应该有 cognee
        assert "cognee" in backends
    
    def test_create_cognee_backend(self):
        """测试创建 Cognee 后端"""
        config = StorageConfig(backend_type="cognee")
        backend = StorageFactory.create(config, name="test_cognee")
        
        assert backend.name == "cognee"
        assert StorageFactory.get_instance("test_cognee") == backend
    
    def test_create_unknown_backend(self):
        """测试创建未知后端"""
        config = StorageConfig(backend_type="unknown")
        
        with pytest.raises(ValueError):
            StorageFactory.create(config)
    
    def test_clear_cache(self):
        """测试清空缓存"""
        config = StorageConfig(backend_type="cognee")
        StorageFactory.create(config, name="cache_test")
        
        assert StorageFactory.get_instance("cache_test") is not None
        
        StorageFactory.clear_cache()
        
        # 缓存清空后，应该创建新实例
        backend2 = StorageFactory.create(config, name="cache_test")
        assert StorageFactory.get_instance("cache_test") == backend2


class TestNGQLBuilder:
    """测试 nGQL 构建器"""
    
    def test_simple_match(self):
        """测试简单 MATCH"""
        query = (nGQLBuilder().match()
                 .node("entity", "v")
                 .return_("v")
                 .build())
        
        assert "MATCH (v:entity)" in query
        assert "RETURN v" in query
    
    def test_match_with_edge(self):
        """测试带边的 MATCH"""
        query = (nGQLBuilder().match()
                 .node("Person", "a")
                 .edge("KNOWS", "e", "out")
                 .node("Person", "b")
                 .return_("a.name", "b.name")
                 .build())
        
        # 检查关键部分（允许空格差异）
        assert "MATCH" in query
        assert "(a:Person)" in query
        assert "-[e:KNOWS]->" in query
        assert "(b:Person)" in query
    
    def test_where_condition(self):
        """测试 WHERE 条件"""
        query = (nGQLBuilder().match()
                 .node("entity", "v")
                 .where(F.field("v.type") == "Person")
                 .return_("v")
                 .build())
        
        assert "WHERE v.type == \"Person\"" in query
    
    def test_where_and(self):
        """测试 AND 条件"""
        cond = (F.field("v.type") == "Person") & (F.field("v.age") > 18)
        
        query = (nGQLBuilder().match()
                 .node("entity", "v")
                 .where(cond)
                 .return_("v")
                 .build())
        
        assert "v.type == \"Person\"" in query
        assert "v.age > 18" in query
    
    def test_limit(self):
        """测试 LIMIT"""
        query = (nGQLBuilder().match()
                 .node("entity", "v")
                 .return_("v")
                 .limit(10)
                 .build())
        
        assert "LIMIT 10" in query
    
    def test_go_statement(self):
        """测试 GO 语句"""
        query = (go(1, "user_123")
                 .over("relates_to")
                 .yield_("dst(edge) as neighbor")
                 .build())
        
        assert "GO 1 STEP FROM \"user_123\"" in query
        assert "OVER relates_to" in query
    
    def test_insert_vertex(self):
        """测试 INSERT VERTEX"""
        values = {
            "vid1": ["Alice", "Person"],
            "vid2": ["Bob", "Person"],
        }
        
        query = insert_vertex("entity", ["name", "type"], values)
        
        assert "INSERT VERTEX entity(name, type) VALUES" in query
        assert '"vid1":("Alice", "Person")' in query


class TestCypherConverter:
    """测试 Cypher 转换器"""
    
    def test_convert_simple_match(self):
        """测试转换简单 MATCH"""
        converter = CypherToNGQL()
        
        cypher = "MATCH (n:Person) RETURN n"
        ngql = converter.convert(cypher)
        
        assert "MATCH" in ngql
        assert "RETURN" in ngql
    
    def test_convert_neighbors(self):
        """测试转换邻居查询"""
        ngql = convert_get_neighbors("user_123", "KNOWS")
        
        assert "GO FROM \"user_123\"" in ngql
        assert "OVER KNOWS" in ngql
    
    def test_convert_shortest_path(self):
        """测试转换最短路径"""
        from bridge.storage.cypher_to_ngql import convert_shortest_path
        
        ngql = convert_shortest_path("a", "b", 5)
        
        assert "FIND SHORTEST PATH" in ngql
        assert "FROM \"a\" TO \"b\"" in ngql
        assert "UPTO 5 STEPS" in ngql
    
    def test_can_convert(self):
        """测试检查是否可转换"""
        converter = CypherToNGQL()
        
        # 简单查询应该可以转换
        assert converter.can_convert("MATCH (n) RETURN n") is True
        
        # 复杂查询可能无法转换
        # 这里假设所有测试查询都能通过通用转换


class TestBatchInserter:
    """测试批量插入器"""
    
    @pytest.mark.asyncio
    async def test_batch_insert(self):
        """测试批量插入"""
        async def mock_add_triples(triples):
            return len(triples)
        
        backend = MagicMock()
        backend.add_triples = mock_add_triples
        
        inserter = BatchInserter(backend, batch_size=2)
        
        triple1 = Triple(
            subject=Node(id="s1"),
            predicate="TEST",
            object=Node(id="o1")
        )
        triple2 = Triple(
            subject=Node(id="s2"),
            predicate="TEST",
            object=Node(id="o2")
        )
        
        await inserter.add(triple1)
        assert len(inserter._buffer) == 1
        
        await inserter.add(triple2)  # 应该触发自动提交
        assert len(inserter._buffer) == 0
        assert inserter.total_inserted == 2
    
    @pytest.mark.asyncio
    async def test_flush(self):
        """测试手动刷新"""
        async def mock_add_triples(triples):
            return len(triples)
        
        backend = MagicMock()
        backend.add_triples = mock_add_triples
        
        inserter = BatchInserter(backend, batch_size=10)
        
        triple = Triple(
            subject=Node(id="s1"),
            predicate="TEST",
            object=Node(id="o1")
        )
        
        await inserter.add(triple)
        count = await inserter.flush()
        
        assert count == 1
        assert len(inserter._buffer) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
