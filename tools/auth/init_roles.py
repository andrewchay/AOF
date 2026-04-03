#!/usr/bin/env python3
"""初始化 RBAC 系统角色和权限

用法:
    # 初始化系统级角色
    python tools/auth/init_roles.py --system
    
    # 为租户创建角色
    python tools/auth/init_roles.py --tenant acme_corp
    
    # 创建默认管理员用户
    python tools/auth/init_roles.py --create-admin admin_user --email admin@example.com

依赖:
    - PostgreSQL/MySQL 数据库（存储 RBAC 数据）
    - 可选：NebulaGraph（同步用户）
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from bridge.auth import (
    RBACManager,
    RoleType,
    ResourceType,
    Action,
    create_system_role,
    SYSTEM_ROLE_PERMISSIONS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def init_system_roles(rbac: RBACManager) -> None:
    """初始化系统级预定义角色"""
    logger.info("Initializing system roles...")
    
    roles_created = []
    
    for role_type in RoleType:
        try:
            role = create_system_role(role_type, tenant_id=None)
            
            # 检查是否已存在
            existing = await rbac.get_role(role.id)
            if existing:
                logger.info(f"Role {role.name} already exists, skipping")
                continue
            
            # 创建角色
            created = await rbac.create_role(
                name=role.name,
                role_type=role.role_type,
                tenant_id=role.tenant_id,
                permissions=role.permissions,
                description=role.description
            )
            roles_created.append(created)
            logger.info(f"Created system role: {created.name} ({created.id})")
            
        except Exception as e:
            logger.error(f"Failed to create role {role_type.value}: {e}")
    
    logger.info(f"System roles initialization complete. Created: {len(roles_created)}")
    return roles_created


async def init_tenant_roles(rbac: RBACManager, tenant_id: str) -> None:
    """为指定租户创建角色"""
    logger.info(f"Initializing roles for tenant: {tenant_id}")
    
    roles = await rbac.create_tenant_roles(tenant_id)
    
    for role_type, role in roles.items():
        logger.info(f"  Created: {role.name} ({role.id})")
    
    logger.info(f"Tenant roles initialization complete for {tenant_id}")


async def create_admin_user(
    rbac: RBACManager,
    username: str,
    email: Optional[str] = None,
    tenant_id: Optional[str] = None
) -> None:
    """创建管理员用户"""
    logger.info(f"Creating admin user: {username}")
    
    try:
        # 创建用户
        user = await rbac.create_user(
            username=username,
            email=email,
            tenant_id=tenant_id,
            is_superuser=True,
            metadata={"created_by": "init_script", "role": "system_admin"}
        )
        logger.info(f"Created admin user: {user.username} ({user.id})")
        
        # 分配 ADMIN 角色
        admin_role_id = "system_admin" if tenant_id is None else f"{tenant_id}_admin"
        
        from bridge.auth import Resource
        assignment = await rbac.grant_role(
            user_id=user.id,
            role_id=admin_role_id,
            resource_type=ResourceType.SYSTEM,
            granted_by="system"
        )
        logger.info(f"Granted ADMIN role to {username}")
        
        return user
        
    except Exception as e:
        logger.error(f"Failed to create admin user: {e}")
        raise


async def verify_setup(rbac: RBACManager) -> bool:
    """验证 RBAC 设置是否正确"""
    logger.info("Verifying RBAC setup...")
    
    all_ok = True
    
    # 1. 检查系统角色
    for role_type in RoleType:
        role_id = f"system_{role_type.value}"
        role = await rbac.get_role(role_id)
        if role:
            logger.info(f"  ✓ Role {role_type.value} exists with {len(role.permissions)} permissions")
        else:
            logger.error(f"  ✗ Role {role_type.value} not found")
            all_ok = False
    
    # 2. 检查权限完整性
    admin_role = await rbac.get_role("system_admin")
    if admin_role:
        expected_perms = len(SYSTEM_ROLE_PERMISSIONS[RoleType.ADMIN])
        actual_perms = len(admin_role.permissions)
        if actual_perms >= expected_perms:
            logger.info(f"  ✓ Admin role has {actual_perms} permissions")
        else:
            logger.warning(f"  ⚠ Admin role has only {actual_perms}/{expected_perms} permissions")
    
    return all_ok


def setup_database_connection(db_url: Optional[str] = None):
    """设置数据库连接（简化版，实际需根据项目 ORM 调整）"""
    # 这里应根据实际使用的 ORM（如 SQLAlchemy, Tortoise 等）初始化
    # 示例使用简单的字典存储（仅用于演示）
    
    class MockDB:
        def __init__(self):
            self.users = {}
            self.roles = {}
            self.assignments = {}
        
        async def execute(self, query, params):
            logger.debug(f"Mock execute: {query}")
        
        async def fetch_one(self, query, params):
            return None
        
        async def fetch_many(self, query, params):
            return []
    
    return MockDB()


async def main():
    parser = argparse.ArgumentParser(description="Initialize RBAC roles and permissions")
    parser.add_argument("--system", action="store_true", help="Initialize system roles")
    parser.add_argument("--tenant", type=str, help="Create roles for specific tenant")
    parser.add_argument("--create-admin", type=str, help="Create admin user with given username")
    parser.add_argument("--email", type=str, help="Admin user email")
    parser.add_argument("--db-url", type=str, help="Database connection URL")
    parser.add_argument("--verify", action="store_true", help="Verify setup after initialization")
    
    args = parser.parse_args()
    
    # 如果没有参数，默认初始化系统角色
    if not any([args.system, args.tenant, args.create_admin, args.verify]):
        args.system = True
    
    # 初始化数据库连接
    db = setup_database_connection(args.db_url)
    rbac = RBACManager(db_session=db)
    
    try:
        # 初始化系统角色
        if args.system:
            await init_system_roles(rbac)
        
        # 初始化租户角色
        if args.tenant:
            await init_tenant_roles(rbac, args.tenant)
        
        # 创建管理员用户
        if args.create_admin:
            await create_admin_user(
                rbac=rbac,
                username=args.create_admin,
                email=args.email,
                tenant_id=args.tenant
            )
        
        # 验证设置
        if args.verify or args.system:
            ok = await verify_setup(rbac)
            if not ok:
                sys.exit(1)
        
        logger.info("Initialization complete!")
        
    except Exception as e:
        logger.error(f"Initialization failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    from typing import Optional
    asyncio.run(main())
