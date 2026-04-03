"""本地 LRU 缓存

基于 OrderedDict 实现的线程安全 LRU 缓存。
适用于：
- 单进程部署
- 频繁访问的热数据
- 对延迟敏感的场景
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Optional, Any, Callable, Dict
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目"""
    value: Any
    expire_at: Optional[float] = None  # 过期时间戳（None 表示永不过期）
    
    def is_expired(self) -> bool:
        if self.expire_at is None:
            return False
        return time.time() > self.expire_at


class LocalCache:
    """线程安全的 LRU 缓存
    
    特性：
    - 自动过期（TTL）
    - LRU 淘汰
    - 线程安全
    - 命中统计
    
    使用示例:
        cache = LocalCache(max_size=1000)
        cache.set("key", value, ttl=300)
        value = cache.get("key")
    """
    
    def __init__(self, max_size: int = 10000, default_ttl: Optional[int] = 300):
        """
        Args:
            max_size: 最大缓存条目数
            default_ttl: 默认过期时间（秒），None 表示永不过期
        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        
        # 统计
        self._hits = 0
        self._misses = 0
        self._evictions = 0
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存值
        
        Returns:
            缓存值，如果不存在或已过期则返回 None
        """
        with self._lock:
            entry = self._cache.get(key)
            
            if entry is None:
                self._misses += 1
                return None
            
            if entry.is_expired():
                del self._cache[key]
                self._misses += 1
                return None
            
            # LRU：移到末尾（最新使用）
            self._cache.move_to_end(key)
            self._hits += 1
            return entry.value
    
    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None
    ) -> None:
        """设置缓存值
        
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒），None 表示使用默认值，-1 表示永不过期
        """
        with self._lock:
            # 计算过期时间
            if ttl == -1:
                expire_at = None
            elif ttl is not None:
                expire_at = time.time() + ttl
            elif self.default_ttl is not None:
                expire_at = time.time() + self.default_ttl
            else:
                expire_at = None
            
            # 创建条目
            entry = CacheEntry(value=value, expire_at=expire_at)
            
            # 如果 key 已存在，先删除（再添加会移到末尾）
            if key in self._cache:
                del self._cache[key]
            
            # 检查是否需要淘汰
            while len(self._cache) >= self.max_size:
                self._evict_lru()
            
            self._cache[key] = entry
    
    def delete(self, key: str) -> bool:
        """删除缓存值
        
        Returns:
            是否成功删除
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
            logger.info("Local cache cleared")
    
    def get_many(self, keys: list[str]) -> Dict[str, Any]:
        """批量获取"""
        result = {}
        for key in keys:
            value = self.get(key)
            if value is not None:
                result[key] = value
        return result
    
    def set_many(
        self,
        mapping: Dict[str, Any],
        ttl: Optional[int] = None
    ) -> None:
        """批量设置"""
        for key, value in mapping.items():
            self.set(key, value, ttl)
    
    def delete_many(self, keys: list[str]) -> int:
        """批量删除，返回删除数量"""
        count = 0
        for key in keys:
            if self.delete(key):
                count += 1
        return count
    
    def keys(self, pattern: Optional[str] = None) -> list[str]:
        """获取所有键（支持简单通配符 *）"""
        with self._lock:
            keys = list(self._cache.keys())
            
            if pattern:
                import fnmatch
                keys = [k for k in keys if fnmatch.fnmatch(k, pattern)]
            
            return keys
    
    def invalidate_pattern(self, pattern: str) -> int:
        """按模式删除缓存
        
        Args:
            pattern: 通配符模式，如 "user:*"
        
        Returns:
            删除的条目数
        """
        import fnmatch
        
        with self._lock:
            keys_to_delete = [
                k for k in self._cache.keys()
                if fnmatch.fnmatch(k, pattern)
            ]
            
            for key in keys_to_delete:
                del self._cache[key]
            
            return len(keys_to_delete)
    
    def _evict_lru(self) -> None:
        """淘汰最久未使用的条目"""
        if not self._cache:
            return
        
        # 删除第一个（最旧）
        oldest_key = next(iter(self._cache))
        del self._cache[oldest_key]
        self._evictions += 1
    
    def cleanup_expired(self) -> int:
        """清理过期条目
        
        Returns:
            清理的条目数
        """
        with self._lock:
            expired_keys = [
                k for k, v in self._cache.items()
                if v.is_expired()
            ]
            
            for key in expired_keys:
                del self._cache[key]
            
            return len(expired_keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = self._hits / total_requests if total_requests > 0 else 0
            
            # 统计过期条目
            expired_count = sum(
                1 for v in self._cache.values() if v.is_expired()
            )
            
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate * 100, 2),
                "evictions": self._evictions,
                "expired_entries": expired_count,
                "utilization": round(len(self._cache) / self.max_size * 100, 2),
            }
    
    def get_cache_info(self) -> Dict[str, Any]:
        """获取缓存详细信息"""
        with self._lock:
            now = time.time()
            entries_info = []
            
            for key, entry in self._cache.items():
                ttl_remaining = None
                if entry.expire_at:
                    ttl_remaining = max(0, entry.expire_at - now)
                
                entries_info.append({
                    "key": key,
                    "ttl_remaining": ttl_remaining,
                    "size_bytes": len(str(entry.value)),  # 粗略估计
                })
            
            return {
                "entries": entries_info[:100],  # 最多返回 100 条
                "total_entries": len(self._cache),
            }


# 全局单例（可选）
_global_cache: Optional[LocalCache] = None
_global_cache_lock = threading.Lock()


def get_global_cache(max_size: int = 10000) -> LocalCache:
    """获取全局缓存实例"""
    global _global_cache
    
    with _global_cache_lock:
        if _global_cache is None:
            _global_cache = LocalCache(max_size=max_size)
        return _global_cache


def clear_global_cache() -> None:
    """清空全局缓存"""
    global _global_cache
    
    with _global_cache_lock:
        if _global_cache:
            _global_cache.clear()
