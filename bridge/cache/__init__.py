# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""缓存模块

提供多级缓存支持：
- L1: 进程内 LRU 缓存（最快，进程间不共享）
- L2: Redis 缓存（共享，跨进程）
- L3: 数据库（持久化）

使用示例:
    from bridge.cache import CacheManager
    
    cache = CacheManager(redis_client)
    
    # 读取或计算
    result = await cache.get_or_compute(
        key="user:123",
        compute_func=fetch_from_db,
        ttl=300
    )
"""

from .local_cache import LocalCache
from .redis_cache import RedisCache
from .manager import CacheManager, CacheStrategy

__all__ = [
    "LocalCache",
    "RedisCache",
    "CacheManager",
    "CacheStrategy",
]
