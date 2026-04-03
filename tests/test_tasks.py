"""任务队列单元测试

运行:
    cd /path/to/AOF
    python -m pytest tests/test_tasks.py -v
"""

import pytest
import asyncio
from datetime import datetime

from bridge.tasks.models import Task, TaskStatus, TaskPriority, TaskResult


class TestTaskModel:
    """测试任务模型"""
    
    def test_task_creation(self):
        """测试创建任务"""
        task = Task(
            task_type="pagerank",
            dataset_name="test_graph",
            priority=TaskPriority.HIGH,
        )
        
        assert task.task_type == "pagerank"
        assert task.dataset_name == "test_graph"
        assert task.priority == TaskPriority.HIGH
        assert task.status == TaskStatus.PENDING
        assert task.id is not None
        assert task.created_at is not None
    
    def test_task_is_finished(self):
        """测试完成状态判断"""
        finished_statuses = [
            TaskStatus.SUCCESS,
            TaskStatus.FAILURE,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
        ]
        
        for status in finished_statuses:
            task = Task(status=status)
            assert task.is_finished() is True
        
        unfinished_statuses = [
            TaskStatus.PENDING,
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
        ]
        
        for status in unfinished_statuses:
            task = Task(status=status)
            assert task.is_finished() is False
    
    def test_task_can_retry(self):
        """测试可重试判断"""
        task = Task(
            status=TaskStatus.FAILURE,
            retry_count=0,
            max_retries=3,
        )
        assert task.can_retry() is True
        
        task.retry_count = 3
        assert task.can_retry() is False
        
        task.status = TaskStatus.SUCCESS
        assert task.can_retry() is False
    
    def test_task_duration(self):
        """测试执行时长计算"""
        task = Task()
        assert task.duration_seconds is None
        
        task.started_at = datetime.utcnow()
        # 还在运行中
        assert task.duration_seconds is not None
        assert task.duration_seconds >= 0
    
    def test_task_to_dict(self):
        """测试转字典"""
        task = Task(
            task_type="test",
            status=TaskStatus.SUCCESS,
            progress=50.0,
        )
        
        data = task.to_dict()
        
        assert data["task_type"] == "test"
        assert data["status"] == "success"
        assert data["progress"] == 50.0


class TestTaskResult:
    """测试任务结果"""
    
    def test_result_success(self):
        """测试成功结果"""
        result = TaskResult(
            task_id="task_123",
            status=TaskStatus.SUCCESS,
            data={"pagerank": [{"node": "a", "score": 0.5}]},
        )
        
        assert result.is_success() is True
        assert result.task_id == "task_123"
        assert result.data is not None
    
    def test_result_failure(self):
        """测试失败结果"""
        result = TaskResult(
            task_id="task_123",
            status=TaskStatus.FAILURE,
            error_message="Something went wrong",
        )
        
        assert result.is_success() is False
        assert result.error_message is not None


class TestTaskQueue:
    """测试任务队列"""
    
    @pytest.mark.asyncio
    async def test_submit_task(self):
        """测试提交任务"""
        from bridge.tasks.queue import TaskQueue, QueueConfig
        
        config = QueueConfig(max_concurrent=2, poll_interval=0.1)
        queue = TaskQueue(config)
        await queue.start()
        
        try:
            task = Task(task_type="test", parameters={"key": "value"})
            task_id = await queue.submit(task)
            
            assert task_id is not None
            assert task_id == task.id
            assert task.status == TaskStatus.QUEUED
        finally:
            await queue.stop()
    
    @pytest.mark.asyncio
    async def test_get_status(self):
        """测试获取状态"""
        from bridge.tasks.queue import TaskQueue, QueueConfig
        
        config = QueueConfig(max_concurrent=2, poll_interval=0.1)
        queue = TaskQueue(config)
        await queue.start()
        
        try:
            task = Task(task_type="test")
            task_id = await queue.submit(task)
            
            status = await queue.get_status(task_id)
            
            assert status is not None
            assert status == TaskStatus.QUEUED
        finally:
            await queue.stop()
    
    @pytest.mark.asyncio
    async def test_cancel_task(self):
        """测试取消任务"""
        from bridge.tasks.queue import TaskQueue, QueueConfig
        
        config = QueueConfig(max_concurrent=2, poll_interval=0.1)
        queue = TaskQueue(config)
        await queue.start()
        
        try:
            task = Task(task_type="test")
            task_id = await queue.submit(task)
            
            success = await queue.cancel(task_id)
            
            assert success is True
            
            task = await queue.get_task(task_id)
            assert task.status == TaskStatus.CANCELLED
        finally:
            await queue.stop()
    
    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """测试优先级排序"""
        from bridge.tasks.queue import TaskQueue, QueueConfig
        
        config = QueueConfig(max_concurrent=2, poll_interval=0.1)
        queue = TaskQueue(config)
        await queue.start()
        
        try:
            # 提交不同优先级的任务
            low_task = Task(task_type="test", priority=TaskPriority.LOW)
            high_task = Task(task_type="test", priority=TaskPriority.HIGH)
            normal_task = Task(task_type="test", priority=TaskPriority.NORMAL)
            
            await queue.submit(low_task)
            await queue.submit(high_task)
            await queue.submit(normal_task)
            
            # 检查队列顺序（高优先级在前）
            assert queue._queue[0][2] == high_task.id
        finally:
            await queue.stop()


class TestMaterializedView:
    """测试物化视图"""
    
    @pytest.mark.asyncio
    async def test_view_creation(self):
        """测试视图创建"""
        from bridge.cache.materialized_view import (
            MaterializedViewManager,
            ViewDefinition,
        )
        
        manager = MaterializedViewManager()
        
        async def compute():
            return {"data": "test"}
        
        view = ViewDefinition(
            name="test_view",
            compute_func=compute,
            refresh_interval=3600,
        )
        
        await manager.register_view(view)
        
        assert "test_view" in manager._views
    
    @pytest.mark.asyncio
    async def test_view_should_refresh(self):
        """测试刷新判断"""
        from bridge.cache.materialized_view import ViewDefinition
        
        async def compute():
            return {}
        
        # 新视图应该刷新
        view = ViewDefinition(
            name="test",
            compute_func=compute,
            refresh_interval=3600,
        )
        assert view.should_refresh() is True
        
        # 刚刷新的视图不应该刷新
        view.last_refresh = datetime.utcnow()
        assert view.should_refresh() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
