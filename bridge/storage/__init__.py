# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""存储层模块

提供图存储后端抽象和多后端支持：
- 基础接口（GraphBackend）
- Cognee 后端（默认）
- NebulaGraph 后端（分布式）

使用示例:
    from bridge.storage import StorageFactory, NebulaBackend
    
    # 创建后端
    backend = StorageFactory.create("nebula", config)
    
    # 使用统一接口
    await backend.add_triples(triples)
    result = await backend.execute_cypher(query)
"""

from .base import GraphBackend, Triple, Node, Edge, Path
from .factory import StorageFactory, StorageConfig
from .cognee_backend import CogneeBackend

__all__ = [
    # 基础接口
    "GraphBackend",
    "Triple",
    "Node",
    "Edge",
    "Path",
    # 工厂
    "StorageFactory",
    "StorageConfig",
    # 后端
    "CogneeBackend",
]

# NebulaGraph 后端（可选导入）
try:
    from .nebula_backend import NebulaBackend as _NebulaBackend
    NebulaBackend = _NebulaBackend
    __all__.append("NebulaBackend")
except ImportError:
    pass
