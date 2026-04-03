"""限流器

提供多种限流算法：
- 令牌桶（Token Bucket）：平滑流量，允许突发
- 滑动窗口（Sliding Window）：精确计数

支持：
- 内存存储（单机）
- Redis 存储（分布式）
"""

from __future__ import annotations

import time
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum
import logging

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """限流异常"""
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s")


@dataclass
class RateLimitResult:
    """限流结果"""
    allowed: bool
    remaining: int
    reset_time: float  # 重置时间戳
    retry_after: Optional[float] = None  # 需要等待的秒数


class RateLimiterBackend(ABC):
    """限流后端抽象"""
    
    @abstractmethod
    async def check(self, key: str, rate: int, per: int) -> RateLimitResult:
        """检查是否允许请求"""
        pass


class TokenBucket:
    """令牌桶算法
    
    特性：
    - 允许突发流量（桶容量）
    - 平滑输出（恒定速率填充）
    - 适合：API 限流、带宽控制
    
    使用示例:
        bucket = TokenBucket(capacity=10, refill_rate=1)  # 10个令牌，每秒填充1个
        if await bucket.consume("user_123", tokens=1):
            # 处理请求
    """
    
    def __init__(
        self,
        capacity: int = 10,
        refill_rate: float = 1.0,  # 每秒填充令牌数
        redis_client: Optional[Any] = None,
    ):
        """
        Args:
            capacity: 桶容量（最大突发流量）
            refill_rate: 填充速率（令牌/秒）
            redis_client: Redis 客户端（分布式场景）
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.redis = redis_client
        
        # 本地存储（单机）
        self._buckets: Dict[str, Dict[str, float]] = {}
        self._lock = asyncio.Lock()
    
    async def consume(
        self,
        key: str,
        tokens: int = 1
    ) -> tuple[bool, Dict[str, Any]]:
        """消费令牌
        
        Returns:
            (是否允许, 附加信息)
        """
        if self.redis:
            return await self._consume_redis(key, tokens)
        else:
            return await self._consume_local(key, tokens)
    
    async def _consume_local(self, key: str, tokens: int) -> tuple[bool, Dict[str, Any]]:
        """本地消费"""
        async with self._lock:
            now = time.time()
            
            # 获取或创建桶
            if key not in self._buckets:
                self._buckets[key] = {
                    "tokens": self.capacity,
                    "last_update": now,
                }
            
            bucket = self._buckets[key]
            
            # 计算新令牌数
            elapsed = now - bucket["last_update"]
            new_tokens = elapsed * self.refill_rate
            bucket["tokens"] = min(self.capacity, bucket["tokens"] + new_tokens)
            bucket["last_update"] = now
            
            # 检查是否足够
            if bucket["tokens"] >= tokens:
                bucket["tokens"] -= tokens
                return True, {
                    "remaining": int(bucket["tokens"]),
                    "reset_time": now + (self.capacity - bucket["tokens"]) / self.refill_rate,
                }
            else:
                retry_after = (tokens - bucket["tokens"]) / self.refill_rate
                return False, {
                    "remaining": 0,
                    "reset_time": now + retry_after,
                    "retry_after": retry_after,
                }
    
    async def _consume_redis(self, key: str, tokens: int) -> tuple[bool, Dict[str, Any]]:
        """Redis 消费（分布式）"""
        # Lua 脚本保证原子性
        lua_script = """
        local key = KEYS[1]
        local capacity = tonumber(ARGV[1])
        local refill_rate = tonumber(ARGV[2])
        local tokens_requested = tonumber(ARGV[3])
        local now = tonumber(ARGV[4])
        
        -- 获取当前状态
        local bucket = redis.call('HMGET', key, 'tokens', 'last_update')
        local current_tokens = tonumber(bucket[1]) or capacity
        local last_update = tonumber(bucket[2]) or now
        
        -- 计算新令牌数
        local elapsed = now - last_update
        local new_tokens = math.min(capacity, current_tokens + elapsed * refill_rate)
        
        -- 检查并更新
        if new_tokens >= tokens_requested then
            new_tokens = new_tokens - tokens_requested
            redis.call('HMSET', key, 'tokens', new_tokens, 'last_update', now)
            redis.call('EXPIRE', key, 3600)  -- 1小时过期
            return {1, new_tokens, 0}
        else
            local retry_after = (tokens_requested - new_tokens) / refill_rate
            redis.call('HMSET', key, 'tokens', new_tokens, 'last_update', now)
            redis.call('EXPIRE', key, 3600)
            return {0, new_tokens, retry_after}
        end
        """
        
        now = time.time()
        result = await self.redis.eval(
            lua_script,
            1,  # key 数量
            f"token_bucket:{key}",
            self.capacity,
            self.refill_rate,
            tokens,
            now
        )
        
        allowed = result[0] == 1
        remaining = int(result[1])
        retry_after = float(result[2]) if result[2] > 0 else None
        
        return allowed, {
            "remaining": remaining,
            "reset_time": now + (self.capacity - remaining) / self.refill_rate,
            "retry_after": retry_after,
        }


class SlidingWindow:
    """滑动窗口算法
    
    特性：
    - 精确计数（无突发）
    - 时间窗口平滑滑动
    - 适合：严格限流、配额管理
    
    使用示例:
        window = SlidingWindow(window_size=60, max_requests=100)  # 每分钟100次
        if await window.allow("user_123"):
            # 处理请求
    """
    
    def __init__(
        self,
        window_size: int = 60,  # 窗口大小（秒）
        max_requests: int = 100,
        redis_client: Optional[Any] = None,
    ):
        self.window_size = window_size
        self.max_requests = max_requests
        self.redis = redis_client
        
        # 本地存储
        self._windows: Dict[str, list] = {}
        self._lock = asyncio.Lock()
    
    async def allow(self, key: str) -> tuple[bool, Dict[str, Any]]:
        """检查是否允许请求"""
        now = time.time()
        window_start = now - self.window_size
        
        if self.redis:
            return await self._allow_redis(key, now, window_start)
        else:
            return await self._allow_local(key, now, window_start)
    
    async def _allow_local(
        self,
        key: str,
        now: float,
        window_start: float
    ) -> tuple[bool, Dict[str, Any]]:
        """本地检查"""
        async with self._lock:
            # 获取或创建窗口
            if key not in self._windows:
                self._windows[key] = []
            
            window = self._windows[key]
            
            # 清理过期请求
            self._windows[key] = [t for t in window if t > window_start]
            
            # 检查是否超过限制
            if len(self._windows[key]) < self.max_requests:
                self._windows[key].append(now)
                remaining = self.max_requests - len(self._windows[key])
                reset_time = self._windows[key][0] + self.window_size if self._windows[key] else now + self.window_size
                return True, {
                    "remaining": remaining,
                    "reset_time": reset_time,
                }
            else:
                # 计算重置时间
                reset_time = self._windows[key][0] + self.window_size
                retry_after = reset_time - now
                return False, {
                    "remaining": 0,
                    "reset_time": reset_time,
                    "retry_after": max(0, retry_after),
                }
    
    async def _allow_redis(
        self,
        key: str,
        now: float,
        window_start: float
    ) -> tuple[bool, Dict[str, Any]]:
        """Redis 检查（分布式）"""
        redis_key = f"sliding_window:{key}"
        
        # 使用 pipeline 保证原子性
        pipe = self.redis.pipeline()
        
        # 移除过期成员
        pipe.zremrangebyscore(redis_key, 0, window_start)
        
        # 获取当前数量
        pipe.zcard(redis_key)
        
        results = await pipe.execute()
        current_count = results[1]
        
        if current_count < self.max_requests:
            # 添加当前请求
            await self.redis.zadd(redis_key, {str(now): now})
            await self.redis.expire(redis_key, self.window_size)
            
            remaining = self.max_requests - current_count - 1
            return True, {
                "remaining": remaining,
                "reset_time": now + self.window_size,
            }
        else:
            # 获取最早请求的过期时间
            oldest = await self.redis.zrange(redis_key, 0, 0, withscores=True)
            reset_time = oldest[0][1] + self.window_size if oldest else now + self.window_size
            retry_after = reset_time - now
            
            return False, {
                "remaining": 0,
                "reset_time": reset_time,
                "retry_after": max(0, retry_after),
            }


class RateLimiter:
    """限流器（统一接口）
    
    根据配置自动选择算法：
    - token_bucket: 令牌桶（允许突发）
    - sliding_window: 滑动窗口（精确计数）
    """
    
    def __init__(
        self,
        algorithm: str = "token_bucket",
        rate: int = 100,  # 请求数
        per: int = 60,    # 时间窗口（秒）
        burst: Optional[int] = None,  # 突发容量（仅令牌桶）
        redis_client: Optional[Any] = None,
    ):
        """
        Args:
            algorithm: 算法类型（token_bucket/sliding_window）
            rate: 速率（每 per 秒的请求数）
            per: 时间窗口（秒）
            burst: 突发容量（令牌桶专用）
            redis_client: Redis 客户端
        """
        self.algorithm = algorithm
        self.rate = rate
        self.per = per
        
        if algorithm == "token_bucket":
            capacity = burst or rate
            refill_rate = rate / per
            self._impl = TokenBucket(
                capacity=capacity,
                refill_rate=refill_rate,
                redis_client=redis_client,
            )
        elif algorithm == "sliding_window":
            self._impl = SlidingWindow(
                window_size=per,
                max_requests=rate,
                redis_client=redis_client,
            )
        else:
            raise ValueError(f"Unknown algorithm: {algorithm}")
    
    async def allow(self, key: str) -> bool:
        """检查是否允许请求
        
        Returns:
            是否允许
        """
        if self.algorithm == "token_bucket":
            allowed, _ = await self._impl.consume(key)
        else:
            allowed, _ = await self._impl.allow(key)
        
        return allowed
    
    async def check(self, key: str) -> RateLimitResult:
        """详细检查
        
        Returns:
            详细的限流结果
        """
        if self.algorithm == "token_bucket":
            allowed, info = await self._impl.consume(key)
        else:
            allowed, info = await self._impl.allow(key)
        
        return RateLimitResult(
            allowed=allowed,
            remaining=info.get("remaining", 0),
            reset_time=info.get("reset_time", time.time()),
            retry_after=info.get("retry_after"),
        )
    
    async def raise_if_limited(self, key: str):
        """如果限流则抛出异常"""
        result = await self.check(key)
        if not result.allowed:
            raise RateLimitExceeded(result.retry_after or self.per)


# 便捷函数

def create_rate_limiter(
    requests: int = 100,
    per_seconds: int = 60,
    algorithm: str = "token_bucket",
    redis_url: Optional[str] = None,
) -> RateLimiter:
    """创建限流器"""
    redis_client = None
    if redis_url and REDIS_AVAILABLE:
        redis_client = redis.from_url(redis_url)
    
    return RateLimiter(
        algorithm=algorithm,
        rate=requests,
        per=per_seconds,
        redis_client=redis_client,
    )
