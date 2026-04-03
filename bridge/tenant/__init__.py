"""多租户管理模块

提供多租户隔离功能：
- 租户创建与管理
- 数据隔离（通过 NebulaGraph GraphSpace）
- 资源配额管理
- 租户级权限控制

使用示例:
    from bridge.tenant import TenantManager, TenantContext
    
    async with TenantContext(tenant_id="acme"):
        # 在此上下文中的操作都限定在该租户空间
        await create_dataset(name="docs")
"""

from .manager import TenantManager, Tenant, TenantConfig
from .context import TenantContext, get_current_tenant_id, set_current_tenant
from .middleware import TenantMiddleware

__all__ = [
    "TenantManager",
    "Tenant",
    "TenantConfig",
    "TenantContext",
    "get_current_tenant_id",
    "set_current_tenant",
    "clear_current_tenant",
    "TenantMiddleware",
]
