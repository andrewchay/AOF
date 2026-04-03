"""存储后端工厂

管理存储后端的创建和生命周期。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, Type
import logging

from .base import GraphBackend

logger = logging.getLogger(__name__)


@dataclass
class StorageConfig:
    """存储配置"""
    backend_type: str = "cognee"  # "cognee" 或 "nebula"
    
    # Cognee 配置
    cognee_root: Optional[str] = None
    
    # NebulaGraph 配置
    nebula_host: str = "127.0.0.1"
    nebula_port: int = 9669
    nebula_user: str = "root"
    nebula_password: str = "nebula"
    nebula_space: str = "aof_default"
    
    # 连接池配置
    max_connections: int = 10
    connection_timeout: int = 30
    
    # 其他
    default_dataset: str = "default"


class StorageFactory:
    """存储后端工厂"""
    
    _backends: Dict[str, Type[GraphBackend]] = {}
    _instances: Dict[str, GraphBackend] = {}
    
    @classmethod
    def register(cls, name: str, backend_class: Type[GraphBackend]) -> None:
        """注册后端类型"""
        cls._backends[name] = backend_class
        logger.info(f"Registered storage backend: {name}")
    
    @classmethod
    def create(
        cls,
        config: StorageConfig,
        name: Optional[str] = None,
    ) -> GraphBackend:
        """创建存储后端实例
        
        Args:
            config: 存储配置
            name: 实例名称（用于缓存）
        
        Returns:
            GraphBackend 实例
        """
        backend_type = config.backend_type
        
        if backend_type not in cls._backends:
            raise ValueError(f"Unknown backend type: {backend_type}. "
                           f"Available: {list(cls._backends.keys())}")
        
        # 如果提供了名称，检查缓存
        if name and name in cls._instances:
            return cls._instances[name]
        
        # 创建新实例
        backend_class = cls._backends[backend_type]
        instance = backend_class(backend_type, config.__dict__)
        
        # 缓存实例
        if name:
            cls._instances[name] = instance
        
        logger.info(f"Created storage backend: {backend_type}")
        return instance
    
    @classmethod
    def get_instance(cls, name: str) -> Optional[GraphBackend]:
        """获取已缓存的实例"""
        return cls._instances.get(name)
    
    @classmethod
    def list_backends(cls) -> list[str]:
        """列出可用的后端类型"""
        return list(cls._backends.keys())
    
    @classmethod
    def clear_cache(cls) -> None:
        """清空实例缓存"""
        cls._instances.clear()


# 注册内置后端
def _register_builtin_backends():
    """注册内置后端"""
    try:
        from .cognee_backend import CogneeBackend
        StorageFactory.register("cognee", CogneeBackend)
    except ImportError as e:
        logger.warning(f"Could not register CogneeBackend: {e}")
    
    try:
        from .nebula_backend import NebulaBackend
        StorageFactory.register("nebula", NebulaBackend)
    except ImportError as e:
        logger.warning(f"Could not register NebulaBackend: {e}")


# 模块加载时自动注册
_register_builtin_backends()
