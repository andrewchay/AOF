# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""重试机制

提供智能重试功能：
- 固定间隔重试
- 指数退避
- 抖动（Jitter）
- 条件重试
"""

from __future__ import annotations

import asyncio
import random
import logging
from typing import Optional, Callable, Any, Type, Union, Tuple
from dataclasses import dataclass
from functools import wraps

logger = logging.getLogger(__name__)


# 可重试的异常类型
RetryableException = Union[Type[Exception], Tuple[Type[Exception], ...]]


@dataclass
class RetryConfig:
    """重试配置"""
    max_attempts: int = 3              # 最大尝试次数
    base_delay: float = 1.0            # 基础延迟（秒）
    max_delay: float = 60.0            # 最大延迟（秒）
    exponential_base: float = 2.0      # 指数基数
    jitter: bool = True                # 是否添加抖动
    jitter_max: float = 1.0            # 最大抖动（秒）
    
    # 重试条件
    retryable_exceptions: RetryableException = Exception
    should_retry: Optional[Callable[[Exception], bool]] = None
    
    # 回调
    on_retry: Optional[Callable[[int, Exception, float], None]] = None
    on_success: Optional[Callable[[int], None]] = None
    on_failure: Optional[Callable[[int, Exception], None]] = None


class RetryExhausted(Exception):
    """重试次数耗尽异常"""
    def __init__(self, attempts: int, last_exception: Exception):
        self.attempts = attempts
        self.last_exception = last_exception
        super().__init__(f"Retry exhausted after {attempts} attempts: {last_exception}")


class RetryPolicy:
    """重试策略
    
    使用示例:
        policy = RetryPolicy(
            max_attempts=3,
            base_delay=1.0,
            exponential_base=2.0
        )
        
        result = await policy.execute(fetch_data)
    """
    
    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()
    
    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """执行函数，失败时重试
        
        Args:
            func: 要执行的异步函数
            *args, **kwargs: 函数参数
        
        Returns:
            函数返回值
        
        Raises:
            RetryExhausted: 重试次数耗尽
        """
        last_exception = None
        
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                result = await func(*args, **kwargs)
                
                # 成功回调
                if self.config.on_success:
                    try:
                        self.config.on_success(attempt)
                    except Exception as e:
                        logger.error(f"on_success callback error: {e}")
                
                return result
                
            except Exception as e:
                last_exception = e
                
                # 检查是否应该重试
                if not self._should_retry(e, attempt):
                    raise
                
                # 计算延迟
                if attempt < self.config.max_attempts:
                    delay = self._calculate_delay(attempt)
                    
                    logger.warning(
                        f"Attempt {attempt}/{self.config.max_attempts} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    
                    # 重试回调
                    if self.config.on_retry:
                        try:
                            self.config.on_retry(attempt, e, delay)
                        except Exception as cb_e:
                            logger.error(f"on_retry callback error: {cb_e}")
                    
                    await asyncio.sleep(delay)
        
        # 重试耗尽
        logger.error(f"All {self.config.max_attempts} attempts failed")
        
        # 失败回调
        if self.config.on_failure:
            try:
                self.config.on_failure(self.config.max_attempts, last_exception)
            except Exception as e:
                logger.error(f"on_failure callback error: {e}")
        
        raise RetryExhausted(self.config.max_attempts, last_exception)
    
    def _should_retry(self, exception: Exception, attempt: int) -> bool:
        """检查是否应该重试"""
        # 检查是否是可重试的异常类型
        if not isinstance(exception, self.config.retryable_exceptions):
            return False
        
        # 自定义条件
        if self.config.should_retry:
            return self.config.should_retry(exception)
        
        return True
    
    def _calculate_delay(self, attempt: int) -> float:
        """计算重试延迟（指数退避 + 抖动）"""
        # 指数退避
        delay = self.config.base_delay * (self.config.exponential_base ** (attempt - 1))
        
        # 限制最大延迟
        delay = min(delay, self.config.max_delay)
        
        # 添加抖动（避免惊群效应）
        if self.config.jitter:
            jitter = random.uniform(0, self.config.jitter_max)
            delay += jitter
        
        return delay


# 便捷装饰器

def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: RetryableException = Exception,
):
    """重试装饰器
    
    使用示例:
        @retry(max_attempts=3, base_delay=1.0)
        async def fetch_data():
            # 可能失败的网络请求
            pass
    """
    config = RetryConfig(
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        exponential_base=exponential_base,
        jitter=jitter,
        retryable_exceptions=retryable_exceptions,
    )
    
    policy = RetryPolicy(config)
    
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await policy.execute(func, *args, **kwargs)
        
        return wrapper
    return decorator


# 特定场景的重试策略

class NetworkRetryPolicy(RetryPolicy):
    """网络请求重试策略"""
    
    def __init__(self):
        super().__init__(RetryConfig(
            max_attempts=5,
            base_delay=1.0,
            max_delay=30.0,
            exponential_base=2.0,
            jitter=True,
            retryable_exceptions=(
                ConnectionError,
                TimeoutError,
                OSError,
            ),
        ))


class DatabaseRetryPolicy(RetryPolicy):
    """数据库操作重试策略"""
    
    def __init__(self):
        super().__init__(RetryConfig(
            max_attempts=3,
            base_delay=0.5,
            max_delay=10.0,
            exponential_base=2.0,
            jitter=True,
            retryable_exceptions=(
                ConnectionError,
                TimeoutError,
            ),
        ))


# 便捷函数

async def with_retry(
    func: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    **kwargs
) -> Any:
    """带重试的函数调用
    
    使用示例:
        result = await with_retry(
            fetch_data,
            url="https://api.example.com",
            max_attempts=3,
            base_delay=1.0
        )
    """
    config = RetryConfig(
        max_attempts=max_attempts,
        base_delay=base_delay,
    )
    
    policy = RetryPolicy(config)
    return await policy.execute(func, *args, **kwargs)
