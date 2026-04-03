"""审计日志记录器

提供异步审计日志记录功能，支持：
- 多级日志（DEBUG/INFO/WARNING/ERROR/CRITICAL）
- 批量写入优化
- 多后端存储（DB/文件/消息队列）
- 敏感数据脱敏
"""

from __future__ import annotations

import uuid
import json
import logging
import asyncio
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field, asdict
from collections import deque
import copy

# 配置
logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100
DEFAULT_FLUSH_INTERVAL = 5  # 秒
SENSITIVE_FIELDS = {
    "password", "secret", "token", "api_key", "apikey", "api-key",
    "authorization", "auth", "credential", "credentials",
    "private_key", "privatekey", "private-key",
}


class AuditLevel(str, Enum):
    """审计日志级别"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """审计事件
    
    字段说明:
        event_id: 唯一事件 ID
        timestamp: 事件发生时间
        level: 日志级别
        
        # 主体（谁）
        user_id: 用户 ID
        username: 用户名
        tenant_id: 租户 ID
        session_id: 会话 ID
        
        # 行为（做了什么）
        action: 操作类型（如 dataset:create, search:execute）
        status: 操作状态（success/failure/pending）
        
        # 客体（对什么做的）
        resource_type: 资源类型
        resource_id: 资源 ID
        
        # 详情
        request_payload: 请求参数（脱敏后）
        response_summary: 响应摘要（不含敏感数据）
        error_message: 错误信息
        
        # 上下文
        client_ip: 客户端 IP
        user_agent: 用户代理
        request_id: 请求追踪 ID
        duration_ms: 操作耗时（毫秒）
        
        # 扩展
        metadata: 额外元数据
    """
    # 标识
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    level: AuditLevel = AuditLevel.INFO
    
    # 主体
    user_id: Optional[str] = None
    username: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None
    
    # 行为
    action: str = ""  # 格式: resource_type:operation
    status: str = "pending"  # success/failure/pending
    
    # 客体
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    
    # 详情
    request_payload: Optional[Dict[str, Any]] = None
    response_summary: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    
    # 上下文
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    duration_ms: Optional[int] = None
    
    # 扩展
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化）"""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['level'] = self.level.value
        return data
    
    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), default=str)


class DataMasker:
    """数据脱敏器"""
    
    @staticmethod
    def mask_sensitive_data(data: Any, sensitive_fields: Optional[set] = None) -> Any:
        """递归脱敏敏感数据"""
        fields = sensitive_fields or SENSITIVE_FIELDS
        
        if isinstance(data, dict):
            masked = {}
            for key, value in data.items():
                # 检查 key 是否包含敏感字段
                key_lower = key.lower()
                is_sensitive = any(sf in key_lower for sf in fields)
                
                if is_sensitive and value is not None:
                    masked[key] = DataMasker._mask_value(value)
                elif isinstance(value, (dict, list)):
                    masked[key] = DataMasker.mask_sensitive_data(value, fields)
                else:
                    masked[key] = value
            return masked
        
        elif isinstance(data, list):
            return [DataMasker.mask_sensitive_data(item, fields) for item in data]
        
        return data
    
    @staticmethod
    def _mask_value(value: Any) -> str:
        """脱敏单个值"""
        if isinstance(value, str):
            if len(value) <= 4:
                return "****"
            return value[:2] + "****" + value[-2:]
        return "****"


class AuditLogger:
    """审计日志记录器
    
    特性：
    - 异步批量写入，避免阻塞主流程
    - 支持多后端（数据库、文件、消息队列）
    - 自动脱敏敏感数据
    - 失败重试和降级
    """
    
    def __init__(
        self,
        db_session=None,
        log_file: Optional[str] = None,
        mq_client=None,  # 消息队列客户端（如 Kafka）
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval: int = DEFAULT_FLUSH_INTERVAL,
        enable_masking: bool = True,
    ):
        self.db = db_session
        self.log_file = log_file
        self.mq = mq_client
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.enable_masking = enable_masking
        
        # 批量写入缓冲
        self._buffer: deque = deque()
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False
        
        # 统计
        self._total_logged = 0
        self._total_failed = 0
        
        # 文件句柄（如果需要）
        self._file_handle = None
        if log_file:
            self._file_handle = open(log_file, 'a', encoding='utf-8')
    
    async def start(self):
        """启动后台刷新任务"""
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("Audit logger started")
    
    async def stop(self):
        """停止并刷新剩余日志"""
        self._running = False
        
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        # 刷新剩余日志
        await self._flush_buffer()
        
        if self._file_handle:
            self._file_handle.close()
        
        logger.info(f"Audit logger stopped. Total logged: {self._total_logged}, failed: {self._total_failed}")
    
    async def log_event(self, event: AuditEvent, immediate: bool = False) -> bool:
        """记录审计事件
        
        Args:
            event: 审计事件
            immediate: 是否立即写入（不缓冲）
        
        Returns:
            是否成功
        """
        # 脱敏
        if self.enable_masking and event.request_payload:
            event.request_payload = DataMasker.mask_sensitive_data(event.request_payload)
        
        if immediate:
            return await self._write_single(event)
        
        # 加入缓冲
        async with self._lock:
            self._buffer.append(event)
            should_flush = len(self._buffer) >= self.batch_size
        
        if should_flush:
            await self._flush_buffer()
        
        return True
    
    async def log(
        self,
        action: str,
        user_id: Optional[str] = None,
        status: str = "success",
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        request_payload: Optional[Dict] = None,
        error_message: Optional[str] = None,
        **kwargs
    ) -> bool:
        """便捷方法：快速创建并记录事件"""
        event = AuditEvent(
            action=action,
            user_id=user_id,
            status=status,
            resource_type=resource_type,
            resource_id=resource_id,
            request_payload=request_payload,
            error_message=error_message,
            **kwargs
        )
        return await self.log_event(event)
    
    async def _flush_loop(self):
        """后台定期刷新任务"""
        while self._running:
            try:
                await asyncio.sleep(self.flush_interval)
                await self._flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Audit flush loop error: {e}")
    
    async def _flush_buffer(self):
        """刷新缓冲区到存储"""
        async with self._lock:
            if not self._buffer:
                return
            
            # 取出所有待写入事件
            events = list(self._buffer)
            self._buffer.clear()
        
        # 批量写入
        success = await self._write_batch(events)
        
        if success:
            self._total_logged += len(events)
        else:
            self._total_failed += len(events)
            # 失败时尝试单条写入（降级）
            for event in events:
                await self._write_single(event)
    
    async def _write_batch(self, events: List[AuditEvent]) -> bool:
        """批量写入"""
        try:
            # 写入数据库
            if self.db:
                await self._write_to_db_batch(events)
            
            # 写入文件
            if self._file_handle:
                await self._write_to_file_batch(events)
            
            # 写入消息队列
            if self.mq:
                await self._write_to_mq_batch(events)
            
            return True
            
        except Exception as e:
            logger.error(f"Batch write failed: {e}")
            return False
    
    async def _write_single(self, event: AuditEvent) -> bool:
        """单条写入（降级用）"""
        try:
            if self.db:
                await self._write_to_db_single(event)
            
            if self._file_handle:
                await self._write_to_file_single(event)
            
            if self.mq:
                await self._write_to_mq_single(event)
            
            self._total_logged += 1
            return True
            
        except Exception as e:
            logger.error(f"Single write failed: {e}")
            self._total_failed += 1
            return False
    
    # ========== 具体写入实现 ==========
    
    async def _write_to_db_batch(self, events: List[AuditEvent]):
        """批量写入数据库"""
        # TODO: 实现批量 INSERT
        # 示例：INSERT INTO audit_logs (...) VALUES (...), (...), ...
        pass
    
    async def _write_to_db_single(self, event: AuditEvent):
        """单条写入数据库"""
        # TODO: 实现单条 INSERT
        pass
    
    async def _write_to_file_batch(self, events: List[AuditEvent]):
        """批量写入文件"""
        lines = [e.to_json() + "\n" for e in events]
        self._file_handle.writelines(lines)
        self._file_handle.flush()
    
    async def _write_to_file_single(self, event: AuditEvent):
        """单条写入文件"""
        self._file_handle.write(event.to_json() + "\n")
        self._file_handle.flush()
    
    async def _write_to_mq_batch(self, events: List[AuditEvent]):
        """批量写入消息队列"""
        # TODO: 实现 Kafka/RabbitMQ 批量发送
        pass
    
    async def _write_to_mq_single(self, event: AuditEvent):
        """单条写入消息队列"""
        # TODO: 实现 Kafka/RabbitMQ 单条发送
        pass
    
    # ========== 统计信息 ==========
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_logged": self._total_logged,
            "total_failed": self._total_failed,
            "buffer_size": len(self._buffer),
            "batch_size": self.batch_size,
            "flush_interval": self.flush_interval,
        }


# ========== 与 FastAPI 集成 ==========

from fastapi import Request

class AuditMiddleware:
    """审计中间件（用于 FastAPI）
    
    自动记录所有请求的审计日志
    """
    
    def __init__(
        self,
        audit_logger: AuditLogger,
        exclude_paths: Optional[List[str]] = None,
        sensitive_paths: Optional[List[str]] = None,
    ):
        self.audit = audit_logger
        self.exclude_paths = exclude_paths or [
            "/health", "/metrics", "/docs", "/openapi.json"
        ]
        self.sensitive_paths = sensitive_paths or ["/auth/login", "/auth/refresh"]
    
    async def __call__(self, request: Request, call_next):
        """处理请求"""
        path = request.url.path
        
        # 跳过排除的路径
        if any(path.startswith(p) for p in self.exclude_paths):
            return await call_next(request)
        
        # 记录开始时间
        start_time = datetime.utcnow()
        
        # 读取请求体（如果是敏感路径，不记录 body）
        body = None
        if request.method in ["POST", "PUT", "PATCH"]:
            if path not in self.sensitive_paths:
                try:
                    body_bytes = await request.body()
                    body = json.loads(body_bytes) if body_bytes else None
                    # 重新设置 body（因为读取后会被消费）
                    async def receive():
                        return {"type": "http.request", "body": body_bytes}
                    request._receive = receive
                except:
                    pass
        
        # 执行请求
        response = await call_next(request)
        
        # 计算耗时
        duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        # 获取用户信息
        user_id = getattr(request.state, "user_id", None)
        tenant_id = getattr(request.state, "tenant_id", None)
        
        # 创建审计事件
        event = AuditEvent(
            user_id=user_id,
            tenant_id=tenant_id,
            action=f"{request.method.lower()}:{path}",
            status="success" if response.status_code < 400 else "failure",
            request_payload=body,
            response_summary={"status_code": response.status_code},
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            request_id=request.headers.get("x-request-id") or str(uuid.uuid4()),
            duration_ms=duration,
        )
        
        # 记录错误信息（如果是失败）
        if response.status_code >= 400:
            event.error_code = str(response.status_code)
            event.level = AuditLevel.WARNING if response.status_code < 500 else AuditLevel.ERROR
        
        # 异步记录（不阻塞响应）
        asyncio.create_task(self.audit.log_event(event))
        
        return response


# ========== 便捷装饰器 ==========

def audit_action(
    action: str,
    resource_type: Optional[str] = None,
    resource_id_param: Optional[str] = None,
    log_request: bool = True,
    log_response: bool = False,
):
    """审计装饰器
    
    使用示例:
        @audit_action("dataset:create", "dataset")
        async def create_dataset(request, data: DatasetCreate):
            pass
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 获取 request 和 audit_logger
            request = kwargs.get('request')
            if not request and args:
                for arg in args:
                    if hasattr(arg, 'state'):
                        request = arg
                        break
            
            audit_logger = None
            if request and hasattr(request.app.state, 'audit_logger'):
                audit_logger = request.app.state.audit_logger
            
            # 获取用户信息
            user_id = getattr(request.state, "user_id", None) if request else None
            tenant_id = getattr(request.state, "tenant_id", None) if request else None
            
            # 获取资源 ID
            resource_id = None
            if resource_id_param:
                resource_id = kwargs.get(resource_id_param)
                if not resource_id and request:
                    resource_id = request.path_params.get(resource_id_param)
            
            # 记录请求
            start_time = datetime.utcnow()
            request_payload = kwargs if log_request else None
            
            try:
                result = await func(*args, **kwargs)
                
                # 记录成功
                if audit_logger:
                    duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                    await audit_logger.log(
                        action=action,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        status="success",
                        resource_type=resource_type,
                        resource_id=resource_id,
                        request_payload=request_payload,
                        duration_ms=duration,
                    )
                
                return result
                
            except Exception as e:
                # 记录失败
                if audit_logger:
                    duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                    await audit_logger.log(
                        action=action,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        status="failure",
                        resource_type=resource_type,
                        resource_id=resource_id,
                        request_payload=request_payload,
                        error_message=str(e),
                        duration_ms=duration,
                    )
                raise
        
        return wrapper
    return decorator
