# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""RBAC 权限模型定义

本模块定义 RBAC（基于角色的访问控制）的核心数据模型：
- User: 用户
- Role: 角色 (admin/owner/editor/viewer)
- Permission: 权限 (dataset:read, dataset:write, ontology:admin)
- Resource: 资源 (dataset:xxx, ontology:xxx)

与 NebulaGraph 集成：
- 用户同步到 NebulaGraph 原生用户系统
- 租户隔离通过 NebulaGraph GraphSpace 实现
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, List, Dict


class RoleType(str, Enum):
    """预定义角色类型，按权限从高到低排列"""
    ADMIN = "admin"           # 超级管理员，所有权限
    OWNER = "owner"           # 资源所有者，拥有资源的完全控制权
    EDITOR = "editor"         # 编辑者，可读写但不可删除
    VIEWER = "viewer"         # 查看者，只读权限
    
    @property
    def level(self) -> int:
        """权限级别，数值越大权限越高"""
        levels = {
            RoleType.VIEWER: 1,
            RoleType.EDITOR: 2,
            RoleType.OWNER: 3,
            RoleType.ADMIN: 4,
        }
        return levels.get(self, 0)


class ResourceType(str, Enum):
    """资源类型"""
    DATASET = "dataset"       # 数据集
    ONTOLOGY = "ontology"     # 本体
    SYSTEM = "system"         # 系统级资源
    AUDIT = "audit"           # 审计日志


class Action(str, Enum):
    """操作类型"""
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    ADMIN = "admin"           # 管理权限（如授权给他人）


@dataclass
class Permission:
    """权限定义
    
    格式: resource_type:action 或 resource_type:resource_id:action
    示例:
        - dataset:*:read      # 读取所有数据集
        - dataset:abc123:write # 写入特定数据集
        - ontology:*:admin    # 管理所有本体
    """
    resource_type: ResourceType
    action: Action
    resource_id: Optional[str] = None  # None 表示通配所有资源
    
    def __str__(self) -> str:
        if self.resource_id:
            return f"{self.resource_type.value}:{self.resource_id}:{self.action.value}"
        return f"{self.resource_type.value}:*:{self.action.value}"
    
    def matches(self, other: Permission) -> bool:
        """检查此权限是否匹配另一个权限（用于权限检查）"""
        if self.resource_type != other.resource_type:
            return False
        if self.action != other.action and self.action != Action.ADMIN:
            return False
        if self.resource_id is None:  # 通配符匹配所有
            return True
        return self.resource_id == other.resource_id
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_type": self.resource_type.value,
            "action": self.action.value,
            "resource_id": self.resource_id,
            "permission_string": str(self),
        }


@dataclass
class Resource:
    """资源定义"""
    type: ResourceType
    id: str
    name: Optional[str] = None
    tenant_id: Optional[str] = None  # 所属租户
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __str__(self) -> str:
        return f"{self.type.value}:{self.id}"
    
    def to_permission(self, action: Action) -> Permission:
        """生成对此资源的特定权限"""
        return Permission(
            resource_type=self.type,
            action=action,
            resource_id=self.id
        )


@dataclass
class User:
    """用户定义"""
    id: str                           # 唯一标识（如 UUID）
    username: str                     # 登录名
    email: Optional[str] = None
    tenant_id: Optional[str] = None   # 主租户（用户所属组织）
    roles: List[Role] = field(default_factory=list)
    is_active: bool = True
    is_superuser: bool = False        # 超级管理员，绕过所有权限检查
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def has_permission(self, permission: Permission) -> bool:
        """检查用户是否拥有特定权限"""
        if self.is_superuser:
            return True
        for role in self.roles:
            if role.has_permission(permission):
                return True
        return False
    
    def can_access_resource(self, resource: Resource, action: Action) -> bool:
        """检查用户是否可以访问特定资源"""
        required = resource.to_permission(action)
        return self.has_permission(required)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "tenant_id": self.tenant_id,
            "roles": [r.to_dict() for r in self.roles],
            "is_active": self.is_active,
            "is_superuser": self.is_superuser,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Role:
    """角色定义"""
    id: str
    name: str
    role_type: RoleType
    tenant_id: Optional[str] = None   # None 表示系统级角色
    permissions: List[Permission] = field(default_factory=list)
    description: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def has_permission(self, permission: Permission) -> bool:
        """检查角色是否包含特定权限"""
        for p in self.permissions:
            if p.matches(permission):
                return True
        return False
    
    def add_permission(self, permission: Permission) -> None:
        """添加权限（避免重复）"""
        if not self.has_permission(permission):
            self.permissions.append(permission)
    
    def remove_permission(self, permission_str: str) -> bool:
        """移除权限"""
        for i, p in enumerate(self.permissions):
            if str(p) == permission_str:
                self.permissions.pop(i)
                return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role_type": self.role_type.value,
            "tenant_id": self.tenant_id,
            "permissions": [p.to_dict() for p in self.permissions],
            "description": self.description,
        }


@dataclass
class UserRoleAssignment:
    """用户角色关联（多对多关系的实现）"""
    user_id: str
    role_id: str
    resource_type: ResourceType      # 角色适用的资源类型
    resource_id: Optional[str] = None  # 角色适用的特定资源（None 表示租户级）
    granted_by: Optional[str] = None   # 授权者
    granted_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None  # 角色过期时间
    
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "role_id": self.role_id,
            "resource_type": self.resource_type.value,
            "resource_id": self.resource_id,
            "granted_by": self.granted_by,
            "granted_at": self.granted_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_expired": self.is_expired(),
        }


# 预定义系统角色权限
SYSTEM_ROLE_PERMISSIONS: Dict[RoleType, List[Permission]] = {
    RoleType.ADMIN: [
        # 管理员拥有所有权限
        Permission(ResourceType.SYSTEM, Action.ADMIN),
        Permission(ResourceType.DATASET, Action.ADMIN),
        Permission(ResourceType.ONTOLOGY, Action.ADMIN),
        Permission(ResourceType.AUDIT, Action.ADMIN),
    ],
    RoleType.OWNER: [
        # 资源所有者：对特定资源的完全控制
        Permission(ResourceType.DATASET, Action.CREATE),
        Permission(ResourceType.DATASET, Action.READ),
        Permission(ResourceType.DATASET, Action.UPDATE),
        Permission(ResourceType.DATASET, Action.DELETE),
        Permission(ResourceType.DATASET, Action.ADMIN),
        Permission(ResourceType.ONTOLOGY, Action.CREATE),
        Permission(ResourceType.ONTOLOGY, Action.READ),
        Permission(ResourceType.ONTOLOGY, Action.UPDATE),
        Permission(ResourceType.ONTOLOGY, Action.DELETE),
    ],
    RoleType.EDITOR: [
        # 编辑者：读写但不可删除或授权
        Permission(ResourceType.DATASET, Action.CREATE),
        Permission(ResourceType.DATASET, Action.READ),
        Permission(ResourceType.DATASET, Action.UPDATE),
        Permission(ResourceType.ONTOLOGY, Action.READ),
        Permission(ResourceType.ONTOLOGY, Action.UPDATE),
    ],
    RoleType.VIEWER: [
        # 查看者：只读
        Permission(ResourceType.DATASET, Action.READ),
        Permission(ResourceType.ONTOLOGY, Action.READ),
    ],
}


def create_system_role(role_type: RoleType, tenant_id: Optional[str] = None) -> Role:
    """创建系统预定义角色"""
    role_id = f"system_{role_type.value}" if tenant_id is None else f"{tenant_id}_{role_type.value}"
    return Role(
        id=role_id,
        name=role_type.value.capitalize(),
        role_type=role_type,
        tenant_id=tenant_id,
        permissions=SYSTEM_ROLE_PERMISSIONS.get(role_type, []).copy(),
        description=f"System defined {role_type.value} role"
    )
