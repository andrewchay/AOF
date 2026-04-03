"""审计日志模块

提供企业级审计功能：
- 操作日志记录
- 变更追踪
- 合规报告生成
- 与 RBAC 集成

使用示例:
    from bridge.audit import AuditLogger, AuditEvent
    
    audit = AuditLogger(db_session)
    await audit.log_event(AuditEvent(
        user_id="user_123",
        action="dataset:create",
        resource_type="dataset",
        resource_id="ds_456",
    ))
"""

from .logger import AuditLogger, AuditEvent, AuditLevel
from .query import AuditQuery, AuditReportGenerator

__all__ = [
    "AuditLogger",
    "AuditEvent",
    "AuditLevel",
    "AuditQuery",
    "AuditReportGenerator",
]
