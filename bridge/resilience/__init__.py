"""弹性组件模块

提供高可用保障：
- 限流（Rate Limiting）
- 熔断（Circuit Breaker）
- 重试（Retry）
- 超时（Timeout）

使用示例:
    from bridge.resilience import RateLimiter, CircuitBreaker
    
    limiter = RateLimiter(rate=100, per=60)  # 每分钟100次
    if await limiter.allow("user_123"):
        # 处理请求
"""

from .rate_limiter import RateLimiter, TokenBucket, SlidingWindow
from .circuit_breaker import CircuitBreaker, CircuitState
from .retry import RetryPolicy, retry

__all__ = [
    "RateLimiter",
    "TokenBucket",
    "SlidingWindow",
    "CircuitBreaker",
    "CircuitState",
    "RetryPolicy",
    "retry",
]
