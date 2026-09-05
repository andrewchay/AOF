"""RBAC 权限管理核心

提供权限检查、角色分配、资源访问控制等功能。
与 NebulaGraph 集成：
- 用户同步到 NebulaGraph 原生用户系统
- 租户隔离通过 GraphSpace 实现

使用示例:
    # 初始化 RBAC
    rbac = RBACManager(db_session)
    
    # 检查权限
    if rbac.check_permission(user_id, resource, Action.READ):
        # 允许访问
        
    # 分配角色
    rbac.grant_role(user_id, RoleType.EDITOR, resource, granted_by="admin")
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable
from functools import wraps
import logging

from .models import (
    User, Role, Resource, Permission, UserRoleAssignment,
    RoleType, ResourceType, Action,
    create_system_role
)

logger = logging.getLogger(__name__)


class RBACError(Exception):
    """RBAC 相关错误"""
    pass


class PermissionDenied(RBACError):
    """权限不足"""
    pass


class ResourceNotFound(RBACError):
    """资源不存在"""
    pass


class RBACManager:
    """RBAC 权限管理器
    
    职责：
    1. 用户/角色/权限的 CRUD
    2. 权限检查
    3. 与 NebulaGraph 用户系统同步
    4. 租户隔离管理
    """
    
    def __init__(
        self, 
        db_session=None,
        nebula_pool=None,  # NebulaGraph 连接池（可选）
        cache_client=None   # Redis 缓存客户端（可选）
    ):
        self.db = db_session
        self.nebula_pool = nebula_pool
        self.cache = cache_client
        self._user_cache: Dict[str, User] = {}  # 内存缓存（短时效）
        
        # 内存存储（当 db_session 为 None 时使用）
        self._users_store: Dict[str, User] = {}
        self._roles_store: Dict[str, Role] = {}
        self._assignments_store: List[UserRoleAssignment] = []
        
    # ========== 用户管理 ==========
    
    async def create_user(
        self,
        username: str,
        email: Optional[str] = None,
        tenant_id: Optional[str] = None,
        is_superuser: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> User:
        """创建新用户"""
        user_id = str(uuid.uuid4())
        user = User(
            id=user_id,
            username=username,
            email=email,
            tenant_id=tenant_id,
            is_superuser=is_superuser,
            metadata=metadata or {}
        )
        
        # 持久化到数据库
        await self._persist_user(user)
        
        # 同步到 NebulaGraph（如果配置了）
        if self.nebula_pool:
            await self._sync_user_to_nebula(user)
        
        logger.info(f"Created user: {username} ({user_id})")
        return user
    
    async def get_user(self, user_id: str, use_cache: bool = True) -> Optional[User]:
        """获取用户信息（带缓存）"""
        # 1. 查内存缓存
        if use_cache and user_id in self._user_cache:
            return self._user_cache[user_id]
        
        # 2. 查 Redis 缓存
        if self.cache:
            cached = await self.cache.get(f"user:{user_id}")
            if cached:
                # 反序列化
                user = self._deserialize_user(cached)
                self._user_cache[user_id] = user
                return user
        
        # 3. 查数据库
        user = await self._fetch_user_from_db(user_id)
        if user:
            self._user_cache[user_id] = user
            if self.cache:
                await self.cache.setex(f"user:{user_id}", 300, self._serialize_user(user))
        
        return user
    
    async def get_user_by_username(self, username: str) -> Optional[User]:
        """通过用户名获取用户"""
        return await self._fetch_user_by_username(username)
    
    # ========== 角色管理 ==========
    
    async def create_role(
        self,
        name: str,
        role_type: RoleType,
        tenant_id: Optional[str] = None,
        permissions: Optional[List[Permission]] = None,
        description: Optional[str] = None
    ) -> Role:
        """创建自定义角色"""
        role_id = str(uuid.uuid4())
        role = Role(
            id=role_id,
            name=name,
            role_type=role_type,
            tenant_id=tenant_id,
            permissions=permissions or [],
            description=description
        )
        
        await self._persist_role(role)
        logger.info(f"Created role: {name} ({role_id})")
        return role
    
    async def get_role(self, role_id: str) -> Optional[Role]:
        """获取角色信息"""
        return await self._fetch_role_from_db(role_id)
    
    async def list_roles(
        self, 
        tenant_id: Optional[str] = None,
        role_type: Optional[RoleType] = None
    ) -> List[Role]:
        """列角色（支持租户过滤）"""
        return await self._fetch_roles(tenant_id=tenant_id, role_type=role_type)
    
    # ========== 权限检查 ==========
    
    async def check_permission(
        self,
        user_id: str,
        resource_type: ResourceType,
        action: Action,
        resource_id: Optional[str] = None
    ) -> bool:
        """检查用户是否有特定权限"""
        required = Permission(resource_type, action, resource_id)
        return await self._check_permission_internal(user_id, required)
    
    async def check_resource_access(
        self,
        user_id: str,
        resource: Resource,
        action: Action
    ) -> bool:
        """检查用户是否可以访问特定资源"""
        required = resource.to_permission(action)
        return await self._check_permission_internal(user_id, required)
    
    async def require_permission(
        self,
        user_id: str,
        resource_type: ResourceType,
        action: Action,
        resource_id: Optional[str] = None
    ):
        """要求必须有权限，否则抛出 PermissionDenied"""
        if not await self.check_permission(user_id, resource_type, action, resource_id):
            resource_str = f"{resource_type.value}:{resource_id or '*'}"
            raise PermissionDenied(
                f"User {user_id} does not have {action.value} permission on {resource_str}"
            )
    
    async def _check_permission_internal(self, user_id: str, required: Permission) -> bool:
        """内部权限检查逻辑"""
        user = await self.get_user(user_id)
        if not user:
            return False
        
        # 超级管理员绕过检查
        if user.is_superuser:
            return True
        
        # 检查用户的所有角色
        for role in user.roles:
            if role.has_permission(required):
                return True
        
        return False
    
    # ========== 角色分配 ==========
    
    async def grant_role(
        self,
        user_id: str,
        role_id: str,
        resource_type: ResourceType,
        resource_id: Optional[str] = None,
        granted_by: Optional[str] = None,
        expires_days: Optional[int] = None
    ) -> UserRoleAssignment:
        """授予用户角色"""
        # 检查授权者权限
        if granted_by:
            # 需要 ADMIN 权限才能授权
            await self.require_permission(
                granted_by, ResourceType.SYSTEM, Action.ADMIN
            )
        
        # 检查角色是否存在
        role = await self.get_role(role_id)
        if not role:
            raise RBACError(f"Role {role_id} not found")
        
        # 创建角色分配
        expires_at = None
        if expires_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_days)
        
        assignment = UserRoleAssignment(
            user_id=user_id,
            role_id=role_id,
            resource_type=resource_type,
            resource_id=resource_id,
            granted_by=granted_by,
            expires_at=expires_at
        )
        
        await self._persist_assignment(assignment)
        
        # 清除用户缓存（权限已变更）
        await self._invalidate_user_cache(user_id)
        
        logger.info(f"Granted role {role_id} to user {user_id} on {resource_type}:{resource_id or '*'}")
        return assignment
    
    async def revoke_role(
        self,
        user_id: str,
        role_id: str,
        resource_type: ResourceType,
        resource_id: Optional[str] = None,
        revoked_by: Optional[str] = None
    ) -> bool:
        """撤销用户角色"""
        # 检查撤销者权限
        if revoked_by:
            await self.require_permission(
                revoked_by, ResourceType.SYSTEM, Action.ADMIN
            )
        
        success = await self._delete_assignment(user_id, role_id, resource_type, resource_id)
        if success:
            await self._invalidate_user_cache(user_id)
            logger.info(f"Revoked role {role_id} from user {user_id}")
        
        return success
    
    async def list_user_roles(
        self,
        user_id: str,
        include_expired: bool = False
    ) -> List[UserRoleAssignment]:
        """列出用户的所有角色分配"""
        assignments = await self._fetch_user_assignments(user_id)
        if not include_expired:
            assignments = [a for a in assignments if not a.is_expired()]
        return assignments
    
    async def list_resource_members(
        self,
        resource_type: ResourceType,
        resource_id: str
    ) -> List[Dict[str, Any]]:
        """列出资源的成员及其角色"""
        return await self._fetch_resource_members(resource_type, resource_id)
    
    # ========== 租户管理 ==========
    
    async def create_tenant_roles(self, tenant_id: str) -> Dict[RoleType, Role]:
        """为租户创建系统预定义角色"""
        roles = {}
        for role_type in RoleType:
            role = create_system_role(role_type, tenant_id=tenant_id)
            await self._persist_role(role)
            roles[role_type] = role
        
        logger.info(f"Created system roles for tenant: {tenant_id}")
        return roles
    
    async def get_tenant_admin(self, tenant_id: str) -> Optional[User]:
        """获取租户管理员"""
        # 查找拥有租户级 ADMIN 角色的用户
        admin_role_id = f"{tenant_id}_admin"
        return await self._fetch_user_by_role(admin_role_id)
    
    # ========== NebulaGraph 集成 ==========
    
    async def _sync_user_to_nebula(self, user: User) -> bool:
        """同步用户到 NebulaGraph
        
        创建 NebulaGraph 用户并设置密码（随机生成）
        实际密码通过其他方式（如 SSO）验证
        """
        if not self.nebula_pool:
            return False
        
        try:
            # 使用 admin 会话创建用户
            
            session = self.nebula_pool.get_session("root", "nebula")
            
            # 创建用户
            create_user_ngql = f'CREATE USER IF NOT EXISTS "{user.username}" WITH PASSWORD "{self._generate_temp_password(user.id)}"'
            result = session.execute(create_user_ngql)
            
            if result.is_succeeded():
                logger.info(f"Synced user {user.username} to NebulaGraph")
                session.release()
                return True
            else:
                logger.error(f"Failed to sync user to NebulaGraph: {result.error_msg()}")
                session.release()
                return False
                
        except Exception as e:
            logger.error(f"Error syncing user to NebulaGraph: {e}")
            return False
    
    def _generate_temp_password(self, user_id: str) -> str:
        """生成临时密码（基于 user_id 的确定性密码）"""
        import hashlib
        return hashlib.sha256(f"aof_{user_id}_nebula".encode()).hexdigest()[:16]
    
    async def grant_nebula_permission(
        self,
        user: User,
        space_name: str,
        nebula_role: str = "GUEST"  # GOD, ADMIN, DBA, USER, GUEST
    ) -> bool:
        """在 NebulaGraph 中授权用户访问图空间"""
        if not self.nebula_pool:
            return False
        
        try:
            session = self.nebula_pool.get_session("root", "nebula")
            
            # 授权角色
            grant_ngql = f'GRANT ROLE {nebula_role} ON {space_name} TO "{user.username}"'
            result = session.execute(grant_ngql)
            
            session.release()
            
            if result.is_succeeded():
                logger.info(f"Granted {nebula_role} on {space_name} to {user.username}")
                return True
            else:
                logger.error(f"Failed to grant NebulaGraph role: {result.error_msg()}")
                return False
                
        except Exception as e:
            logger.error(f"Error granting NebulaGraph permission: {e}")
            return False
    
    # ========== 缓存管理 ==========
    
    async def _invalidate_user_cache(self, user_id: str) -> None:
        """清除用户缓存"""
        if user_id in self._user_cache:
            del self._user_cache[user_id]
        if self.cache:
            await self.cache.delete(f"user:{user_id}")
    
    # ========== 数据库操作（需根据实际 ORM 实现）==========
    
    async def _persist_user(self, user: User) -> None:
        """持久化用户到数据库"""
        if self.db is not None:
            from .db_models import DBUser
            from sqlalchemy import select
            async with self.db() as session:
                # Check if user already exists
                result = await session.execute(
                    select(DBUser).where(DBUser.id == user.id)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.username = user.username
                    existing.email = user.email
                    existing.tenant_id = user.tenant_id
                    existing.is_active = user.is_active
                    existing.is_superuser = user.is_superuser
                    existing.metadata_json = user.metadata
                    existing.updated_at = datetime.utcnow()
                else:
                    db_user = DBUser(
                        id=user.id,
                        username=user.username,
                        email=user.email,
                        tenant_id=user.tenant_id,
                        is_active=user.is_active,
                        is_superuser=user.is_superuser,
                        metadata_json=user.metadata,
                    )
                    session.add(db_user)
                await session.commit()
        else:
            self._users_store[user.id] = user
    
    async def _fetch_user_from_db(self, user_id: str) -> Optional[User]:
        """从数据库获取用户"""
        if self.db is not None:
            from .db_models import DBUser
            from sqlalchemy import select
            async with self.db() as session:
                result = await session.execute(
                    select(DBUser).where(DBUser.id == user_id)
                )
                db_user = result.scalar_one_or_none()
                if db_user:
                    return User(
                        id=db_user.id,
                        username=db_user.username,
                        email=db_user.email,
                        tenant_id=db_user.tenant_id,
                        is_active=db_user.is_active,
                        is_superuser=db_user.is_superuser,
                        metadata=db_user.metadata_json or {},
                        created_at=db_user.created_at,
                        updated_at=db_user.updated_at,
                    )
                return None
        else:
            return self._users_store.get(user_id)
    
    async def _fetch_user_by_username(self, username: str) -> Optional[User]:
        """通过用户名查询用户"""
        if self.db is not None:
            from .db_models import DBUser
            from sqlalchemy import select
            async with self.db() as session:
                result = await session.execute(
                    select(DBUser).where(DBUser.username == username)
                )
                db_user = result.scalar_one_or_none()
                if db_user:
                    return User(
                        id=db_user.id,
                        username=db_user.username,
                        email=db_user.email,
                        tenant_id=db_user.tenant_id,
                        is_active=db_user.is_active,
                        is_superuser=db_user.is_superuser,
                        metadata=db_user.metadata_json or {},
                        created_at=db_user.created_at,
                        updated_at=db_user.updated_at,
                    )
                return None
        else:
            for user in self._users_store.values():
                if user.username == username:
                    return user
            return None
    
    async def _persist_role(self, role: Role) -> None:
        """持久化角色"""
        if self.db is not None:
            from .db_models import DBRole, DBPermission
            from sqlalchemy import select
            async with self.db() as session:
                result = await session.execute(
                    select(DBRole).where(DBRole.id == role.id)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.name = role.name
                    existing.role_type = role.role_type.value
                    existing.tenant_id = role.tenant_id
                    existing.description = role.description
                else:
                    db_role = DBRole(
                        id=role.id,
                        name=role.name,
                        role_type=role.role_type.value,
                        tenant_id=role.tenant_id,
                        description=role.description,
                    )
                    session.add(db_role)
                    # Persist permissions
                    for perm in role.permissions:
                        db_perm = DBPermission(
                            id=str(uuid.uuid4()),
                            role_id=role.id,
                            resource_type=perm.resource_type.value,
                            action=perm.action.value,
                            resource_id=perm.resource_id,
                        )
                        session.add(db_perm)
                await session.commit()
        else:
            self._roles_store[role.id] = role
    
    async def _fetch_role_from_db(self, role_id: str) -> Optional[Role]:
        """从数据库获取角色"""
        if self.db is not None:
            from .db_models import DBRole
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload
            async with self.db() as session:
                result = await session.execute(
                    select(DBRole).where(DBRole.id == role_id)
                    .options(selectinload(DBRole.permissions))
                )
                db_role = result.scalar_one_or_none()
                if db_role:
                    permissions = [
                        Permission(
                            resource_type=ResourceType(p.resource_type),
                            action=Action(p.action),
                            resource_id=p.resource_id,
                        )
                        for p in db_role.permissions
                    ]
                    return Role(
                        id=db_role.id,
                        name=db_role.name,
                        role_type=RoleType(db_role.role_type),
                        tenant_id=db_role.tenant_id,
                        permissions=permissions,
                        description=db_role.description,
                        created_at=db_role.created_at,
                    )
                return None
        else:
            return self._roles_store.get(role_id)
    
    async def _fetch_roles(
        self,
        tenant_id: Optional[str] = None,
        role_type: Optional[RoleType] = None
    ) -> List[Role]:
        """查询角色列表"""
        if self.db is not None:
            from .db_models import DBRole
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload
            async with self.db() as session:
                query = select(DBRole).options(selectinload(DBRole.permissions))
                if tenant_id is not None:
                    query = query.where(DBRole.tenant_id == tenant_id)
                if role_type is not None:
                    query = query.where(DBRole.role_type == role_type.value)
                result = await session.execute(query)
                db_roles = result.scalars().all()
                roles = []
                for db_role in db_roles:
                    permissions = [
                        Permission(
                            resource_type=ResourceType(p.resource_type),
                            action=Action(p.action),
                            resource_id=p.resource_id,
                        )
                        for p in db_role.permissions
                    ]
                    roles.append(Role(
                        id=db_role.id,
                        name=db_role.name,
                        role_type=RoleType(db_role.role_type),
                        tenant_id=db_role.tenant_id,
                        permissions=permissions,
                        description=db_role.description,
                        created_at=db_role.created_at,
                    ))
                return roles
        else:
            roles = list(self._roles_store.values())
            if tenant_id is not None:
                roles = [r for r in roles if r.tenant_id == tenant_id]
            if role_type is not None:
                roles = [r for r in roles if r.role_type == role_type]
            return roles
    
    async def _persist_assignment(self, assignment: UserRoleAssignment) -> None:
        """持久化角色分配"""
        if self.db is not None:
            from .db_models import user_roles_table
            from sqlalchemy import insert
            async with self.db() as session:
                stmt = insert(user_roles_table).values(
                    user_id=assignment.user_id,
                    role_id=assignment.role_id,
                    resource_type=assignment.resource_type.value,
                    resource_id=assignment.resource_id,
                    granted_by=assignment.granted_by,
                    granted_at=assignment.granted_at,
                    expires_at=assignment.expires_at,
                )
                await session.execute(stmt)
                await session.commit()
        else:
            self._assignments_store.append(assignment)
    
    async def _delete_assignment(
        self,
        user_id: str,
        role_id: str,
        resource_type: ResourceType,
        resource_id: Optional[str] = None
    ) -> bool:
        """删除角色分配"""
        if self.db is not None:
            from .db_models import user_roles_table
            from sqlalchemy import delete, and_
            async with self.db() as session:
                conditions = [
                    user_roles_table.c.user_id == user_id,
                    user_roles_table.c.role_id == role_id,
                    user_roles_table.c.resource_type == resource_type.value,
                ]
                if resource_id is not None:
                    conditions.append(user_roles_table.c.resource_id == resource_id)
                else:
                    conditions.append(user_roles_table.c.resource_id.is_(None))
                stmt = delete(user_roles_table).where(and_(*conditions))
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
        else:
            original_len = len(self._assignments_store)
            self._assignments_store = [
                a for a in self._assignments_store
                if not (a.user_id == user_id and a.role_id == role_id
                        and a.resource_type == resource_type
                        and a.resource_id == resource_id)
            ]
            return len(self._assignments_store) < original_len
    
    async def _fetch_user_assignments(self, user_id: str) -> List[UserRoleAssignment]:
        """获取用户的角色分配"""
        if self.db is not None:
            from .db_models import user_roles_table
            from sqlalchemy import select
            async with self.db() as session:
                result = await session.execute(
                    select(user_roles_table).where(user_roles_table.c.user_id == user_id)
                )
                rows = result.fetchall()
                return [
                    UserRoleAssignment(
                        user_id=row.user_id,
                        role_id=row.role_id,
                        resource_type=ResourceType(row.resource_type),
                        resource_id=row.resource_id,
                        granted_by=row.granted_by,
                        granted_at=row.granted_at,
                        expires_at=row.expires_at,
                    )
                    for row in rows
                ]
        else:
            return [a for a in self._assignments_store if a.user_id == user_id]
    
    async def _fetch_resource_members(
        self,
        resource_type: ResourceType,
        resource_id: str
    ) -> List[Dict[str, Any]]:
        """获取资源成员"""
        if self.db is not None:
            from .db_models import user_roles_table, DBUser, DBRole
            from sqlalchemy import select
            async with self.db() as session:
                result = await session.execute(
                    select(
                        user_roles_table.c.user_id,
                        user_roles_table.c.role_id,
                        user_roles_table.c.granted_by,
                        user_roles_table.c.granted_at,
                        user_roles_table.c.expires_at,
                        DBUser.username,
                        DBRole.name.label('role_name'),
                    )
                    .join(DBUser, user_roles_table.c.user_id == DBUser.id)
                    .join(DBRole, user_roles_table.c.role_id == DBRole.id)
                    .where(user_roles_table.c.resource_type == resource_type.value)
                    .where(user_roles_table.c.resource_id == resource_id)
                )
                rows = result.fetchall()
                return [
                    {
                        'user_id': row.user_id,
                        'username': row.username,
                        'role_id': row.role_id,
                        'role_name': row.role_name,
                        'granted_by': row.granted_by,
                        'granted_at': row.granted_at.isoformat() if row.granted_at else None,
                        'expires_at': row.expires_at.isoformat() if row.expires_at else None,
                    }
                    for row in rows
                ]
        else:
            members = []
            for a in self._assignments_store:
                if a.resource_type == resource_type and a.resource_id == resource_id:
                    user = self._users_store.get(a.user_id)
                    role = self._roles_store.get(a.role_id)
                    members.append({
                        'user_id': a.user_id,
                        'username': user.username if user else None,
                        'role_id': a.role_id,
                        'role_name': role.name if role else None,
                        'granted_by': a.granted_by,
                        'granted_at': a.granted_at.isoformat() if a.granted_at else None,
                        'expires_at': a.expires_at.isoformat() if a.expires_at else None,
                    })
            return members
    
    async def _fetch_user_by_role(self, role_id: str) -> Optional[User]:
        """通过角色查询用户"""
        if self.db is not None:
            from .db_models import user_roles_table, DBUser
            from sqlalchemy import select
            async with self.db() as session:
                result = await session.execute(
                    select(DBUser)
                    .join(user_roles_table, DBUser.id == user_roles_table.c.user_id)
                    .where(user_roles_table.c.role_id == role_id)
                    .limit(1)
                )
                db_user = result.scalar_one_or_none()
                if db_user:
                    return User(
                        id=db_user.id,
                        username=db_user.username,
                        email=db_user.email,
                        tenant_id=db_user.tenant_id,
                        is_active=db_user.is_active,
                        is_superuser=db_user.is_superuser,
                        metadata=db_user.metadata_json or {},
                        created_at=db_user.created_at,
                        updated_at=db_user.updated_at,
                    )
                return None
        else:
            for a in self._assignments_store:
                if a.role_id == role_id:
                    return self._users_store.get(a.user_id)
            return None
    
    def _serialize_user(self, user: User) -> str:
        """序列化用户"""
        import json
        return json.dumps(user.to_dict())
    
    def _deserialize_user(self, data: str) -> User:
        """反序列化用户"""
        import json
        d = json.loads(data)
        roles = []
        for r in d.get('roles', []):
            permissions = [
                Permission(
                    resource_type=ResourceType(p['resource_type']),
                    action=Action(p['action']),
                    resource_id=p.get('resource_id'),
                )
                for p in r.get('permissions', [])
            ]
            roles.append(Role(
                id=r['id'],
                name=r['name'],
                role_type=RoleType(r['role_type']),
                tenant_id=r.get('tenant_id'),
                permissions=permissions,
                description=r.get('description'),
            ))
        return User(
            id=d['id'],
            username=d['username'],
            email=d.get('email'),
            tenant_id=d.get('tenant_id'),
            roles=roles,
            is_active=d.get('is_active', True),
            is_superuser=d.get('is_superuser', False),
            created_at=datetime.fromisoformat(d['created_at']) if d.get('created_at') else datetime.utcnow(),
            metadata=d.get('metadata', {}),
        )


# ========== 装饰器工具 ==========

def require_permission(
    resource_type: ResourceType,
    action: Action,
    resource_id_param: Optional[str] = None,
    get_user_id: Optional[Callable] = None
):
    """权限检查装饰器（用于 API 端点）
    
    使用示例:
        @require_permission(ResourceType.DATASET, Action.READ, resource_id_param="dataset_id")
        async def get_dataset(request, dataset_id: str):
            # 只有有权限的用户才能执行
            pass
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 获取 user_id
            if get_user_id:
                user_id = get_user_id(*args, **kwargs)
            else:
                # 默认从 request 中获取
                request = args[0] if args else kwargs.get('request')
                user_id = getattr(request, 'user_id', None) or getattr(request.state, 'user_id', None)
            
            if not user_id:
                raise PermissionDenied("User not authenticated")
            
            # 获取 resource_id
            resource_id = kwargs.get(resource_id_param) if resource_id_param else None
            
            # 检查权限（需要 rbac_manager 实例，这里简化处理）
            # 实际使用中应从 app state 获取
            rbac = kwargs.get('rbac_manager') or getattr(args[0], 'rbac_manager', None)
            if rbac:
                await rbac.require_permission(user_id, resource_type, action, resource_id)
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator
