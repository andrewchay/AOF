"""审计日志数据库模型（SQLAlchemy 版本）

此模块提供 SQLAlchemy ORM 模型，用于持久化审计日志数据。

表结构:
    - audit_logs: 审计日志表
"""

from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Text, Integer, JSON, Index
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class DBAuditLog(Base):
    """审计日志数据库模型"""
    __tablename__ = 'audit_logs'
    
    # 主键
    id = Column(String(36), primary_key=True)
    event_id = Column(String(36), unique=True, nullable=False, index=True)
    
    # 时间戳
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    level = Column(String(20), default='info', nullable=False)
    
    # 主体（谁）
    user_id = Column(String(36), nullable=True, index=True)
    username = Column(String(100), nullable=True)
    tenant_id = Column(String(50), nullable=True, index=True)
    session_id = Column(String(100), nullable=True)
    
    # 行为（做了什么）
    action = Column(String(100), nullable=False, index=True)
    status = Column(String(20), default='pending', nullable=False)
    
    # 客体（对什么做的）
    resource_type = Column(String(50), nullable=True, index=True)
    resource_id = Column(String(100), nullable=True)
    
    # 详情
    request_payload = Column(JSON, nullable=True)
    response_summary = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    error_code = Column(String(50), nullable=True)
    
    # 上下文
    client_ip = Column(String(50), nullable=True)
    user_agent = Column(Text, nullable=True)
    request_id = Column(String(100), nullable=True, index=True)
    duration_ms = Column(Integer, nullable=True)
    
    # 扩展
    metadata_json = Column('metadata', JSON, default=dict)
    
    __table_args__ = (
        # 按时间和用户查询
        Index('idx_audit_user_time', 'user_id', 'timestamp'),
        # 按租户和时间查询
        Index('idx_audit_tenant_time', 'tenant_id', 'timestamp'),
        # 按资源和操作查询
        Index('idx_audit_resource', 'resource_type', 'resource_id', 'timestamp'),
        # 按操作类型查询
        Index('idx_audit_action_time', 'action', 'timestamp'),
        # 按状态查询
        Index('idx_audit_status_time', 'status', 'timestamp'),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "level": self.level,
            "user_id": self.user_id,
            "username": self.username,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "action": self.action,
            "status": self.status,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "request_payload": self.request_payload,
            "response_summary": self.response_summary,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "client_ip": self.client_ip,
            "user_agent": self.user_agent,
            "request_id": self.request_id,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata_json,
        }
    
    @classmethod
    def from_audit_event(cls, event):
        """从 AuditEvent 创建数据库模型"""
        return cls(
            id=event.event_id,
            event_id=event.event_id,
            timestamp=event.timestamp,
            level=event.level.value if hasattr(event.level, 'value') else str(event.level),
            user_id=event.user_id,
            username=event.username,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            action=event.action,
            status=event.status,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            request_payload=event.request_payload,
            response_summary=event.response_summary,
            error_message=event.error_message,
            error_code=event.error_code,
            client_ip=event.client_ip,
            user_agent=event.user_agent,
            request_id=event.request_id,
            duration_ms=event.duration_ms,
            metadata_json=event.metadata,
        )


def init_audit_tables(engine):
    """创建审计相关表"""
    Base.metadata.create_all(engine)


def drop_audit_tables(engine):
    """删除审计相关表"""
    Base.metadata.drop_all(engine)
