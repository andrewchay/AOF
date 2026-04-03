"""缓存模块单元测试

运行:
    cd /path/to/AOF
    python -m pytest tests/test_cache.py -v
"""

import pytest
import time
import asyncio
from unittest.mock import AsyncMock, MagicMock

from bridge.cache.local_cache import LocalCache
from bridge.cache.manager import CacheManager, CacheStrategy, CacheConfig


class TestLocalCache:
    """测试本地 LRU 缓存"""
    
    @pytest.fixture
    def cache(self):
        """创建测试缓存"""
        return LocalCache(max_size=100, default_ttl=300)
    
    def test_basic_get_set(self, cache):
        """测试基本读写"""
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"
    
    def test_get_nonexistent(self, cache):
        """测试读取不存在的键"""
        assert cache.get("nonexistent") is None
    
    def test_update_value(self, cache):
        """测试更新值"""
        cache.set("key", "old_value")
        cache.set("key", "new_value")
        assert cache.get("key") == "new_value"
    
    def test_delete(self, cache):
        """测试删除"""
        cache.set("key", "value")
        assert cache.delete("key") is True
        assert cache.get("key") is None
        assert cache.delete("key") is False
    
    def test_ttl_expiration(self, cache):
        """测试 TTL 过期"""
        cache.set("key", "value", ttl=0.1)  # 100ms
        assert cache.get("key") == "value"
        
        time.sleep(0.15)
        assert cache.get("key") is None
    
    def test_no_ttl(self, cache):
        """测试永不过期"""
        cache.set("key", "value", ttl=-1)
        assert cache.get("key") == "value"
        # 即使等待也不会过期
        time.sleep(0.1)
        assert cache.get("key") == "value"
    
    def test_lru_eviction(self):
        """测试 LRU 淘汰"""
        cache = LocalCache(max_size=3)
        
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        
        # 访问 a，使其变为最新
        cache.get("a")
        
        # 添加 d，应该淘汰 b（最久未使用）
        cache.set("d", 4)
        
        assert cache.get("a") == 1  # 还在
        assert cache.get("b") is None  # 被淘汰
        assert cache.get("c") == 3
        assert cache.get("d") == 4
    
    def test_clear(self, cache):
        """测试清空"""
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        cache.clear()
        
        assert cache.get("key1") is None
        assert cache.get("key2") is None
        assert cache.get_stats()["size"] == 0
    
    def test_get_many(self, cache):
        """测试批量获取"""
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        
        result = cache.get_many(["a", "b", "nonexistent"])
        
        assert result == {"a": 1, "b": 2}
    
    def test_set_many(self, cache):
        """测试批量设置"""
        cache.set_many({"a": 1, "b": 2, "c": 3})
        
        assert cache.get("a") == 1
        assert cache.get("b") == 2
        assert cache.get("c") == 3
    
    def test_delete_many(self, cache):
        """测试批量删除"""
        cache.set_many({"a": 1, "b": 2, "c": 3})
        
        count = cache.delete_many(["a", "b", "nonexistent"])
        
        assert count == 2
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") == 3
    
    def test_invalidate_pattern(self, cache):
        """测试模式删除"""
        cache.set("user:1", "u1")
        cache.set("user:2", "u2")
        cache.set("product:1", "p1")
        
        count = cache.invalidate_pattern("user:*")
        
        assert count == 2
        assert cache.get("user:1") is None
        assert cache.get("user:2") is None
        assert cache.get("product:1") == "p1"
    
    def test_cleanup_expired(self, cache):
        """测试清理过期"""
        cache.set("a", 1, ttl=0.1)
        cache.set("b", 2, ttl=0.1)
        cache.set("c", 3)  # 不过期
        
        time.sleep(0.15)
        
        count = cache.cleanup_expired()
        
        assert count == 2
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") == 3
    
    def test_stats(self, cache):
        """测试统计信息"""
        cache.set("a", 1)
        cache.get("a")  # hit
        cache.get("b")  # miss
        
        stats = cache.get_stats()
        
        assert stats["size"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 50.0
    
    def test_thread_safety(self, cache):
        """测试线程安全（简单测试）"""
        import threading
        
        errors = []
        
        def worker():
            try:
                for i in range(100):
                    cache.set(f"key_{i}", i)
                    cache.get(f"key_{i}")
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0


class TestCacheManager:
    """测试缓存管理器"""
    
    @pytest.fixture
    def manager(self):
        """创建测试管理器（仅 L1）"""
        return CacheManager(config=CacheConfig(l2_enabled=False))
    
    @pytest.mark.asyncio
    async def test_get_set_l1_only(self, manager):
        """测试仅 L1 读写"""
        await manager.set("key", "value")
        
        value = await manager.get("key")
        assert value == "value"
    
    @pytest.mark.asyncio
    async def test_get_nonexistent(self, manager):
        """测试读取不存在的键"""
        value = await manager.get("nonexistent")
        assert value is None
    
    @pytest.mark.asyncio
    async def test_delete(self, manager):
        """测试删除"""
        await manager.set("key", "value")
        
        success = await manager.delete("key")
        assert success is True
        
        value = await manager.get("key")
        assert value is None
    
    @pytest.mark.asyncio
    async def test_get_or_compute(self, manager):
        """测试获取或计算"""
        compute_count = 0
        
        async def compute():
            nonlocal compute_count
            compute_count += 1
            return "computed_value"
        
        # 第一次，应该计算
        value = await manager.get_or_compute("key", compute)
        assert value == "computed_value"
        assert compute_count == 1
        
        # 第二次，应该命中缓存
        value = await manager.get_or_compute("key", compute)
        assert value == "computed_value"
        assert compute_count == 1  # 没有重新计算
    
    @pytest.mark.asyncio
    async def test_get_or_compute_concurrent(self, manager):
        """测试并发获取或计算（防击穿）"""
        compute_count = 0
        
        async def compute():
            nonlocal compute_count
            await asyncio.sleep(0.1)  # 模拟慢计算
            compute_count += 1
            return "value"
        
        # 并发调用
        tasks = [
            manager.get_or_compute("key", compute)
            for _ in range(10)
        ]
        
        results = await asyncio.gather(*tasks)
        
        # 所有结果应该相同
        assert all(r == "value" for r in results)
        # 计算只应该执行一次
        assert compute_count == 1
    
    @pytest.mark.asyncio
    async def test_clear(self, manager):
        """测试清空"""
        await manager.set("key1", "value1")
        await manager.set("key2", "value2")
        
        await manager.clear()
        
        assert await manager.get("key1") is None
        assert await manager.get("key2") is None
    
    @pytest.mark.asyncio
    async def test_get_stats(self, manager):
        """测试统计"""
        await manager.set("key", "value")
        await manager.get("key")
        
        stats = await manager.get_stats()
        
        assert stats["l1_enabled"] is True
        assert stats["l2_enabled"] is False
        assert stats["strategy"] == "l1_l2"
    
    @pytest.mark.asyncio
    async def test_cached_decorator(self, manager):
        """测试缓存装饰器"""
        call_count = 0
        
        @manager.cached(key_prefix="test", l1_ttl=300)
        async def expensive_function(x, y):
            nonlocal call_count
            call_count += 1
            return x + y
        
        # 第一次调用
        result = await expensive_function(1, 2)
        assert result == 3
        assert call_count == 1
        
        # 第二次调用（相同参数），应该命中缓存
        result = await expensive_function(1, 2)
        assert result == 3
        assert call_count == 1  # 没有重新调用
        
        # 不同参数
        result = await expensive_function(2, 3)
        assert result == 5
        assert call_count == 2


class TestCacheStrategy:
    """测试缓存策略"""
    
    def test_search_cache_strategy(self):
        """测试搜索缓存策略"""
        from bridge.cache.manager import SEARCH_CACHE_STRATEGY
        
        assert SEARCH_CACHE_STRATEGY.l1_enabled is True
        assert SEARCH_CACHE_STRATEGY.l1_default_ttl == 300
        assert SEARCH_CACHE_STRATEGY.l2_default_ttl == 1800
    
    def test_analytics_cache_strategy(self):
        """测试分析缓存策略"""
        from bridge.cache.manager import ANALYTICS_CACHE_STRATEGY
        
        assert ANALYTICS_CACHE_STRATEGY.l1_enabled is True
        assert ANALYTICS_CACHE_STRATEGY.l2_default_ttl == 3600
    
    def test_ontology_cache_strategy(self):
        """测试本体缓存策略"""
        from bridge.cache.manager import ONTOLOGY_CACHE_STRATEGY
        
        assert ONTOLOGY_CACHE_STRATEGY.l1_enabled is True
        assert ONTOLOGY_CACHE_STRATEGY.l1_default_ttl == 0  # 永不过期
        assert ONTOLOGY_CACHE_STRATEGY.l2_default_ttl == 86400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
