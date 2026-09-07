"""审计日志模块单元测试

运行:
    cd /path/to/AOF
    python -m pytest tests/test_audit_logger.py -v
"""

import pytest
import json
import tempfile
import os

from bridge.audit.logger import (
    AuditEvent, AuditLevel, AuditLogger, DataMasker, 
    AuditConfigurationError, FileOutbox
)


class TestDataMasker:
    """测试数据脱敏"""
    
    def test_mask_password_field(self):
        """测试密码字段脱敏"""
        data = {
            "username": "test",
            "password": "secret123",
            "email": "test@example.com"
        }
        masked = DataMasker.mask_sensitive_data(data)
        
        assert masked["username"] == "test"
        # 长度大于4的密码：保留前后2位，中间脱敏
        assert masked["password"] == "se****23"
        assert masked["email"] == "test@example.com"
    
    def test_mask_api_key(self):
        """测试 API key 脱敏"""
        data = {
            "api_key": "sk-1234567890abcdef",
            "name": "my_key"
        }
        masked = DataMasker.mask_sensitive_data(data)
        
        # 部分脱敏：显示前后2位
        assert masked["api_key"] == "sk****ef"
    
    def test_mask_nested_data(self):
        """测试嵌套数据结构脱敏"""
        data = {
            "user": {
                "name": "test",
                "secret_token": "token123"
            },
            "credentials": ["cred1", "cred2"]
        }
        masked = DataMasker.mask_sensitive_data(data)
        
        # 长度大于4的 token：保留前后2位，中间脱敏
        assert masked["user"]["secret_token"] == "to****23"
        # 列表中的敏感词也会被处理（如果元素是字符串且匹配）
    
    def test_mask_short_value(self):
        """测试短值脱敏"""
        data = {"password": "12345678"}
        masked = DataMasker.mask_sensitive_data(data)
        
        # 长度大于4：保留前后2位，中间脱敏
        assert masked["password"] == "12****78"
    
    def test_mask_very_short_value(self):
        """测试非常短的值脱敏"""
        data = {"password": "1234"}
        masked = DataMasker.mask_sensitive_data(data)
        
        # 长度<=4的值：完全脱敏
        assert masked["password"] == "****"


class TestAuditEvent:
    """测试审计事件"""
    
    def test_event_creation(self):
        """测试创建审计事件"""
        event = AuditEvent(
            user_id="user_123",
            action="dataset:create",
            status="success",
            resource_type="dataset",
            resource_id="ds_456",
            request_payload={"name": "test_dataset"},
            duration_ms=150
        )
        
        assert event.user_id == "user_123"
        assert event.action == "dataset:create"
        assert event.status == "success"
        assert event.event_id is not None  # 自动生成 UUID
        assert event.timestamp is not None
        assert event.level == AuditLevel.INFO  # 默认级别
    
    def test_event_to_dict(self):
        """测试事件序列化"""
        event = AuditEvent(
            user_id="user_123",
            action="test:action",
            status="success"
        )
        
        data = event.to_dict()
        
        assert data["user_id"] == "user_123"
        assert data["action"] == "test:action"
        assert data["status"] == "success"
        assert isinstance(data["timestamp"], str)
        assert data["level"] == "info"
    
    def test_event_to_json(self):
        """测试事件转 JSON"""
        event = AuditEvent(
            user_id="user_123",
            action="test:action"
        )
        
        json_str = event.to_json()
        data = json.loads(json_str)
        
        assert data["user_id"] == "user_123"
        assert "event_id" in data


class TestAuditLogger:
    """测试审计日志记录器"""
    
    @pytest.fixture
    def temp_log_file(self):
        """创建临时日志文件"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
            yield f.name
        os.unlink(f.name)
    
    @pytest.fixture
    def temp_outbox_dir(self):
        """创建临时 outbox 目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def logger_with_file(self, temp_log_file):
        """创建带文件后端的测试日志记录器"""
        return AuditLogger(
            log_file=temp_log_file,
            batch_size=10,
            flush_interval=1,
            enable_masking=True,
            strict_mode=False  # 非严格模式，允许测试
        )
    
    @pytest.fixture
    def logger_with_outbox(self, temp_outbox_dir):
        """创建带 outbox 后端的测试日志记录器"""
        return AuditLogger(
            mq_outbox_dir=temp_outbox_dir,
            batch_size=10,
            flush_interval=1,
            enable_masking=True,
            strict_mode=False
        )
    
    def test_no_backend_raises_error(self):
        """测试没有配置后端时抛出异常"""
        with pytest.raises(AuditConfigurationError) as exc_info:
            AuditLogger(strict_mode=True)
        
        assert "At least one audit backend must be configured" in str(exc_info.value)
    
    def test_non_strict_mode_allows_no_backend(self):
        """测试非严格模式允许无后端"""
        logger = AuditLogger(strict_mode=False)
        assert logger is not None
    
    @pytest.mark.asyncio
    async def test_log_event(self, logger_with_file):
        """测试记录事件"""
        event = AuditEvent(
            user_id="user_123",
            action="dataset:read",
            status="success"
        )
        
        result = await logger_with_file.log_event(event)
        assert result is True
        
        # 检查缓冲区
        assert len(logger_with_file._buffer) == 1
    
    @pytest.mark.asyncio
    async def test_log_with_masking(self, logger_with_file):
        """测试带脱敏的记录"""
        event = AuditEvent(
            user_id="user_123",
            action="auth:login",
            status="success",
            request_payload={
                "username": "test",
                "password": "secret123"  # 应该被脱敏
            }
        )
        
        await logger_with_file.log_event(event)
        
        # 检查缓冲区中的事件是否已脱敏
        buffered_event = logger_with_file._buffer[0]
        # 长度大于4的密码：保留前后2位，中间脱敏
        assert buffered_event.request_payload["password"] == "se****23"
        assert buffered_event.request_payload["username"] == "test"
    
    @pytest.mark.asyncio
    async def test_log_convenience_method(self, logger_with_file):
        """测试便捷的 log 方法"""
        result = await logger_with_file.log(
            action="search:execute",
            user_id="user_456",
            status="success",
            resource_type="dataset",
            resource_id="ds_789",
            duration_ms=250
        )
        
        assert result is True
        assert len(logger_with_file._buffer) == 1
        
        event = logger_with_file._buffer[0]
        assert event.action == "search:execute"
        assert event.user_id == "user_456"
        assert event.duration_ms == 250
    
    @pytest.mark.asyncio
    async def test_immediate_flush(self, logger_with_file, temp_log_file):
        """测试立即写入"""
        event = AuditEvent(
            user_id="user_123",
            action="critical:delete",
            status="success"
        )
        
        # immediate=True 应该直接写入（不经过缓冲区）
        result = await logger_with_file.log_event(event, immediate=True)
        assert result is True
        
        # 验证文件中有内容
        with open(temp_log_file, 'r') as f:
            content = f.read()
            assert "critical:delete" in content
    
    @pytest.mark.asyncio
    async def test_logger_stats(self, logger_with_file):
        """测试统计信息"""
        # 记录一些事件
        for i in range(5):
            await logger_with_file.log(
                action=f"action:{i}",
                user_id=f"user_{i}"
            )
        
        stats = logger_with_file.get_stats()
        
        assert stats["buffer_size"] == 5
        assert stats["batch_size"] == 10
        assert stats["total_logged"] == 0  # 尚未刷新
        assert stats["total_failed"] == 0
    
    def test_logger_start_stop(self, logger_with_file):
        """测试启动和停止"""
        import asyncio
        
        # 启动
        asyncio.run(logger_with_file.start())
        assert logger_with_file._running is True
        assert logger_with_file._flush_task is not None
        
        # 停止
        asyncio.run(logger_with_file.stop())
        assert logger_with_file._running is False
    
    @pytest.mark.asyncio
    async def test_outbox_write(self, logger_with_outbox, temp_outbox_dir):
        """测试 outbox 写入"""
        event = AuditEvent(
            user_id="user_123",
            action="mq:test",
            status="success"
        )
        
        result = await logger_with_outbox.log_event(event, immediate=True)
        assert result is True
        
        # 验证 outbox 目录中有文件
        files = list(os.listdir(temp_outbox_dir))
        assert len(files) > 0


class TestFileOutbox:
    """测试文件 Outbox"""
    
    @pytest.fixture
    def temp_outbox_dir(self):
        """创建临时 outbox 目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.mark.asyncio
    async def test_write_batch(self, temp_outbox_dir):
        """测试批量写入"""
        outbox = FileOutbox(temp_outbox_dir)
        
        events = [
            AuditEvent(user_id="user_1", action="action:1"),
            AuditEvent(user_id="user_2", action="action:2"),
        ]
        
        result = await outbox.write_batch(events)
        assert result is True
        
        # 验证文件创建
        files = list(os.listdir(temp_outbox_dir))
        assert len(files) == 1
        assert files[0].startswith("audit_outbox_")
    
    @pytest.mark.asyncio
    async def test_read_pending_events(self, temp_outbox_dir):
        """测试读取待处理事件"""
        outbox = FileOutbox(temp_outbox_dir)
        
        events = [
            AuditEvent(user_id="user_1", action="action:1"),
            AuditEvent(user_id="user_2", action="action:2"),
        ]
        
        await outbox.write_batch(events)
        
        # 读取事件
        pending = outbox.read_pending_events()
        assert len(pending) == 2


class TestAuditLevel:
    """测试审计级别"""
    
    def test_level_values(self):
        """测试级别值"""
        assert AuditLevel.DEBUG.value == "debug"
        assert AuditLevel.INFO.value == "info"
        assert AuditLevel.WARNING.value == "warning"
        assert AuditLevel.ERROR.value == "error"
        assert AuditLevel.CRITICAL.value == "critical"
    
    def test_level_comparison(self):
        """测试级别枚举"""
        levels = [AuditLevel.DEBUG, AuditLevel.INFO, AuditLevel.WARNING, 
                  AuditLevel.ERROR, AuditLevel.CRITICAL]
        
        # 检查都是有效的枚举值
        for level in levels:
            assert isinstance(level, AuditLevel)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
