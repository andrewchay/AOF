"""任务数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
import uuid
import json


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"           # 等待中
    QUEUED = "queued"             # 已入队
    RUNNING = "running"           # 执行中
    SUCCESS = "success"           # 成功
    FAILURE = "failure"           # 失败
    CANCELLED = "cancelled"       # 已取消
    TIMEOUT = "timeout"           # 超时


class TaskPriority(int, Enum):
    """任务优先级"""
    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


@dataclass
class Task:
    """任务定义"""
    # 基本信息
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_type: str = ""                           # 任务类型：pagerank, community, etc.
    name: Optional[str] = None
    description: Optional[str] = None
    
    # 状态
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    
    # 参数
    dataset_name: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    # 执行信息
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    worker_id: Optional[str] = None
    
    # 元数据
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    
    # 进度
    progress: float = 0.0                          # 0-100
    progress_message: Optional[str] = None
    
    # 重试
    max_retries: int = 3
    retry_count: int = 0
    
    # 超时
    timeout_seconds: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "task_type": self.task_type,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "dataset_name": self.dataset_name,
            "parameters": self.parameters,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "worker_id": self.worker_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "tags": self.tags,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }
    
    @property
    def duration_seconds(self) -> Optional[float]:
        """计算执行时长"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        elif self.started_at:
            return (datetime.utcnow() - self.started_at).total_seconds()
        return None
    
    def is_finished(self) -> bool:
        """检查是否已完成"""
        return self.status in (
            TaskStatus.SUCCESS,
            TaskStatus.FAILURE,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
        )
    
    def can_retry(self) -> bool:
        """检查是否可以重试"""
        return self.retry_count < self.max_retries and self.status == TaskStatus.FAILURE


@dataclass
class TaskResult:
    """任务结果"""
    task_id: str
    status: TaskStatus
    
    # 结果数据
    data: Optional[Any] = None
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None
    
    # 元数据
    output_files: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    
    # 时间
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "data": self.data,
            "error_message": self.error_message,
            "output_files": self.output_files,
            "metrics": self.metrics,
            "created_at": self.created_at.isoformat(),
        }
    
    def is_success(self) -> bool:
        """检查是否成功"""
        return self.status == TaskStatus.SUCCESS


@dataclass
class TaskQuery:
    """任务查询条件"""
    status: Optional[TaskStatus] = None
    task_type: Optional[str] = None
    dataset_name: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    
    # 时间范围
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None
    
    # 分页
    limit: int = 100
    offset: int = 0
    
    # 排序
    order_by: str = "created_at"
    order_desc: bool = True


@dataclass
class TaskStats:
    """任务统计"""
    total_tasks: int = 0
    pending_count: int = 0
    running_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    
    avg_duration_seconds: float = 0.0
    max_duration_seconds: float = 0.0
    min_duration_seconds: float = 0.0
    
    # 按类型统计
    by_type: Dict[str, int] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_tasks": self.total_tasks,
            "pending_count": self.pending_count,
            "running_count": self.running_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "avg_duration_seconds": self.avg_duration_seconds,
            "by_type": self.by_type,
        }
