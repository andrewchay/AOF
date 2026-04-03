"""Redis 缓存

基于 Redis 的分布式缓存实现。
适用于：
- 多进程/多节点部署
- 需要共享缓存的场景
- 缓存持久化需求
"""

from __future__ import annotations

import json
import pickle
import logging
from typing import Optional, Any, Dict, List, Union
from datetime import timedelta

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis 缓存客户端
    
    特性：
    - 支持异步操作
    - 自动序列化（JSON/pickle）
    - 连接池管理
    - 批量操作
    
    使用示例:
        cache = RedisCache.from_url("redis://localhost:6379/0")
        await cache.set("key", value, ttl=300)
        value = await cache.get("key")
    """
    
    def __init__(
        self,
        client: Any,  # redis.Redis 实例
        key_prefix: str = "aof:",
        default_ttl: int = 300,
        serializer: str = "json",  # 'json' 或 'pickle'
    ):
        """
        Args:
            client: Redis 客户端实例
            key_prefix: 键前缀（用于命名空间隔离）
            default_ttl: 默认过期时间（秒）
            serializer: 序列化方式
        """
        if not REDIS_AVAILABLE:
            raise ImportError("redis package is required. Install with: pip install redis")
        
        self.client = client
        self.key_prefix = key_prefix
        self.default_ttl = default_ttl
        self.serializer = serializer
        
        # 统计
        self._hits = 0
        self._misses = 0
    
    @classmethod
    async def from_url(
        cls,
        url: str = "redis://localhost:6379/0",
        key_prefix: str = "aof:",
        **kwargs
    ) -> "RedisCache":
        """从 URL 创建缓存实例
        
        Args:
            url: Redis 连接 URL
            key_prefix: 键前缀
            **kwargs: 传递给 redis.from_url 的参数
        """
        if not REDIS_AVAILABLE:
            raise ImportError("redis package is required")
        
        client = await redis.from_url(url, **kwargs)
        return cls(client, key_prefix=key_prefix)
    
    @classmethod
    def from_client(
        cls,
        client: Any,
        key_prefix: str = "aof:",
    ) -> "RedisCache":
        """从已有客户端创建"""
        return cls(client, key_prefix=key_prefix)
    
    def _make_key(self, key: str) -> str:
        """生成带前缀的键"""
        return f"{self.key_prefix}{key}"
    
    def _serialize(self, value: Any) -> Union[str, bytes]:
        """序列化值"""
        if self.serializer == "json":
            return json.dumps(value, default=str)
        else:
            return pickle.dumps(value)
    
    def _deserialize(self, data: Union[str, bytes]) -> Any:
        """反序列化值"""
        if data is None:
            return None
        
        if self.serializer == "json":
            if isinstance(data, bytes):
                data = data.decode('utf-8')
            return json.loads(data)
        else:
            if isinstance(data, str):
                data = data.encode('utf-8')
            return pickle.loads(data)
    
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        try:
            data = await self.client.get(self._make_key(key))
            
            if data is None:
                self._misses += 1
                return None
            
            self._hits += 1
            return self._deserialize(data)
            
        except Exception as e:
            logger.error(f"Redis get error: {e}")
            self._misses += 1
            return None
    
    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        nx: bool = False,  # 仅当键不存在时才设置
        xx: bool = False,  # 仅当键存在时才设置
    ) -> bool:
        """设置缓存值
        
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒），None 表示使用默认值
            nx: Not Exists，仅当键不存在时才设置
            xx: Exists，仅当键存在时才设置
        
        Returns:
            是否设置成功
        """
        try:
            serialized = self._serialize(value)
            
            expire = ttl if ttl is not None else self.default_ttl
            
            result = await self.client.set(
                self._make_key(key),
                serialized,
                ex=expire if expire > 0 else None,
                nx=nx,
                xx=xx,
            )
            
            return result is not None
            
        except Exception as e:
            logger.error(f"Redis set error: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """删除缓存值"""
        try:
            result = await self.client.delete(self._make_key(key))
            return result > 0
        except Exception as e:
            logger.error(f"Redis delete error: {e}")
            return False
    
    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        try:
            result = await self.client.exists(self._make_key(key))
            return result > 0
        except Exception as e:
            logger.error(f"Redis exists error: {e}")
            return False
    
    async def ttl(self, key: str) -> int:
        """获取剩余过期时间（秒）
        
        Returns:
            -2: 键不存在
            -1: 键存在但没有设置过期时间
            >=0: 剩余秒数
        """
        try:
            return await self.client.ttl(self._make_key(key))
        except Exception as e:
            logger.error(f"Redis ttl error: {e}")
            return -2
    
    async def expire(self, key: str, ttl: int) -> bool:
        """设置/更新过期时间"""
        try:
            return await self.client.expire(self._make_key(key), ttl)
        except Exception as e:
            logger.error(f"Redis expire error: {e}")
            return False
    
    async def get_many(self, keys: List[str]) -> Dict[str, Any]:
        """批量获取"""
        if not keys:
            return {}
        
        try:
            prefixed_keys = [self._make_key(k) for k in keys]
            values = await self.client.mget(prefixed_keys)
            
            result = {}
            for key, value in zip(keys, values):
                if value is not None:
                    result[key] = self._deserialize(value)
                    self._hits += 1
                else:
                    self._misses += 1
            
            return result
            
        except Exception as e:
            logger.error(f"Redis mget error: {e}")
            self._misses += len(keys)
            return {}
    
    async def set_many(
        self,
        mapping: Dict[str, Any],
        ttl: Optional[int] = None
    ) -> bool:
        """批量设置"""
        if not mapping:
            return True
        
        try:
            # MSET 不支持 TTL，需要单独设置
            pipe = self.client.pipeline()
            
            for key, value in mapping.items():
                serialized = self._serialize(value)
                pipe.set(self._make_key(key), serialized)
            
            await pipe.execute()
            
            # 设置过期时间
            if ttl is not None and ttl > 0:
                pipe = self.client.pipeline()
                for key in mapping.keys():
                    pipe.expire(self._make_key(key), ttl)
                await pipe.execute()
            
            return True
            
        except Exception as e:
            logger.error(f"Redis mset error: {e}")
            return False
    
    async def delete_many(self, keys: List[str]) -> int:
        """批量删除"""
        if not keys:
            return 0
        
        try:
            prefixed_keys = [self._make_key(k) for k in keys]
            return await self.client.delete(*prefixed_keys)
        except Exception as e:
            logger.error(f"Redis delete many error: {e}")
            return 0
    
    async def keys(self, pattern: str = "*") -> List[str]:
        """按模式查找键"""
        try:
            full_pattern = self._make_key(pattern)
            keys = await self.client.keys(full_pattern)
            
            # 移除前缀
            prefix_len = len(self.key_prefix)
            return [k[prefix_len:] if k.startswith(self.key_prefix) else k 
                    for k in keys]
        except Exception as e:
            logger.error(f"Redis keys error: {e}")
            return []
    
    async def invalidate_pattern(self, pattern: str) -> int:
        """按模式删除缓存"""
        try:
            keys = await self.keys(pattern)
            if keys:
                return await self.delete_many(keys)
            return 0
        except Exception as e:
            logger.error(f"Redis invalidate pattern error: {e}")
            return 0
    
    async def clear(self) -> bool:
        """清空缓存（谨慎使用）"""
        try:
            # 只删除带前缀的键
            keys = await self.keys("*")
            if keys:
                await self.delete_many(keys)
            logger.info("Redis cache cleared")
            return True
        except Exception as e:
            logger.error(f"Redis clear error: {e}")
            return False
    
    async def increment(self, key: str, amount: int = 1) -> Optional[int]:
        """原子递增"""
        try:
            return await self.client.incrby(self._make_key(key), amount)
        except Exception as e:
            logger.error(f"Redis increment error: {e}")
            return None
    
    async def decrement(self, key: str, amount: int = 1) -> Optional[int]:
        """原子递减"""
        try:
            return await self.client.decrby(self._make_key(key), amount)
        except Exception as e:
            logger.error(f"Redis decrement error: {e}")
            return None
    
    async def get_or_compute(
        self,
        key: str,
        compute_func: callable,
        ttl: Optional[int] = None,
    ) -> Any:
        """获取或计算
        
        缓存未命中时自动计算并缓存结果。
        
        Args:
            key: 缓存键
            compute_func: 计算函数（异步）
            ttl: 过期时间
        """
        # 尝试获取缓存
        value = await self.get(key)
        if value is not None:
            return value
        
        # 计算值
        value = await compute_func()
        
        # 缓存结果
        if value is not None:
            await self.set(key, value, ttl=ttl)
        
        return value
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        try:
            info = await self.client.info()
            
            total_requests = self._hits + self._misses
            hit_rate = self._hits / total_requests if total_requests > 0 else 0
            
            return {
                "redis_version": info.get("redis_version", "unknown"),
                "used_memory_human": info.get("used_memory_human", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate * 100, 2),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
            }
        except Exception as e:
            logger.error(f"Redis stats error: {e}")
            return {"error": str(e)}
    
    async def close(self) -> None:
        """关闭连接"""
        try:
            await self.client.close()
        except Exception as e:
            logger.error(f"Redis close error: {e}")


# 便捷函数

async def create_redis_cache(
    host: str = "localhost",
    port: int = 6379,
    db: int = 0,
    password: Optional[str] = None,
    key_prefix: str = "aof:",
) -> RedisCache:
    """创建 Redis 缓存实例"""
    url = f"redis://{host}:{port}/{db}"
    if password:
        url = f"redis://:{password}@{host}:{port}/{db}"
    
    return await RedisCache.from_url(url, key_prefix=key_prefix)
