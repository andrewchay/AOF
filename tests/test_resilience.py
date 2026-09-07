# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""弹性组件单元测试

运行:
    cd /path/to/AOF
    python -m pytest tests/test_resilience.py -v
"""

import pytest
import asyncio

from bridge.resilience.rate_limiter import (
    TokenBucket, SlidingWindow, RateLimiter, RateLimitExceeded
)
from bridge.resilience.circuit_breaker import (
    CircuitBreaker, CircuitState, CircuitBreakerConfig, CircuitBreakerOpen
)
from bridge.resilience.retry import (
    RetryPolicy, RetryConfig, RetryExhausted, retry
)


class TestTokenBucket:
    """测试令牌桶"""
    
    @pytest.fixture
    def bucket(self):
        """创建测试令牌桶"""
        return TokenBucket(capacity=10, refill_rate=1.0)
    
    @pytest.mark.asyncio
    async def test_consume_success(self, bucket):
        """测试成功消费"""
        allowed, info = await bucket.consume("key", tokens=1)
        
        assert allowed is True
        assert info["remaining"] == 9
    
    @pytest.mark.asyncio
    async def test_consume_exceed_capacity(self, bucket):
        """测试超出容量"""
        # 消费超过容量的令牌
        allowed, info = await bucket.consume("key", tokens=15)
        
        assert allowed is False
        assert info["remaining"] == 0
        assert info["retry_after"] > 0
    
    @pytest.mark.asyncio
    async def test_refill(self, bucket):
        """测试令牌填充"""
        # 先消费一些令牌
        await bucket.consume("key", tokens=5)
        
        # 等待填充
        await asyncio.sleep(1.1)
        
        # 应该恢复了 1 个令牌
        allowed, info = await bucket.consume("key", tokens=1)
        assert allowed is True
        # 剩余令牌应该增加了
        assert info["remaining"] >= 5
    
    @pytest.mark.asyncio
    async def test_different_keys(self, bucket):
        """测试不同 key 独立计数"""
        allowed1, _ = await bucket.consume("user1", tokens=5)
        allowed2, info = await bucket.consume("user2", tokens=5)
        
        assert allowed1 is True
        assert allowed2 is True
        # user2 应该有 5 个剩余
        assert info["remaining"] == 5


class TestSlidingWindow:
    """测试滑动窗口"""
    
    @pytest.fixture
    def window(self):
        """创建测试窗口"""
        return SlidingWindow(window_size=2, max_requests=3)
    
    @pytest.mark.asyncio
    async def test_allow_within_limit(self, window):
        """测试在限制内允许"""
        for i in range(3):
            allowed, info = await window.allow("key")
            assert allowed is True, f"Request {i+1} should be allowed"
            assert info["remaining"] == 2 - i
    
    @pytest.mark.asyncio
    async def test_exceed_limit(self, window):
        """测试超出限制"""
        # 先达到限制
        for _ in range(3):
            await window.allow("key")
        
        # 第 4 次应该被拒绝
        allowed, info = await window.allow("key")
        assert allowed is False
        assert info["remaining"] == 0
        assert info["retry_after"] > 0
    
    @pytest.mark.asyncio
    async def test_window_slide(self, window):
        """测试窗口滑动"""
        # 达到限制
        for _ in range(3):
            await window.allow("key")
        
        # 等待窗口滑动
        await asyncio.sleep(2.1)
        
        # 应该又可以请求了
        allowed, info = await window.allow("key")
        assert allowed is True


class TestRateLimiter:
    """测试限流器"""
    
    @pytest.mark.asyncio
    async def test_token_bucket_allow(self):
        """测试令牌桶允许"""
        limiter = RateLimiter(
            algorithm="token_bucket",
            rate=10,
            per=60,
        )
        
        # 前 10 次应该允许
        for i in range(10):
            assert await limiter.allow("key") is True, f"Request {i+1} should be allowed"
    
    @pytest.mark.asyncio
    async def test_sliding_window_allow(self):
        """测试滑动窗口允许"""
        limiter = RateLimiter(
            algorithm="sliding_window",
            rate=3,
            per=60,
        )
        
        for i in range(3):
            assert await limiter.allow("key") is True, f"Request {i+1} should be allowed"
        
        assert await limiter.allow("key") is False
    
    @pytest.mark.asyncio
    async def test_check_detailed(self):
        """测试详细检查"""
        limiter = RateLimiter(
            algorithm="token_bucket",
            rate=10,
            per=60,
        )
        
        result = await limiter.check("key")
        
        assert result.allowed is True
        assert result.remaining == 9
    
    @pytest.mark.asyncio
    async def test_raise_if_limited(self):
        """测试限流时抛出异常"""
        limiter = RateLimiter(
            algorithm="token_bucket",
            rate=1,
            per=60,
        )
        
        # 第一次允许
        await limiter.raise_if_limited("key")
        
        # 第二次应该抛出异常
        with pytest.raises(RateLimitExceeded):
            await limiter.raise_if_limited("key")


class TestCircuitBreaker:
    """测试熔断器"""
    
    @pytest.fixture
    def breaker(self):
        """创建测试熔断器"""
        return CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=5.0,  # 较长超时避免测试干扰
                half_open_max_calls=2,
                success_threshold=2,
                failure_rate_threshold=101.0,  # >100 禁用失败率检查
            )
        )
    
    @pytest.mark.asyncio
    async def test_successful_call(self, breaker):
        """测试成功调用"""
        async def success_func():
            return "success"
        
        result = await breaker.call(success_func)
        assert result == "success"
        assert breaker.state == CircuitState.CLOSED
    
    @pytest.mark.asyncio
    async def test_failure_record(self):
        """测试失败记录"""
        # 使用独立的熔断器
        breaker = CircuitBreaker(
            name="failure_test",
            config=CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=5.0,
                failure_rate_threshold=101.0,  # >100 禁用失败率检查
            )
        )
        
        async def fail_func():
            raise ValueError("error")
        
        # 前 2 次失败，熔断器关闭
        for i in range(2):
            with pytest.raises(ValueError):
                await breaker.call(fail_func)
            assert breaker.state == CircuitState.CLOSED, f"After {i+1} failures, state should be CLOSED"
        
        # 第 3 次失败触发熔断，状态变为 OPEN
        with pytest.raises(ValueError):
            await breaker.call(fail_func)
        # 注意：第3次失败后熔断器已经打开
        assert breaker.state == CircuitState.OPEN
    
    @pytest.mark.asyncio
    async def test_open_circuit_blocks(self, breaker):
        """测试熔断状态阻止调用"""
        async def fail_func():
            raise ValueError("error")
        
        # 触发熔断（3次失败）
        for i in range(3):
            try:
                await breaker.call(fail_func)
            except ValueError:
                pass
            except CircuitBreakerOpen:
                # 第4次调用会触发熔断
                pass
        
        assert breaker.state == CircuitState.OPEN
        
        # 熔断状态应该阻止调用
        async def should_not_run():
            return "result"
        
        with pytest.raises(CircuitBreakerOpen):
            await breaker.call(should_not_run)
    
    @pytest.mark.asyncio
    async def test_fallback(self, breaker):
        """测试回退值"""
        async def fail_func():
            raise ValueError("error")
        
        # 触发熔断（3次失败）
        for i in range(3):
            try:
                await breaker.call(fail_func)
            except ValueError:
                pass
            except CircuitBreakerOpen:
                # 第4次调用会触发熔断
                pass
        
        assert breaker.state == CircuitState.OPEN
        
        # 使用回退值
        result = await breaker.call(fail_func, fallback="fallback_value")
        assert result == "fallback_value"
    
    @pytest.mark.asyncio
    async def test_half_open_recovery(self):
        """测试半开恢复"""
        # 创建短恢复时间的熔断器
        breaker = CircuitBreaker(
            name="half_open_test",
            config=CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=0.1,  # 短恢复时间
                half_open_max_calls=2,
                success_threshold=2,
                failure_rate_threshold=101.0,  # >100 禁用失败率检查
            )
        )
        
        async def success_func():
            return "success"
        
        async def fail_func():
            raise ValueError("error")
        
        # 触发熔断
        for _ in range(3):
            try:
                await breaker.call(fail_func)
            except ValueError:
                pass
        
        assert breaker.state == CircuitState.OPEN
        
        # 等待恢复
        await asyncio.sleep(0.15)
        
        # 第一次成功调用应该进入半开
        result = await breaker.call(success_func)
        assert result == "success"
        assert breaker.state == CircuitState.HALF_OPEN
        
        # 再成功一次，应该关闭
        result = await breaker.call(success_func)
        assert result == "success"
        assert breaker.state == CircuitState.CLOSED
    
    @pytest.mark.asyncio
    async def test_half_open_failure_reopen(self):
        """测试半开失败重新熔断"""
        # 创建短恢复时间的熔断器
        breaker = CircuitBreaker(
            name="reopen_test",
            config=CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=0.1,
                half_open_max_calls=2,
                success_threshold=2,
                failure_rate_threshold=101.0,  # >100 禁用失败率检查
            )
        )
        
        async def fail_func():
            raise ValueError("error")
        
        # 触发熔断
        for _ in range(3):
            try:
                await breaker.call(fail_func)
            except ValueError:
                pass
        
        # 等待恢复
        await asyncio.sleep(0.15)
        
        # 进入半开后再次失败
        with pytest.raises(ValueError):
            await breaker.call(fail_func)
        
        # 应该重新熔断
        assert breaker.state == CircuitState.OPEN
    
    def test_get_stats(self, breaker):
        """测试统计"""
        stats = breaker.get_stats()
        
        assert stats["name"] == "test"
        assert stats["state"] == "closed"


class TestRetryPolicy:
    """测试重试策略"""
    
    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """测试成功不重试"""
        policy = RetryPolicy(RetryConfig(max_attempts=3))
        
        call_count = 0
        
        async def success_func():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = await policy.execute(success_func)
        
        assert result == "success"
        assert call_count == 1  # 只调用一次
    
    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        """测试重试后成功"""
        policy = RetryPolicy(RetryConfig(max_attempts=3, base_delay=0.1))
        
        call_count = 0
        
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("error")
            return "success"
        
        result = await policy.execute(flaky_func)
        
        assert result == "success"
        assert call_count == 3  # 重试 3 次
    
    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        """测试重试耗尽"""
        policy = RetryPolicy(RetryConfig(max_attempts=2, base_delay=0.1))
        
        async def always_fail():
            raise ValueError("always fails")
        
        with pytest.raises(RetryExhausted) as exc_info:
            await policy.execute(always_fail)
        
        assert exc_info.value.attempts == 2
    
    @pytest.mark.asyncio
    async def test_non_retryable_exception(self):
        """测试不可重试的异常"""
        policy = RetryPolicy(RetryConfig(
            max_attempts=3,
            retryable_exceptions=ValueError,
        ))
        
        async def raise_type_error():
            raise TypeError("type error")
        
        # TypeError 不在可重试列表中
        with pytest.raises(TypeError):
            await policy.execute(raise_type_error)
    
    @pytest.mark.asyncio
    async def test_custom_should_retry(self):
        """测试自定义重试条件"""
        def should_retry(e):
            return isinstance(e, ValueError) and str(e) == "retry me"
        
        policy = RetryPolicy(RetryConfig(
            max_attempts=3,
            should_retry=should_retry,
            base_delay=0.1,
        ))
        
        call_count = 0
        
        async def conditional_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("retry me")
        
        with pytest.raises(RetryExhausted):
            await policy.execute(conditional_fail)
        
        assert call_count == 3


class TestRetryDecorator:
    """测试重试装饰器"""
    
    @pytest.mark.asyncio
    async def test_retry_decorator(self):
        """测试装饰器"""
        call_count = 0
        
        @retry(max_attempts=3, base_delay=0.1)
        async def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("error")
            return "success"
        
        result = await flaky_function()
        
        assert result == "success"
        assert call_count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
