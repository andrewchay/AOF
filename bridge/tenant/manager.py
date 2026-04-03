"""租户管理器

管理租户的生命周期：
- 创建/删除租户
- 租户配置管理
- 资源配额控制
- 与 NebulaGraph GraphSpace 集成
"""

from __future__ import annotations

import uuid
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
import logging

from bridge.auth import RBACManager, create_system_role, RoleType

logger = logging.getLogger(__name__)


class TenantStatus(str, Enum):
    """租户状态"""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING = "pending"  # 创建中
    DELETED = "deleted"


@dataclass
class TenantConfig:
    """租户配置"""
    # 存储配额
    max_datasets: int = 100
    max_storage_gb: float = 100.0
    
    # 计算配额
    max_concurrent_queries: int = 10
    max_graph_nodes: int = 10_000_000  # 1000万节点
    
    # 功能开关
    enable_analytics: bool = True
    enable_ml_features: bool = False
    enable_api_access: bool = True
    
    # 保留策略
    audit_log_retention_days: int = 90
    backup_retention_days: int = 30
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_datasets": self.max_datasets,
            "max_storage_gb": self.max_storage_gb,
            "max_concurrent_queries": self.max_concurrent_queries,
            "max_graph_nodes": self.max_graph_nodes,
            "enable_analytics": self.enable_analytics,
            "enable_ml_features": self.enable_ml_features,
            "enable_api_access": self.enable_api_access,
            "audit_log_retention_days": self.audit_log_retention_days,
            "backup_retention_days": self.backup_retention_days,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TenantConfig":
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


@dataclass
class Tenant:
    """租户实体"""
    id: str
    name: str
    slug: str  # URL 友好的标识（如 acme-corp）
    status: TenantStatus
    config: TenantConfig
    
    # 关联信息
    admin_user_id: Optional[str] = None
    nebula_space: Optional[str] = None  # NebulaGraph GraphSpace 名称
    
    # 统计
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    dataset_count: int = 0
    total_nodes: int = 0
    total_edges: int = 0
    
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "status": self.status.value,
            "config": self.config.to_dict(),
            "admin_user_id": self.admin_user_id,
            "nebula_space": self.nebula_space,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "usage": {
                "dataset_count": self.dataset_count,
                "total_nodes": self.total_nodes,
                "total_edges": self.total_edges,
            },
            "metadata": self.metadata,
        }


class TenantManager:
    """租户管理器
    
    职责：
    1. 租户的 CRUD
    2. 与 RBAC 集成（创建租户时初始化角色）
    3. 与 NebulaGraph 集成（创建 GraphSpace）
    4. 配额管理
    """
    
    # 租户 slug 命名规则
    SLUG_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]{2,62}[a-z0-9]$')
    
    def __init__(
        self,
        db_session=None,
        rbac_manager: Optional[RBACManager] = None,
        nebula_pool=None,
    ):
        self.db = db_session
        self.rbac = rbac_manager
        self.nebula_pool = nebula_pool
    
    # ========== 租户 CRUD ==========
    
    async def create_tenant(
        self,
        name: str,
        slug: Optional[str] = None,
        admin_email: Optional[str] = None,
        config: Optional[TenantConfig] = None,
    ) -> Tenant:
        """创建新租户
        
        流程：
        1. 验证 slug 唯一性
        2. 创建租户记录
        3. 初始化 RBAC 角色
        4. 创建 NebulaGraph GraphSpace
        5. 创建管理员用户
        """
        # 生成 slug
        if slug is None:
            slug = self._generate_slug(name)
        
        if not self._validate_slug(slug):
            raise ValueError(f"Invalid tenant slug: {slug}")
        
        # 检查 slug 是否已存在
        if await self._slug_exists(slug):
            raise ValueError(f"Tenant slug already exists: {slug}")
        
        tenant_id = str(uuid.uuid4())
        tenant = Tenant(
            id=tenant_id,
            name=name,
            slug=slug,
            status=TenantStatus.PENDING,
            config=config or TenantConfig(),
            nebula_space=f"aof_{slug}",  # NebulaGraph space 命名
        )
        
        try:
            # 1. 持久化租户
            await self._persist_tenant(tenant)
            
            # 2. 初始化 RBAC 角色
            if self.rbac:
                await self.rbac.create_tenant_roles(tenant_id)
            
            # 3. 创建 NebulaGraph GraphSpace
            if self.nebula_pool:
                await self._create_nebula_space(tenant)
            
            # 4. 创建管理员用户
            if admin_email and self.rbac:
                admin = await self.rbac.create_user(
                    username=f"{slug}_admin",
                    email=admin_email,
                    tenant_id=tenant_id,
                )
                # 授予租户 ADMIN 角色
                from bridge.auth import ResourceType
                admin_role_id = f"{tenant_id}_admin"
                await self.rbac.grant_role(
                    user_id=admin.id,
                    role_id=admin_role_id,
                    resource_type=ResourceType.SYSTEM,
                    granted_by="system"
                )
                tenant.admin_user_id = admin.id
                
                # 同步到 NebulaGraph
                if self.nebula_pool:
                    await self.rbac.sync_user_to_nebula(admin)
                    await self.rbac.grant_nebula_permission(
                        admin, tenant.nebula_space, "ADMIN"
                    )
            
            # 更新状态为激活
            tenant.status = TenantStatus.ACTIVE
            await self._update_tenant_status(tenant.id, TenantStatus.ACTIVE)
            
            logger.info(f"Created tenant: {name} ({slug})")
            return tenant
            
        except Exception as e:
            logger.error(f"Failed to create tenant {slug}: {e}")
            # 回滚逻辑
            await self._cleanup_tenant(tenant.id)
            raise
    
    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """获取租户信息"""
        return await self._fetch_tenant(tenant_id)
    
    async def get_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        """通过 slug 获取租户"""
        return await self._fetch_tenant_by_slug(slug)
    
    async def list_tenants(
        self,
        status: Optional[TenantStatus] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Tenant]:
        """列出租户"""
        return await self._fetch_tenants(status=status, limit=limit, offset=offset)
    
    async def update_tenant(
        self,
        tenant_id: str,
        name: Optional[str] = None,
        config: Optional[TenantConfig] = None,
        status: Optional[TenantStatus] = None,
    ) -> Optional[Tenant]:
        """更新租户信息"""
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return None
        
        if name:
            tenant.name = name
        if config:
            tenant.config = config
        if status:
            tenant.status = status
        
        tenant.updated_at = datetime.utcnow()
        await self._persist_tenant(tenant)
        
        return tenant
    
    async def delete_tenant(self, tenant_id: str, hard_delete: bool = False) -> bool:
        """删除租户
        
        Args:
            hard_delete: True=物理删除，False=软删除（标记为 deleted）
        """
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return False
        
        if hard_delete:
            # 物理删除：删除 GraphSpace、数据、角色等
            if self.nebula_pool and tenant.nebula_space:
                await self._delete_nebula_space(tenant.nebula_space)
            
            await self._delete_tenant_data(tenant_id)
        else:
            # 软删除
            tenant.status = TenantStatus.DELETED
            await self._update_tenant_status(tenant_id, TenantStatus.DELETED)
        
        logger.info(f"Deleted tenant: {tenant_id} (hard={hard_delete})")
        return True
    
    async def suspend_tenant(self, tenant_id: str, reason: Optional[str] = None) -> bool:
        """暂停租户（禁止访问但保留数据）"""
        success = await self._update_tenant_status(tenant_id, TenantStatus.SUSPENDED)
        if success:
            logger.warning(f"Suspended tenant: {tenant_id}, reason: {reason}")
        return success
    
    async def activate_tenant(self, tenant_id: str) -> bool:
        """激活已暂停的租户"""
        return await self._update_tenant_status(tenant_id, TenantStatus.ACTIVE)
    
    # ========== 配额管理 ==========
    
    async def check_quota(
        self,
        tenant_id: str,
        resource_type: str,
        requested_amount: int = 1
    ) -> tuple[bool, Dict[str, Any]]:
        """检查配额
        
        Returns:
            (是否允许, 配额信息)
        """
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return False, {"error": "Tenant not found"}
        
        if tenant.status != TenantStatus.ACTIVE:
            return False, {"error": f"Tenant is {tenant.status.value}"}
        
        config = tenant.config
        
        # 检查具体配额
        if resource_type == "dataset":
            current = tenant.dataset_count
            limit = config.max_datasets
            if current + requested_amount > limit:
                return False, {
                    "resource": "dataset",
                    "current": current,
                    "requested": requested_amount,
                    "limit": limit,
                    "available": max(0, limit - current),
                }
        
        elif resource_type == "graph_nodes":
            current = tenant.total_nodes
            limit = config.max_graph_nodes
            if current + requested_amount > limit:
                return False, {
                    "resource": "graph_nodes",
                    "current": current,
                    "requested": requested_amount,
                    "limit": limit,
                    "available": max(0, limit - current),
                }
        
        return True, {"status": "ok"}
    
    async def update_usage_stats(self, tenant_id: str) -> None:
        """更新租户使用统计"""
        # TODO: 从 NebulaGraph 和数据库统计实际使用量
        pass
    
    async def get_usage_report(self, tenant_id: str) -> Dict[str, Any]:
        """获取租户使用报告"""
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return {"error": "Tenant not found"}
        
        config = tenant.config
        
        return {
            "tenant_id": tenant_id,
            "tenant_name": tenant.name,
            "status": tenant.status.value,
            "datasets": {
                "used": tenant.dataset_count,
                "limit": config.max_datasets,
                "percentage": (tenant.dataset_count / config.max_datasets * 100) 
                              if config.max_datasets > 0 else 0,
            },
            "graph_nodes": {
                "used": tenant.total_nodes,
                "limit": config.max_graph_nodes,
                "percentage": (tenant.total_nodes / config.max_graph_nodes * 100)
                              if config.max_graph_nodes > 0 else 0,
            },
            "features": {
                "analytics": config.enable_analytics,
                "ml": config.enable_ml_features,
                "api": config.enable_api_access,
            },
        }
    
    # ========== NebulaGraph 集成 ==========
    
    async def _create_nebula_space(self, tenant: Tenant) -> bool:
        """为租户创建 NebulaGraph GraphSpace"""
        if not self.nebula_pool:
            return False
        
        try:
            from nebula3.gclient.net import Session
            
            session = self.nebula_pool.get_session("root", "nebula")
            
            # 创建 GraphSpace
            space_name = tenant.nebula_space
            partition_num = min(max(tenant.config.max_graph_nodes // 100000, 10), 100)
            
            create_space_ngql = f"""
            CREATE SPACE IF NOT EXISTS {space_name} (
                partition_num={partition_num},
                replica_factor=3,
                vid_type=FIXED_STRING(64)
            )
            """
            
            result = session.execute(create_space_ngql)
            
            if not result.is_succeeded():
                logger.error(f"Failed to create Nebula space: {result.error_msg()}")
                session.release()
                return False
            
            # 等待空间创建完成
            import asyncio
            await asyncio.sleep(2)
            
            # 创建基本 Schema
            session.execute(f"USE {space_name}")
            
            # 创建 Tag: entity
            session.execute("""
                CREATE TAG IF NOT EXISTS entity (
                    name string NOT NULL,
                    type string NOT NULL,
                    properties string,
                    source_doc string,
                    created_at timestamp
                )
            """)
            
            # 创建 Edge: relates_to
            session.execute("""
                CREATE EDGE IF NOT EXISTS relates_to (
                    relation_type string NOT NULL,
                    weight double DEFAULT 1.0,
                    properties string,
                    created_at timestamp
                )
            """)
            
            session.release()
            logger.info(f"Created NebulaGraph space for tenant: {space_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating Nebula space: {e}")
            return False
    
    async def _delete_nebula_space(self, space_name: str) -> bool:
        """删除 NebulaGraph GraphSpace"""
        if not self.nebula_pool:
            return False
        
        try:
            session = self.nebula_pool.get_session("root", "nebula")
            result = session.execute(f"DROP SPACE IF EXISTS {space_name}")
            session.release()
            
            if result.is_succeeded():
                logger.info(f"Deleted Nebula space: {space_name}")
                return True
            else:
                logger.error(f"Failed to delete space: {result.error_msg()}")
                return False
                
        except Exception as e:
            logger.error(f"Error deleting Nebula space: {e}")
            return False
    
    # ========== 工具方法 ==========
    
    def _generate_slug(self, name: str) -> str:
        """从名称生成 slug"""
        # 转换为小写，替换特殊字符
        slug = name.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        
        # 限制长度
        if len(slug) < 3:
            slug = f"tenant-{slug}"
        if len(slug) > 64:
            slug = slug[:64]
        
        # 添加随机后缀避免冲突
        suffix = uuid.uuid4().hex[:8]
        return f"{slug}-{suffix}"
    
    def _validate_slug(self, slug: str) -> bool:
        """验证 slug 格式"""
        if not slug:
            return False
        if len(slug) < 3 or len(slug) > 64:
            return False
        return bool(self.SLUG_PATTERN.match(slug))
    
    # ========== 数据库操作（待实现）==========
    
    async def _slug_exists(self, slug: str) -> bool:
        """检查 slug 是否已存在"""
        # TODO: 实现数据库查询
        return False
    
    async def _persist_tenant(self, tenant: Tenant) -> None:
        """持久化租户"""
        # TODO: 实现数据库写入
        pass
    
    async def _fetch_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """获取租户"""
        # TODO: 实现数据库查询
        pass
    
    async def _fetch_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        """通过 slug 获取租户"""
        # TODO: 实现数据库查询
        pass
    
    async def _fetch_tenants(
        self,
        status: Optional[TenantStatus] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Tenant]:
        """列出租户"""
        # TODO: 实现数据库查询
        return []
    
    async def _update_tenant_status(
        self,
        tenant_id: str,
        status: TenantStatus
    ) -> bool:
        """更新租户状态"""
        # TODO: 实现数据库更新
        return True
    
    async def _delete_tenant_data(self, tenant_id: str) -> None:
        """删除租户数据"""
        # TODO: 实现数据清理
        pass
    
    async def _cleanup_tenant(self, tenant_id: str) -> None:
        """清理创建失败的租户"""
        logger.warning(f"Cleaning up tenant: {tenant_id}")
        await self._delete_tenant_data(tenant_id)
