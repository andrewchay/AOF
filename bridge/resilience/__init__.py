# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
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
