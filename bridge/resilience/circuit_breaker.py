"""熔断器

防止故障扩散，保护系统稳定性。

状态转换：
    CLOSED (正常) -> OPEN (熔断) -> HALF_OPEN (探测) -> CLOSED
    
触发条件：
    - 失败率达到阈值
    - 连续失败次数达到阈值
    
恢复：
    - 超时后进入半开状态
    - 探测成功则关闭
"""

from __future__ import annotations

import time
import asyncio
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Any, Callable, Dict
from functools import wraps
import logging

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """熔断器状态"""
    CLOSED = "closed"       # 正常状态，允许请求
    OPEN = "open"           # 熔断状态，拒绝请求
    HALF_OPEN = "half_open" # 半开状态，试探性允许


class CircuitBreakerOpen(Exception):
    """熔断器开启异常"""
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Circuit breaker is OPEN. Retry after {retry_after}s")


@dataclass
class CircuitBreakerConfig:
    """熔断器配置"""
    failure_threshold: int = 5          # 触发熔断的失败次数
    recovery_timeout: float = 30.0      # 熔断后等待时间（秒）
    half_open_max_calls: int = 3        # 半开状态最大试探次数
    success_threshold: int = 2          # 半开状态成功次数阈值（达到则关闭）
    
    # 失败率阈值（百分比）
    failure_rate_threshold: float = 50.0
    
    # 时间窗口（统计失败率）
    window_size: float = 60.0


@dataclass
class CircuitStats:
    """熔断统计"""
    state: CircuitState
    failures: int = 0
    successes: int = 0
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    last_failure_time: Optional[float] = None
    last_state_change: float = field(default_factory=time.time)
    total_calls: int = 0
    total_failures: int = 0


class CircuitBreaker:
    """熔断器
    
    使用示例:
        breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30.0
        )
        
        try:
            result = await breaker.call(
                fetch_data,
                fallback=default_data
            )
        except CircuitBreakerOpen:
            # 熔断中，使用降级逻辑
            pass
    """
    
    def __init__(
        self,
        name: str = "default",
        config: Optional[CircuitBreakerConfig] = None,
        on_state_change: Optional[Callable] = None,
    ):
        """
        Args:
            name: 熔断器名称
            config: 配置
            on_state_change: 状态变化回调
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.on_state_change = on_state_change
        
        self._state = CircuitState.CLOSED
        self._lock = asyncio.Lock()
        
        # 统计
        self._stats = CircuitStats(state=CircuitState.CLOSED)
        
        # 半开状态计数
        self._half_open_calls = 0
        
        # 失败时间窗口（用于计算失败率）
        self._failure_times: list[float] = []
    
    @property
    def state(self) -> CircuitState:
        """当前状态"""
        return self._state
    
    async def call(
        self,
        func: Callable,
        *args,
        fallback: Optional[Any] = None,
        **kwargs
    ) -> Any:
        """执行函数，带熔断保护
        
        Args:
            func: 要执行的函数（异步）
            *args, **kwargs: 函数参数
            fallback: 熔断时的回退值
        
        Returns:
            函数结果或 fallback
        
        Raises:
            CircuitBreakerOpen: 如果熔断且没有 fallback
        """
        async with self._lock:
            # 检查状态
            if self._state == CircuitState.OPEN:
                # 检查是否可以进入半开
                if self._can_attempt_reset():
                    await self._transition_to(CircuitState.HALF_OPEN)
                    self._half_open_calls = 0
                else:
                    # 仍然熔断
                    retry_after = self._get_retry_after()
                    if fallback is not None:
                        logger.warning(f"Circuit {self.name} is OPEN, using fallback")
                        return fallback
                    raise CircuitBreakerOpen(retry_after)
            
            elif self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    # 半开状态请求过多，当作熔断处理
                    retry_after = self._get_retry_after()
                    if fallback is not None:
                        return fallback
                    raise CircuitBreakerOpen(retry_after)
                
                self._half_open_calls += 1
        
        # 执行函数（在锁外执行）
        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception:
            await self.record_failure()
            if fallback is not None:
                return fallback
            raise
    
    async def record_success(self) -> None:
        """记录成功"""
        async with self._lock:
            self._stats.total_calls += 1
            self._stats.successes += 1
            self._stats.consecutive_successes += 1
            self._stats.consecutive_failures = 0
            
            if self._state == CircuitState.HALF_OPEN:
                # 半开状态下连续成功达到阈值，关闭熔断
                if self._stats.consecutive_successes >= self.config.success_threshold:
                    await self._transition_to(CircuitState.CLOSED)
    
    async def record_failure(self) -> None:
        """记录失败"""
        async with self._lock:
            now = time.time()
            
            self._stats.total_calls += 1
            self._stats.total_failures += 1
            self._stats.failures += 1
            self._stats.consecutive_failures += 1
            self._stats.consecutive_successes = 0
            self._stats.last_failure_time = now
            
            # 记录失败时间（用于计算失败率）
            self._failure_times.append(now)
            self._cleanup_old_failures(now)
            
            # 检查是否需要熔断
            if self._state == CircuitState.CLOSED:
                if self._should_open():
                    await self._transition_to(CircuitState.OPEN)
            
            elif self._state == CircuitState.HALF_OPEN:
                # 半开状态下失败，重新熔断
                await self._transition_to(CircuitState.OPEN)
    
    def _should_open(self) -> bool:
        """检查是否应该熔断"""
        # 检查连续失败次数
        if self._stats.consecutive_failures >= self.config.failure_threshold:
            return True
        
        # 检查失败率
        if self._stats.total_calls > 0:
            failure_rate = (self._stats.total_failures / self._stats.total_calls) * 100
            if failure_rate >= self.config.failure_rate_threshold:
                return True
        
        # 检查时间窗口内的失败率
        recent_failures = len(self._failure_times)
        if recent_failures >= self.config.failure_threshold:
            return True
        
        return False
    
    def _can_attempt_reset(self) -> bool:
        """检查是否可以尝试恢复（进入半开）"""
        if self._state != CircuitState.OPEN:
            return False
        
        elapsed = time.time() - self._stats.last_state_change
        return elapsed >= self.config.recovery_timeout
    
    def _get_retry_after(self) -> float:
        """获取建议重试时间"""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._stats.last_state_change
            return max(0, self.config.recovery_timeout - elapsed)
        return 0
    
    async def _transition_to(self, new_state: CircuitState) -> None:
        """状态转换"""
        old_state = self._state
        self._state = new_state
        self._stats.state = new_state
        self._stats.last_state_change = time.time()
        
        # 重置统计（除半开外）
        if new_state == CircuitState.CLOSED:
            self._stats.failures = 0
            self._stats.successes = 0
            self._stats.consecutive_failures = 0
            self._stats.consecutive_successes = 0
            self._failure_times.clear()
        
        logger.warning(
            f"Circuit {self.name} state changed: {old_state.value} -> {new_state.value}"
        )
        
        # 回调
        if self.on_state_change:
            try:
                await self.on_state_change(self.name, old_state, new_state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")
    
    def _cleanup_old_failures(self, now: float) -> None:
        """清理过期的失败记录"""
        cutoff = now - self.config.window_size
        self._failure_times = [t for t in self._failure_times if t > cutoff]
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "name": self.name,
            "state": self._state.value,
            "failures": self._stats.failures,
            "successes": self._stats.successes,
            "consecutive_failures": self._stats.consecutive_failures,
            "consecutive_successes": self._stats.consecutive_successes,
            "total_calls": self._stats.total_calls,
            "total_failures": self._stats.total_failures,
            "failure_rate": (
                (self._stats.total_failures / self._stats.total_calls * 100)
                if self._stats.total_calls > 0 else 0
            ),
            "retry_after": self._get_retry_after() if self._state == CircuitState.OPEN else 0,
        }


# 便捷装饰器

def circuit_breaker(
    name: Optional[str] = None,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    fallback: Optional[Any] = None,
):
    """熔断器装饰器
    
    使用示例:
        @circuit_breaker(name="api_call", failure_threshold=5)
        async def call_external_api():
            # 可能失败的调用
            pass
    """
    breaker_name = name or "default"
    
    # 全局熔断器存储
    if not hasattr(circuit_breaker, "_breakers"):
        circuit_breaker._breakers = {}
    
    if breaker_name not in circuit_breaker._breakers:
        circuit_breaker._breakers[breaker_name] = CircuitBreaker(
            name=breaker_name,
            config=CircuitBreakerConfig(
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )
        )
    
    breaker = circuit_breaker._breakers[breaker_name]
    
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await breaker.call(func, *args, fallback=fallback, **kwargs)
        
        # 附加统计方法
        wrapper.get_stats = breaker.get_stats
        wrapper.breaker = breaker
        
        return wrapper
    return decorator
