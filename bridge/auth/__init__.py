# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""AOF 认证与授权模块

提供企业级 RBAC（基于角色的访问控制）能力：
- 用户/角色/权限管理
- 资源访问控制
- 多租户隔离
- NebulaGraph 用户同步

主要导出:
    - RBACManager: RBAC 管理器
    - require_permission: 权限检查装饰器
    - User, Role, Permission, Resource: 数据模型
    - RoleType, ResourceType, Action: 枚举类型
"""

from .models import (
    User,
    Role,
    Permission,
    Resource,
    UserRoleAssignment,
    RoleType,
    ResourceType,
    Action,
    create_system_role,
    SYSTEM_ROLE_PERMISSIONS,
)

from .rbac import (
    RBACManager,
    RBACError,
    PermissionDenied,
    ResourceNotFound,
    require_permission,
)

__all__ = [
    # 管理器
    "RBACManager",
    "RBACError",
    "PermissionDenied",
    "ResourceNotFound",
    "require_permission",
    # 模型
    "User",
    "Role",
    "Permission",
    "Resource",
    "UserRoleAssignment",
    # 枚举
    "RoleType",
    "ResourceType",
    "Action",
    # 工具
    "create_system_role",
    "SYSTEM_ROLE_PERMISSIONS",
]
