# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""RBAC 数据库模型（SQLAlchemy 版本）

此模块提供 SQLAlchemy ORM 模型，用于持久化 RBAC 数据。
可根据项目实际情况调整。

表结构:
    - rbac_users: 用户表
    - rbac_roles: 角色表
    - rbac_permissions: 权限表
    - rbac_user_roles: 用户角色关联表（多对多）
"""

from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Boolean, Text, 
    ForeignKey, Table, Index, JSON
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


# 用户角色关联表（中间表）
user_roles_table = Table(
    'rbac_user_roles',
    Base.metadata,
    Column('user_id', String(36), ForeignKey('rbac_users.id'), primary_key=True),
    Column('role_id', String(36), ForeignKey('rbac_roles.id'), primary_key=True),
    Column('resource_type', String(50), primary_key=True),
    Column('resource_id', String(100), nullable=True),
    Column('granted_by', String(36), ForeignKey('rbac_users.id'), nullable=True),
    Column('granted_at', DateTime, default=datetime.utcnow),
    Column('expires_at', DateTime, nullable=True),
)


class DBUser(Base):
    """用户数据库模型"""
    __tablename__ = 'rbac_users'
    
    id = Column(String(36), primary_key=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True)
    tenant_id = Column(String(50), nullable=True, index=True)
    
    # 状态
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    
    # 元数据
    metadata_json = Column('metadata', JSON, default=dict)
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关系
    roles = relationship(
        "DBRole",
        secondary=user_roles_table,
        back_populates="users"
    )
    
    __table_args__ = (
        Index('idx_users_tenant', 'tenant_id', 'is_active'),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "tenant_id": self.tenant_id,
            "is_active": self.is_active,
            "is_superuser": self.is_superuser,
            "metadata": self.metadata_json,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DBRole(Base):
    """角色数据库模型"""
    __tablename__ = 'rbac_roles'
    
    id = Column(String(36), primary_key=True)
    name = Column(String(100), nullable=False)
    role_type = Column(String(20), nullable=False, index=True)  # admin/owner/editor/viewer
    tenant_id = Column(String(50), nullable=True, index=True)
    description = Column(Text, nullable=True)
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    users = relationship(
        "DBUser",
        secondary=user_roles_table,
        back_populates="roles"
    )
    permissions = relationship(
        "DBPermission",
        back_populates="role",
        cascade="all, delete-orphan"
    )
    
    __table_args__ = (
        # 租户内角色名唯一
        Index('idx_roles_tenant_name', 'tenant_id', 'name', unique=True),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "role_type": self.role_type,
            "tenant_id": self.tenant_id,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "permissions": [p.to_dict() for p in self.permissions],
        }


class DBPermission(Base):
    """权限数据库模型"""
    __tablename__ = 'rbac_permissions'
    
    id = Column(String(36), primary_key=True)
    role_id = Column(String(36), ForeignKey('rbac_roles.id'), nullable=False, index=True)
    
    # 权限定义
    resource_type = Column(String(50), nullable=False)  # dataset/ontology/system/audit
    action = Column(String(20), nullable=False)         # create/read/update/delete/admin
    resource_id = Column(String(100), nullable=True)    # None 表示通配
    
    # 关系
    role = relationship("DBRole", back_populates="permissions")
    
    __table_args__ = (
        # 角色 + 资源 + 操作 唯一
        Index(
            'idx_permissions_unique', 
            'role_id', 'resource_type', 'action', 'resource_id',
            unique=True
        ),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "resource_type": self.resource_type,
            "action": self.action,
            "resource_id": self.resource_id,
            "permission_string": f"{self.resource_type}:{self.resource_id or '*'}:{self.action}",
        }


class DBAuditLog(Base):
    """审计日志数据库模型"""
    __tablename__ = 'rbac_audit_logs'
    
    id = Column(String(36), primary_key=True)
    event_id = Column(String(36), unique=True, index=True)
    
    # 用户
    user_id = Column(String(36), ForeignKey('rbac_users.id'), nullable=True, index=True)
    username = Column(String(100), nullable=True)
    
    # 操作
    action = Column(String(50), nullable=False, index=True)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(100), nullable=True)
    
    # 详情
    request_payload = Column(JSON, nullable=True)
    response_status = Column(String(10), nullable=True)
    client_ip = Column(String(50), nullable=True)
    duration_ms = Column(int, nullable=True)
    
    # 时间戳
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    
    __table_args__ = (
        # 按时间和用户查询
        Index('idx_audit_user_time', 'user_id', 'timestamp'),
        # 按资源和操作查询
        Index('idx_audit_resource', 'resource_type', 'resource_id', 'timestamp'),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "event_id": self.event_id,
            "user_id": self.user_id,
            "username": self.username,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "response_status": self.response_status,
            "client_ip": self.client_ip,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


# ========== 数据库初始化 ==========

def init_rbac_tables(engine):
    """创建 RBAC 相关表"""
    Base.metadata.create_all(engine)


def drop_rbac_tables(engine):
    """删除 RBAC 相关表"""
    Base.metadata.drop_all(engine)


# ========== 与 RBACManager 的集成 ==========

class SQLAlchemyRBACAdapter:
    """SQLAlchemy 实现的 RBAC 数据访问层"""
    
    def __init__(self, session_factory):
        self.session_factory = session_factory
    
    async def _persist_user(self, user):
        """保存用户"""
        async with self.session_factory() as session:
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
    
    async def _fetch_user_from_db(self, user_id: str):
        """获取用户"""
        from sqlalchemy import select
        
        async with self.session_factory() as session:
            result = await session.execute(
                select(DBUser).where(DBUser.id == user_id)
            )
            db_user = result.scalar_one_or_none()
            
            if db_user:
                # 转换为领域模型
                from .models import User
                return User(
                    id=db_user.id,
                    username=db_user.username,
                    email=db_user.email,
                    tenant_id=db_user.tenant_id,
                    is_active=db_user.is_active,
                    is_superuser=db_user.is_superuser,
                    metadata=db_user.metadata_json,
                    created_at=db_user.created_at,
                    updated_at=db_user.updated_at,
                )
            return None
    
    # 其他方法类似...
