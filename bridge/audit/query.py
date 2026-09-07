# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""审计日志查询与报告

提供审计日志的查询、分析和报告功能：
- 按时间/用户/资源等多维度查询
- 合规报告生成
- 异常行为检测
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path

from .logger import AuditEvent, AuditLevel


class ReportFormat(str, Enum):
    """报告格式"""
    JSON = "json"
    CSV = "csv"
    PDF = "pdf"
    HTML = "html"


@dataclass
class AuditQueryFilter:
    """审计查询过滤条件"""
    # 时间范围
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    
    # 主体
    user_id: Optional[str] = None
    username: Optional[str] = None
    tenant_id: Optional[str] = None
    
    # 行为
    action: Optional[str] = None
    actions: Optional[List[str]] = None  # 多个操作
    status: Optional[str] = None  # success/failure
    level: Optional[AuditLevel] = None
    
    # 客体
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    
    # 上下文
    client_ip: Optional[str] = None
    request_id: Optional[str] = None
    
    # 文本搜索
    keyword: Optional[str] = None
    
    # 分页
    offset: int = 0
    limit: int = 100
    
    def to_dict(self) -> Dict[str, Any]:
        result = {}
        if self.start_time:
            result["start_time"] = self.start_time.isoformat()
        if self.end_time:
            result["end_time"] = self.end_time.isoformat()
        if self.user_id:
            result["user_id"] = self.user_id
        if self.action:
            result["action"] = self.action
        if self.resource_type:
            result["resource_type"] = self.resource_type
        return result


@dataclass
class AuditQueryResult:
    """查询结果"""
    events: List[AuditEvent]
    total_count: int
    has_more: bool
    filter: AuditQueryFilter
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "events": [e.to_dict() for e in self.events],
            "total_count": self.total_count,
            "has_more": self.has_more,
            "filter": self.filter.to_dict(),
        }


@dataclass
class ActivitySummary:
    """活动摘要统计"""
    period_start: datetime
    period_end: datetime
    total_events: int
    unique_users: int
    unique_resources: int
    action_breakdown: Dict[str, int] = field(default_factory=dict)
    status_breakdown: Dict[str, int] = field(default_factory=dict)
    hourly_distribution: Dict[int, int] = field(default_factory=dict)  # 24小时分布
    top_users: List[Dict[str, Any]] = field(default_factory=list)
    top_resources: List[Dict[str, Any]] = field(default_factory=list)
    error_count: int = 0
    avg_duration_ms: float = 0.0


class AuditQuery:
    """审计日志查询器"""
    
    def __init__(self, db_session=None, log_file: Optional[str] = None):
        self.db = db_session
        self.log_file = log_file
    
    async def query(self, filter: AuditQueryFilter) -> AuditQueryResult:
        """查询审计日志"""
        events = []
        total = 0
        
        if self.db:
            events, total = await self._query_from_db(filter)
        elif self.log_file:
            events, total = await self._query_from_file(filter)
        
        has_more = (filter.offset + len(events)) < total
        
        return AuditQueryResult(
            events=events,
            total_count=total,
            has_more=has_more,
            filter=filter
        )
    
    async def get_event_by_id(self, event_id: str) -> Optional[AuditEvent]:
        """根据 ID 获取单个事件"""
        if self.db:
            return await self._get_event_by_id_from_db(event_id)
        elif self.log_file:
            return await self._get_event_by_id_from_file(event_id)
        return None
    
    async def get_user_activity(
        self,
        user_id: str,
        days: int = 7
    ) -> ActivitySummary:
        """获取用户活动摘要"""
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        
        filter = AuditQueryFilter(
            user_id=user_id,
            start_time=start_time,
            end_time=end_time,
            limit=10000
        )
        
        result = await self.query(filter)
        return self._calculate_summary(result.events, start_time, end_time)
    
    async def get_resource_activity(
        self,
        resource_type: str,
        resource_id: str,
        days: int = 30
    ) -> ActivitySummary:
        """获取资源活动摘要"""
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        
        filter = AuditQueryFilter(
            resource_type=resource_type,
            resource_id=resource_id,
            start_time=start_time,
            end_time=end_time,
            limit=10000
        )
        
        result = await self.query(filter)
        return self._calculate_summary(result.events, start_time, end_time)
    
    async def search_by_keyword(
        self,
        keyword: str,
        days: int = 7,
        limit: int = 100
    ) -> AuditQueryResult:
        """关键词搜索"""
        filter = AuditQueryFilter(
            keyword=keyword,
            start_time=datetime.utcnow() - timedelta(days=days),
            end_time=datetime.utcnow(),
            limit=limit
        )
        return await self.query(filter)
    
    async def get_failed_actions(
        self,
        hours: int = 24,
        min_count: int = 5
    ) -> List[Dict[str, Any]]:
        """获取高频失败操作（用于安全监控）"""
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours)
        
        if self.db:
            return await self._get_failed_actions_from_db(start_time, end_time, min_count)
        elif self.log_file:
            return await self._get_failed_actions_from_file(start_time, end_time, min_count)
        
        return []
    
    async def get_login_anomalies(
        self,
        user_id: str,
        days: int = 7
    ) -> List[AuditEvent]:
        """检测登录异常（时间/IP 异常）"""
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        
        # 获取用户历史登录事件
        filter = AuditQueryFilter(
            user_id=user_id,
            action="auth:login",
            start_time=start_time,
            end_time=end_time,
            limit=1000
        )
        
        result = await self.query(filter)
        events = result.events
        
        if len(events) < 5:
            # 数据不足，无法检测异常
            return []
        
        # 分析登录模式
        anomalies = []
        
        # 1. 检测异常时间（非工作时间）
        for event in events:
            hour = event.timestamp.hour
            # 假设工作时间是 9:00-18:00
            if hour < 6 or hour > 22:
                event.metadata = event.metadata or {}
                event.metadata["anomaly_type"] = "unusual_time"
                event.metadata["anomaly_reason"] = f"Login at unusual hour: {hour}:00"
                anomalies.append(event)
        
        # 2. 检测异常 IP（如果用户通常从固定 IP 登录）
        ip_counts = {}
        for event in events:
            if event.client_ip:
                ip_counts[event.client_ip] = ip_counts.get(event.client_ip, 0) + 1
        
        if ip_counts:
            # 找出最常用的 IP
            common_ip = max(ip_counts, key=ip_counts.get)
            common_ip_count = ip_counts[common_ip]
            
            # 如果某个 IP 使用次数明显少于常用 IP，标记为异常
            threshold = max(1, common_ip_count // 3)
            for event in events:
                if event.client_ip and event.client_ip != common_ip:
                    if ip_counts.get(event.client_ip, 0) < threshold:
                        event.metadata = event.metadata or {}
                        event.metadata["anomaly_type"] = "unusual_ip"
                        event.metadata["anomaly_reason"] = f"Login from unusual IP: {event.client_ip}"
                        if event not in anomalies:
                            anomalies.append(event)
        
        # 3. 检测频繁失败
        failure_count = sum(1 for e in events if e.status == "failure")
        if failure_count > len(events) * 0.3:  # 失败率超过 30%
            for event in events:
                if event.status == "failure":
                    event.metadata = event.metadata or {}
                    event.metadata["anomaly_type"] = "frequent_failures"
                    event.metadata["anomaly_reason"] = "High failure rate detected"
                    if event not in anomalies:
                        anomalies.append(event)
        
        return anomalies
    
    def _calculate_summary(
        self,
        events: List[AuditEvent],
        start_time: datetime,
        end_time: datetime
    ) -> ActivitySummary:
        """计算活动摘要"""
        if not events:
            return ActivitySummary(
                period_start=start_time,
                period_end=end_time,
                total_events=0,
                unique_users=0,
                unique_resources=0
            )
        
        # 统计
        unique_users = set()
        unique_resources = set()
        action_counts = {}
        status_counts = {}
        hourly_counts = {h: 0 for h in range(24)}
        user_counts = {}
        resource_counts = {}
        error_count = 0
        total_duration = 0
        
        for event in events:
            if event.user_id:
                unique_users.add(event.user_id)
                user_counts[event.user_id] = user_counts.get(event.user_id, 0) + 1
            
            if event.resource_id:
                unique_resources.add(event.resource_id)
                resource_counts[event.resource_id] = resource_counts.get(event.resource_id, 0) + 1
            
            action_counts[event.action] = action_counts.get(event.action, 0) + 1
            status_counts[event.status] = status_counts.get(event.status, 0) + 1
            
            hour = event.timestamp.hour
            hourly_counts[hour] = hourly_counts.get(hour, 0) + 1
            
            if event.status == "failure":
                error_count += 1
            
            if event.duration_ms:
                total_duration += event.duration_ms
        
        # Top 10 用户和资源
        top_users = [
            {"user_id": uid, "count": count}
            for uid, count in sorted(user_counts.items(), key=lambda x: -x[1])[:10]
        ]
        
        top_resources = [
            {"resource_id": rid, "count": count}
            for rid, count in sorted(resource_counts.items(), key=lambda x: -x[1])[:10]
        ]
        
        avg_duration = total_duration / len(events) if events else 0
        
        return ActivitySummary(
            period_start=start_time,
            period_end=end_time,
            total_events=len(events),
            unique_users=len(unique_users),
            unique_resources=len(unique_resources),
            action_breakdown=action_counts,
            status_breakdown=status_counts,
            hourly_distribution=hourly_counts,
            top_users=top_users,
            top_resources=top_resources,
            error_count=error_count,
            avg_duration_ms=avg_duration
        )
    
    # ========== 数据库查询实现 ==========
    
    async def _query_from_db(self, filter: AuditQueryFilter) -> Tuple[List[AuditEvent], int]:
        """从数据库查询"""
        from sqlalchemy import select, func, and_, or_
        from .db_models import DBAuditLog
        
        # 构建查询条件
        conditions = []
        
        if filter.start_time:
            conditions.append(DBAuditLog.timestamp >= filter.start_time)
        if filter.end_time:
            conditions.append(DBAuditLog.timestamp <= filter.end_time)
        if filter.user_id:
            conditions.append(DBAuditLog.user_id == filter.user_id)
        if filter.username:
            conditions.append(DBAuditLog.username == filter.username)
        if filter.tenant_id:
            conditions.append(DBAuditLog.tenant_id == filter.tenant_id)
        if filter.action:
            conditions.append(DBAuditLog.action == filter.action)
        if filter.actions:
            conditions.append(DBAuditLog.action.in_(filter.actions))
        if filter.status:
            conditions.append(DBAuditLog.status == filter.status)
        if filter.level:
            conditions.append(DBAuditLog.level == filter.level.value)
        if filter.resource_type:
            conditions.append(DBAuditLog.resource_type == filter.resource_type)
        if filter.resource_id:
            conditions.append(DBAuditLog.resource_id == filter.resource_id)
        if filter.client_ip:
            conditions.append(DBAuditLog.client_ip == filter.client_ip)
        if filter.request_id:
            conditions.append(DBAuditLog.request_id == filter.request_id)
        if filter.keyword:
            # 关键词搜索：在 action, error_message, request_payload 中搜索
            keyword_pattern = f"%{filter.keyword}%"
            keyword_conditions = [
                DBAuditLog.action.ilike(keyword_pattern),
                DBAuditLog.error_message.ilike(keyword_pattern),
                DBAuditLog.username.ilike(keyword_pattern),
            ]
            conditions.append(or_(*keyword_conditions))
        
        # 查询总数
        count_query = select(func.count(DBAuditLog.id))
        if conditions:
            count_query = count_query.where(and_(*conditions))
        
        result = await self.db.execute(count_query)
        total = result.scalar() or 0
        
        # 查询数据
        query = select(DBAuditLog)
        if conditions:
            query = query.where(and_(*conditions))
        
        # 排序：按时间倒序
        query = query.order_by(DBAuditLog.timestamp.desc())
        
        # 分页
        query = query.offset(filter.offset).limit(filter.limit)
        
        result = await self.db.execute(query)
        db_logs = result.scalars().all()
        
        # 转换为 AuditEvent
        events = [self._db_log_to_event(log) for log in db_logs]
        
        return events, total
    
    async def _get_event_by_id_from_db(self, event_id: str) -> Optional[AuditEvent]:
        """从数据库根据 ID 获取事件"""
        from sqlalchemy import select
        from .db_models import DBAuditLog
        
        query = select(DBAuditLog).where(DBAuditLog.event_id == event_id)
        result = await self.db.execute(query)
        db_log = result.scalar_one_or_none()
        
        if db_log:
            return self._db_log_to_event(db_log)
        return None
    
    async def _get_failed_actions_from_db(
        self,
        start_time: datetime,
        end_time: datetime,
        min_count: int
    ) -> List[Dict[str, Any]]:
        """从数据库获取高频失败操作"""
        from sqlalchemy import select, func, and_
        from .db_models import DBAuditLog
        
        query = select(
            DBAuditLog.action,
            func.count(DBAuditLog.id).label('fail_count')
        ).where(
            and_(
                DBAuditLog.status == 'failure',
                DBAuditLog.timestamp >= start_time,
                DBAuditLog.timestamp <= end_time
            )
        ).group_by(
            DBAuditLog.action
        ).having(
            func.count(DBAuditLog.id) >= min_count
        ).order_by(
            func.count(DBAuditLog.id).desc()
        )
        
        result = await self.db.execute(query)
        rows = result.all()
        
        return [
            {"action": row.action, "fail_count": row.fail_count}
            for row in rows
        ]
    
    def _db_log_to_event(self, db_log) -> AuditEvent:
        """将数据库模型转换为 AuditEvent"""
        return AuditEvent(
            event_id=db_log.event_id,
            timestamp=db_log.timestamp,
            level=AuditLevel(db_log.level) if db_log.level else AuditLevel.INFO,
            user_id=db_log.user_id,
            username=db_log.username,
            tenant_id=db_log.tenant_id,
            session_id=db_log.session_id,
            action=db_log.action,
            status=db_log.status,
            resource_type=db_log.resource_type,
            resource_id=db_log.resource_id,
            request_payload=db_log.request_payload,
            response_summary=db_log.response_summary,
            error_message=db_log.error_message,
            error_code=db_log.error_code,
            client_ip=db_log.client_ip,
            user_agent=db_log.user_agent,
            request_id=db_log.request_id,
            duration_ms=db_log.duration_ms,
            metadata=db_log.metadata_json or {},
        )
    
    # ========== 文件查询实现 ==========
    
    async def _query_from_file(self, filter: AuditQueryFilter) -> Tuple[List[AuditEvent], int]:
        """从日志文件查询"""
        if not self.log_file or not Path(self.log_file).exists():
            return [], 0
        
        events = []
        
        with open(self.log_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                
                try:
                    data = json.loads(line)
                    event = self._dict_to_event(data)
                    
                    # 应用过滤器
                    if self._matches_filter(event, filter):
                        events.append(event)
                        
                except json.JSONDecodeError:
                    continue
        
        # 按时间倒序排序
        events.sort(key=lambda e: e.timestamp, reverse=True)
        
        total = len(events)
        
        # 分页
        start = filter.offset
        end = filter.offset + filter.limit
        paginated_events = events[start:end]
        
        return paginated_events, total
    
    async def _get_event_by_id_from_file(self, event_id: str) -> Optional[AuditEvent]:
        """从文件根据 ID 获取事件"""
        if not self.log_file or not Path(self.log_file).exists():
            return None
        
        with open(self.log_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                
                try:
                    data = json.loads(line)
                    if data.get('event_id') == event_id:
                        return self._dict_to_event(data)
                except json.JSONDecodeError:
                    continue
        
        return None
    
    async def _get_failed_actions_from_file(
        self,
        start_time: datetime,
        end_time: datetime,
        min_count: int
    ) -> List[Dict[str, Any]]:
        """从文件获取高频失败操作"""
        if not self.log_file or not Path(self.log_file).exists():
            return []
        
        action_counts = {}
        
        with open(self.log_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                
                try:
                    data = json.loads(line)
                    
                    # 检查时间和状态
                    timestamp = datetime.fromisoformat(data.get('timestamp', ''))
                    if timestamp < start_time or timestamp > end_time:
                        continue
                    
                    if data.get('status') != 'failure':
                        continue
                    
                    action = data.get('action', 'unknown')
                    action_counts[action] = action_counts.get(action, 0) + 1
                    
                except (json.JSONDecodeError, ValueError):
                    continue
        
        # 过滤并排序
        result = [
            {"action": action, "fail_count": count}
            for action, count in action_counts.items()
            if count >= min_count
        ]
        result.sort(key=lambda x: -x["fail_count"])
        
        return result
    
    def _dict_to_event(self, data: Dict[str, Any]) -> AuditEvent:
        """将字典转换为 AuditEvent"""
        # 解析时间戳
        timestamp = data.get('timestamp')
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        elif timestamp is None:
            timestamp = datetime.utcnow()
        
        # 解析级别
        level = data.get('level', 'info')
        if isinstance(level, str):
            level = AuditLevel(level)
        
        return AuditEvent(
            event_id=data.get('event_id', ''),
            timestamp=timestamp,
            level=level,
            user_id=data.get('user_id'),
            username=data.get('username'),
            tenant_id=data.get('tenant_id'),
            session_id=data.get('session_id'),
            action=data.get('action', ''),
            status=data.get('status', 'pending'),
            resource_type=data.get('resource_type'),
            resource_id=data.get('resource_id'),
            request_payload=data.get('request_payload'),
            response_summary=data.get('response_summary'),
            error_message=data.get('error_message'),
            error_code=data.get('error_code'),
            client_ip=data.get('client_ip'),
            user_agent=data.get('user_agent'),
            request_id=data.get('request_id'),
            duration_ms=data.get('duration_ms'),
            metadata=data.get('metadata', {}),
        )
    
    def _matches_filter(self, event: AuditEvent, filter: AuditQueryFilter) -> bool:
        """检查事件是否匹配过滤条件"""
        if filter.start_time and event.timestamp < filter.start_time:
            return False
        if filter.end_time and event.timestamp > filter.end_time:
            return False
        if filter.user_id and event.user_id != filter.user_id:
            return False
        if filter.username and event.username != filter.username:
            return False
        if filter.tenant_id and event.tenant_id != filter.tenant_id:
            return False
        if filter.action and event.action != filter.action:
            return False
        if filter.actions and event.action not in filter.actions:
            return False
        if filter.status and event.status != filter.status:
            return False
        if filter.level and event.level != filter.level:
            return False
        if filter.resource_type and event.resource_type != filter.resource_type:
            return False
        if filter.resource_id and event.resource_id != filter.resource_id:
            return False
        if filter.client_ip and event.client_ip != filter.client_ip:
            return False
        if filter.request_id and event.request_id != filter.request_id:
            return False
        if filter.keyword:
            keyword_lower = filter.keyword.lower()
            searchable = [
                event.action or '',
                event.error_message or '',
                event.username or '',
                json.dumps(event.request_payload or {}),
            ]
            if not any(keyword_lower in s.lower() for s in searchable):
                return False
        
        return True


class AuditReportGenerator:
    """审计报告生成器"""
    
    def __init__(self, query: AuditQuery):
        self.query = query
    
    async def generate_compliance_report(
        self,
        start_date: datetime,
        end_date: datetime,
        tenant_id: Optional[str] = None,
        format: ReportFormat = ReportFormat.JSON
    ) -> str:
        """生成合规报告"""
        filter = AuditQueryFilter(
            start_time=start_date,
            end_time=end_date,
            tenant_id=tenant_id,
            limit=100000  # 获取全部
        )
        
        result = await self.query.query(filter)
        
        # 生成报告内容
        if format == ReportFormat.JSON:
            return self._generate_json_report(result, start_date, end_date)
        elif format == ReportFormat.CSV:
            return self._generate_csv_report(result)
        elif format == ReportFormat.HTML:
            return self._generate_html_report(result, start_date, end_date)
        else:
            raise ValueError(f"Unsupported format: {format}")
    
    async def generate_user_activity_report(
        self,
        user_id: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """生成用户活动报告"""
        summary = await self.query.get_user_activity(user_id, days)
        
        return {
            "user_id": user_id,
            "report_period_days": days,
            "period_start": summary.period_start.isoformat(),
            "period_end": summary.period_end.isoformat(),
            "total_actions": summary.total_events,
            "success_rate": (
                (summary.total_events - summary.error_count) / summary.total_events * 100
                if summary.total_events > 0 else 0
            ),
            "action_breakdown": summary.action_breakdown,
            "hourly_activity_pattern": summary.hourly_distribution,
            "most_accessed_resources": summary.top_resources[:5],
            "average_response_time_ms": summary.avg_duration_ms,
        }
    
    async def generate_security_report(
        self,
        days: int = 7
    ) -> Dict[str, Any]:
        """生成安全审计报告"""
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        
        # 获取失败操作
        failed_actions = await self.query.get_failed_actions(hours=24)
        
        # 获取登录事件
        login_filter = AuditQueryFilter(
            action="auth:login",
            start_time=start_time,
            end_time=end_time,
            limit=10000
        )
        login_result = await self.query.query(login_filter)
        
        # 统计
        total_logins = len(login_result.events)
        failed_logins = sum(1 for e in login_result.events if e.status == "failure")
        
        # 检测异常
        anomalies = []
        for event in login_result.events:
            if event.status == "failure":
                anomalies.append({
                    "type": "failed_login",
                    "timestamp": event.timestamp.isoformat(),
                    "username": event.username,
                    "client_ip": event.client_ip,
                })
        
        return {
            "report_period_days": days,
            "period_start": start_time.isoformat(),
            "period_end": end_time.isoformat(),
            "login_statistics": {
                "total_attempts": total_logins,
                "failed_attempts": failed_logins,
                "success_rate": (
                    (total_logins - failed_logins) / total_logins * 100
                    if total_logins > 0 else 0
                ),
            },
            "high_risk_operations": failed_actions,
            "detected_anomalies": anomalies[:100],  # 最多100条
            "recommendations": self._generate_security_recommendations(
                failed_logins, failed_actions
            ),
        }
    
    def _generate_security_recommendations(
        self,
        failed_logins: int,
        failed_actions: List[Dict]
    ) -> List[str]:
        """生成安全建议"""
        recommendations = []
        
        if failed_logins > 10:
            recommendations.append(
                "检测到大量登录失败，建议检查是否存在暴力破解攻击"
            )
        
        if failed_actions:
            recommendations.append(
                f"检测到 {len(failed_actions)} 个高频失败操作，建议审查相关权限配置"
            )
        
        if not recommendations:
            recommendations.append("未发现明显安全问题")
        
        return recommendations
    
    def _generate_json_report(
        self,
        result: AuditQueryResult,
        start_date: datetime,
        end_date: datetime
    ) -> str:
        """生成 JSON 格式报告"""
        report = {
            "report_type": "compliance_audit",
            "generated_at": datetime.utcnow().isoformat(),
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "summary": {
                "total_events": result.total_count,
                "returned_events": len(result.events),
            },
            "events": [e.to_dict() for e in result.events],
        }
        return json.dumps(report, indent=2, default=str)
    
    def _generate_csv_report(self, result: AuditQueryResult) -> str:
        """生成 CSV 格式报告"""
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # 表头
        writer.writerow([
            "event_id", "timestamp", "user_id", "username", "action",
            "status", "resource_type", "resource_id", "client_ip", "duration_ms"
        ])
        
        # 数据
        for event in result.events:
            writer.writerow([
                event.event_id,
                event.timestamp.isoformat(),
                event.user_id,
                event.username,
                event.action,
                event.status,
                event.resource_type,
                event.resource_id,
                event.client_ip,
                event.duration_ms,
            ])
        
        return output.getvalue()
    
    def _generate_html_report(
        self,
        result: AuditQueryResult,
        start_date: datetime,
        end_date: datetime
    ) -> str:
        """生成 HTML 格式报告"""
        # 简化版 HTML 报告
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>AOF Audit Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #4CAF50; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .summary {{ margin-bottom: 20px; }}
    </style>
</head>
<body>
    <h1>AOF Audit Report</h1>
    <div class="summary">
        <p><strong>Period:</strong> {start_date.isoformat()} to {end_date.isoformat()}</p>
        <p><strong>Total Events:</strong> {result.total_count}</p>
        <p><strong>Generated:</strong> {datetime.utcnow().isoformat()}</p>
    </div>
    <table>
        <tr>
            <th>Timestamp</th>
            <th>User</th>
            <th>Action</th>
            <th>Status</th>
            <th>Resource</th>
            <th>Duration (ms)</th>
        </tr>
"""
        for event in result.events[:1000]:  # 最多显示1000条
            html += f"""
        <tr>
            <td>{event.timestamp.isoformat()}</td>
            <td>{event.username or event.user_id or 'N/A'}</td>
            <td>{event.action}</td>
            <td>{event.status}</td>
            <td>{event.resource_type}:{event.resource_id or '*'}</td>
            <td>{event.duration_ms or 'N/A'}</td>
        </tr>
"""
        
        html += """
    </table>
</body>
</html>
"""
        return html
