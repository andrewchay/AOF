# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""缓存管理器

协调多级缓存（L1 本地 + L2 Redis）：
- L1: 进程内 LRU（最快，不共享）
- L2: Redis（共享，跨进程）

缓存策略：
- 读取：L1 -> L2 -> 数据源
- 写入：数据源 -> L2 -> L1
- 删除：L1 + L2 同时删除
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Any, Dict, Callable, List
from enum import Enum

from .local_cache import LocalCache
from .redis_cache import RedisCache

logger = logging.getLogger(__name__)


class CacheStrategy(Enum):
    """缓存策略"""
    L1_ONLY = "l1_only"       # 仅本地缓存
    L2_ONLY = "l2_only"       # 仅 Redis
    L1_L2 = "l1_l2"           # 两级缓存（默认）


@dataclass
class CacheConfig:
    """缓存配置"""
    # L1 配置
    l1_enabled: bool = True
    l1_max_size: int = 10000
    l1_default_ttl: int = 300
    
    # L2 配置
    l2_enabled: bool = True
    l2_default_ttl: int = 600
    l2_key_prefix: str = "aof:"
    
    # 策略配置
    strategy: CacheStrategy = CacheStrategy.L1_L2
    
    # 回源配置
    lock_timeout: float = 10.0  # 防止缓存击穿锁超时


class CacheManager:
    """缓存管理器
    
    统一接口管理多级缓存：
    
    使用示例:
        # 初始化
        cache = CacheManager(
            l1_cache=LocalCache(),
            l2_cache=await RedisCache.from_url("redis://localhost"),
        )
        
        # 读取或计算
        result = await cache.get_or_compute(
            key="user:123",
            compute_func=fetch_from_db,
            l1_ttl=300,
            l2_ttl=600,
        )
        
        # 删除
        await cache.delete("user:123")
        
        # 按模式删除
        await cache.invalidate_pattern("user:*")
    """
    
    def __init__(
        self,
        l1_cache: Optional[LocalCache] = None,
        l2_cache: Optional[RedisCache] = None,
        config: Optional[CacheConfig] = None,
    ):
        self.config = config or CacheConfig()
        
        # 初始化 L1
        if self.config.l1_enabled and l1_cache is None:
            self.l1 = LocalCache(
                max_size=self.config.l1_max_size,
                default_ttl=self.config.l1_default_ttl,
            )
        else:
            self.l1 = l1_cache if self.config.l1_enabled else None
        
        # 初始化 L2
        self.l2 = l2_cache if self.config.l2_enabled else None
        
        # 防击穿锁
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_lock = asyncio.Lock()
    
    async def get(
        self,
        key: str,
        strategy: Optional[CacheStrategy] = None
    ) -> Optional[Any]:
        """获取缓存值
        
        读取顺序: L1 -> L2
        
        Args:
            key: 缓存键
            strategy: 覆盖默认策略
        """
        strat = strategy or self.config.strategy
        
        # L1 读取
        if strat in (CacheStrategy.L1_ONLY, CacheStrategy.L1_L2) and self.l1:
            value = self.l1.get(key)
            if value is not None:
                return value
        
        # L2 读取
        if strat in (CacheStrategy.L2_ONLY, CacheStrategy.L1_L2) and self.l2:
            value = await self.l2.get(key)
            if value is not None:
                # 回填 L1
                if self.l1 and strat == CacheStrategy.L1_L2:
                    self.l1.set(key, value)
                return value
        
        return None
    
    async def set(
        self,
        key: str,
        value: Any,
        l1_ttl: Optional[int] = None,
        l2_ttl: Optional[int] = None,
        strategy: Optional[CacheStrategy] = None,
    ) -> bool:
        """设置缓存值
        
        写入顺序: L2 -> L1
        
        Args:
            key: 缓存键
            value: 缓存值
            l1_ttl: L1 过期时间（秒）
            l2_ttl: L2 过期时间（秒）
            strategy: 覆盖默认策略
        """
        strat = strategy or self.config.strategy
        success = True
        
        # L2 写入
        if strat in (CacheStrategy.L2_ONLY, CacheStrategy.L1_L2) and self.l2:
            success = await self.l2.set(key, value, ttl=l2_ttl) and success
        
        # L1 写入
        if strat in (CacheStrategy.L1_ONLY, CacheStrategy.L1_L2) and self.l1:
            self.l1.set(key, value, ttl=l1_ttl)
        
        return success
    
    async def delete(
        self,
        key: str,
        strategy: Optional[CacheStrategy] = None
    ) -> bool:
        """删除缓存值"""
        strat = strategy or self.config.strategy
        success = True
        
        if strat in (CacheStrategy.L1_ONLY, CacheStrategy.L1_L2) and self.l1:
            self.l1.delete(key)
        
        if strat in (CacheStrategy.L2_ONLY, CacheStrategy.L1_L2) and self.l2:
            success = await self.l2.delete(key) and success
        
        return success
    
    async def get_or_compute(
        self,
        key: str,
        compute_func: Callable[[], Any],
        l1_ttl: Optional[int] = None,
        l2_ttl: Optional[int] = None,
        strategy: Optional[CacheStrategy] = None,
        prevent_thundering_herd: bool = True,
    ) -> Any:
        """获取或计算（带防击穿）
        
        如果缓存未命中，调用 compute_func 计算值并缓存。
        支持防缓存击穿（防止高并发下同时回源）。
        
        Args:
            key: 缓存键
            compute_func: 计算函数（异步）
            l1_ttl: L1 过期时间
            l2_ttl: L2 过期时间
            strategy: 缓存策略
            prevent_thundering_herd: 是否防击穿
        """
        # 尝试读取缓存
        value = await self.get(key, strategy)
        if value is not None:
            return value
        
        if prevent_thundering_herd:
            # 获取锁，防止并发回源
            lock = await self._get_lock(key)
            
            async with lock:
                # 双重检查（其他协程可能已写入）
                value = await self.get(key, strategy)
                if value is not None:
                    return value
                
                # 执行计算
                value = await compute_func()
                
                # 写入缓存
                if value is not None:
                    await self.set(key, value, l1_ttl, l2_ttl, strategy)
                
                return value
        else:
            # 直接计算
            value = await compute_func()
            
            if value is not None:
                await self.set(key, value, l1_ttl, l2_ttl, strategy)
            
            return value
    
    async def get_many(
        self,
        keys: List[str],
        strategy: Optional[CacheStrategy] = None
    ) -> Dict[str, Any]:
        """批量获取"""
        result = {}
        
        for key in keys:
            value = await self.get(key, strategy)
            if value is not None:
                result[key] = value
        
        return result
    
    async def set_many(
        self,
        mapping: Dict[str, Any],
        l1_ttl: Optional[int] = None,
        l2_ttl: Optional[int] = None,
        strategy: Optional[CacheStrategy] = None,
    ) -> bool:
        """批量设置"""
        strat = strategy or self.config.strategy
        success = True
        
        # L1 批量写入
        if strat in (CacheStrategy.L1_ONLY, CacheStrategy.L1_L2) and self.l1:
            self.l1.set_many(mapping, ttl=l1_ttl)
        
        # L2 批量写入
        if strat in (CacheStrategy.L2_ONLY, CacheStrategy.L1_L2) and self.l2:
            success = await self.l2.set_many(mapping, ttl=l2_ttl) and success
        
        return success
    
    async def delete_many(
        self,
        keys: List[str],
        strategy: Optional[CacheStrategy] = None
    ) -> int:
        """批量删除"""
        strat = strategy or self.config.strategy
        count = 0
        
        for key in keys:
            if await self.delete(key, strat):
                count += 1
        
        return count
    
    async def invalidate_pattern(
        self,
        pattern: str,
        strategy: Optional[CacheStrategy] = None
    ) -> int:
        """按模式删除缓存"""
        strat = strategy or self.config.strategy
        count = 0
        
        if strat in (CacheStrategy.L1_ONLY, CacheStrategy.L1_L2) and self.l1:
            count += self.l1.invalidate_pattern(pattern)
        
        if strat in (CacheStrategy.L2_ONLY, CacheStrategy.L1_L2) and self.l2:
            count += await self.l2.invalidate_pattern(pattern)
        
        logger.info(f"Invalidated {count} keys matching pattern: {pattern}")
        return count
    
    async def clear(self, strategy: Optional[CacheStrategy] = None) -> bool:
        """清空缓存"""
        strat = strategy or self.config.strategy
        success = True
        
        if strat in (CacheStrategy.L1_ONLY, CacheStrategy.L1_L2) and self.l1:
            self.l1.clear()
        
        if strat in (CacheStrategy.L2_ONLY, CacheStrategy.L1_L2) and self.l2:
            success = await self.l2.clear() and success
        
        return success
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = {
            "strategy": self.config.strategy.value,
            "l1_enabled": self.l1 is not None,
            "l2_enabled": self.l2 is not None,
        }
        
        if self.l1:
            stats["l1"] = self.l1.get_stats()
        
        if self.l2:
            stats["l2"] = await self.l2.get_stats()
        
        return stats
    
    async def _get_lock(self, key: str) -> asyncio.Lock:
        """获取防击穿锁"""
        async with self._lock_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]
    
    # ========== 便捷装饰器 ==========
    
    def cached(
        self,
        key_prefix: str = "",
        l1_ttl: Optional[int] = None,
        l2_ttl: Optional[int] = None,
        strategy: Optional[CacheStrategy] = None,
        key_builder: Optional[Callable] = None,
    ):
        """缓存装饰器
        
        使用示例:
            @cache.cached(key_prefix="user", l1_ttl=300)
            async def get_user(user_id: str):
                return await fetch_user_from_db(user_id)
        """
        def decorator(func):
            async def wrapper(*args, **kwargs):
                # 构建缓存键
                if key_builder:
                    cache_key = key_builder(*args, **kwargs)
                else:
                    # 默认：前缀 + 函数名 + 参数哈希
                    import hashlib
                    args_str = str(args) + str(sorted(kwargs.items()))
                    args_hash = hashlib.md5(args_str.encode()).hexdigest()[:8]
                    cache_key = f"{key_prefix}:{func.__name__}:{args_hash}"
                
                # 尝试获取缓存
                value = await self.get(cache_key, strategy)
                if value is not None:
                    return value
                
                # 执行函数
                value = await func(*args, **kwargs)
                
                # 写入缓存
                if value is not None:
                    await self.set(cache_key, value, l1_ttl, l2_ttl, strategy)
                
                return value
            
            # 添加清除缓存的方法
            async def invalidate(*args, **kwargs):
                if key_builder:
                    cache_key = key_builder(*args, **kwargs)
                else:
                    import hashlib
                    args_str = str(args) + str(sorted(kwargs.items()))
                    args_hash = hashlib.md5(args_str.encode()).hexdigest()[:8]
                    cache_key = f"{key_prefix}:{func.__name__}:{args_hash}"
                await self.delete(cache_key)
            
            wrapper.invalidate = invalidate
            return wrapper
        return decorator


# ========== 预定义缓存策略 ==========

# 搜索结果的缓存策略
SEARCH_CACHE_STRATEGY = CacheConfig(
    l1_enabled=True,
    l1_max_size=1000,
    l1_default_ttl=300,  # 5 分钟
    l2_enabled=True,
    l2_default_ttl=1800,  # 30 分钟
)

# 图谱分析结果的缓存策略
ANALYTICS_CACHE_STRATEGY = CacheConfig(
    l1_enabled=True,
    l1_max_size=500,
    l1_default_ttl=600,  # 10 分钟
    l2_enabled=True,
    l2_default_ttl=3600,  # 1 小时
)

# 本体配置的缓存策略（很少变化）
ONTOLOGY_CACHE_STRATEGY = CacheConfig(
    l1_enabled=True,
    l1_max_size=100,
    l1_default_ttl=0,  # 永不过期（L1）
    l2_enabled=True,
    l2_default_ttl=86400,  # 1 天
)
