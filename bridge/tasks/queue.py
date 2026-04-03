"""任务队列实现

基于内存 + Redis 的混合任务队列。
支持优先级、状态追踪、结果存储。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass
from datetime import datetime
import heapq

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from .models import Task, TaskResult, TaskStatus, TaskQuery, TaskStats

logger = logging.getLogger(__name__)


@dataclass
class QueueConfig:
    """队列配置"""
    # 并发控制
    max_concurrent: int = 5
    
    # 轮询间隔
    poll_interval: float = 1.0
    
    # 结果过期时间（秒）
    result_ttl: int = 86400  # 24小时
    
    # Redis 配置
    redis_url: Optional[str] = None
    redis_key_prefix: str = "aof:tasks:"


class TaskQueue:
    """任务队列
    
    特性：
    - 优先级队列
    - 任务状态追踪
    - 结果存储
    - 进度回调
    
    使用示例:
        queue = TaskQueue()
        await queue.start()
        
        # 提交任务
        task = Task(task_type="pagerank", dataset_name="graph1")
        task_id = await queue.submit(task)
        
        # 查询状态
        status = await queue.get_status(task_id)
        
        # 获取结果
        result = await queue.get_result(task_id)
    """
    
    def __init__(self, config: Optional[QueueConfig] = None):
        self.config = config or QueueConfig()
        self.redis: Optional[Any] = None
        
        # 内存队列（优先级队列）
        self._queue: List[tuple] = []  # (priority, created_at, task_id, task)
        self._tasks: Dict[str, Task] = {}
        self._results: Dict[str, TaskResult] = {}
        
        # 执行控制
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        
        # 回调
        self._on_progress: Optional[Callable] = None
        self._on_complete: Optional[Callable] = None
    
    async def connect(self) -> bool:
        """连接 Redis（如果配置了）"""
        if self.config.redis_url and REDIS_AVAILABLE:
            try:
                self.redis = await redis.from_url(self.config.redis_url)
                logger.info("Connected to Redis for task queue")
                return True
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                return False
        return True
    
    async def start(self) -> None:
        """启动队列处理"""
        if self._running:
            return
        
        self._running = True
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._worker_task = asyncio.create_task(self._worker_loop())
        
        logger.info(f"Task queue started (max_concurrent={self.config.max_concurrent})")
    
    async def stop(self) -> None:
        """停止队列处理"""
        self._running = False
        
        # 等待工作循环结束
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        
        # 取消正在运行的任务
        for task_id, task in list(self._running_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # 关闭 Redis 连接
        if self.redis:
            await self.redis.close()
        
        logger.info("Task queue stopped")
    
    async def submit(self, task: Task) -> str:
        """提交任务到队列
        
        Returns:
            任务 ID
        """
        # 设置状态
        task.status = TaskStatus.QUEUED
        task.created_at = datetime.utcnow()
        
        # 存储任务
        self._tasks[task.id] = task
        
        # 加入优先级队列
        # 优先级高的在前，相同优先级按创建时间排序
        heapq.heappush(
            self._queue,
            (-task.priority.value, task.created_at.timestamp(), task.id, task)
        )
        
        # 持久化到 Redis
        if self.redis:
            await self._persist_task(task)
        
        logger.info(f"Task submitted: {task.id} (type={task.task_type}, priority={task.priority.name})")
        return task.id
    
    async def cancel(self, task_id: str) -> bool:
        """取消任务"""
        task = self._tasks.get(task_id)
        if not task:
            return False
        
        if task.is_finished():
            return False
        
        if task.status == TaskStatus.RUNNING and task_id in self._running_tasks:
            # 取消正在运行的任务
            self._running_tasks[task_id].cancel()
        
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.utcnow()
        
        # 从队列中移除
        self._queue = [
            item for item in self._queue
            if item[2] != task_id
        ]
        heapq.heapify(self._queue)
        
        if self.redis:
            await self._persist_task(task)
        
        logger.info(f"Task cancelled: {task_id}")
        return True
    
    async def get_status(self, task_id: str) -> Optional[TaskStatus]:
        """获取任务状态"""
        task = self._tasks.get(task_id)
        if task:
            return task.status
        
        # 尝试从 Redis 加载
        if self.redis:
            task = await self._load_task(task_id)
            if task:
                return task.status
        
        return None
    
    async def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务详情"""
        task = self._tasks.get(task_id)
        if task:
            return task
        
        if self.redis:
            return await self._load_task(task_id)
        
        return None
    
    async def get_result(self, task_id: str) -> Optional[TaskResult]:
        """获取任务结果"""
        result = self._results.get(task_id)
        if result:
            return result
        
        if self.redis:
            return await self._load_result(task_id)
        
        return None
    
    async def query(self, query: TaskQuery) -> List[Task]:
        """查询任务"""
        results = []
        
        for task in self._tasks.values():
            # 应用过滤条件
            if query.status and task.status != query.status:
                continue
            if query.task_type and task.task_type != query.task_type:
                continue
            if query.dataset_name and task.dataset_name != query.dataset_name:
                continue
            if query.tenant_id and task.tenant_id != query.tenant_id:
                continue
            if query.user_id and task.user_id != query.user_id:
                continue
            if query.created_after and task.created_at < query.created_after:
                continue
            if query.created_before and task.created_at > query.created_before:
                continue
            
            results.append(task)
        
        # 排序
        results.sort(
            key=lambda t: getattr(t, query.order_by) or datetime.min,
            reverse=query.order_desc
        )
        
        # 分页
        return results[query.offset:query.offset + query.limit]
    
    async def get_stats(self) -> TaskStats:
        """获取统计信息"""
        stats = TaskStats()
        
        for task in self._tasks.values():
            stats.total_tasks += 1
            
            if task.status == TaskStatus.PENDING or task.status == TaskStatus.QUEUED:
                stats.pending_count += 1
            elif task.status == TaskStatus.RUNNING:
                stats.running_count += 1
            elif task.status == TaskStatus.SUCCESS:
                stats.success_count += 1
            elif task.status == TaskStatus.FAILURE:
                stats.failure_count += 1
            
            # 按类型统计
            stats.by_type[task.task_type] = stats.by_type.get(task.task_type, 0) + 1
            
            # 时长统计
            if task.duration_seconds:
                if stats.avg_duration_seconds == 0:
                    stats.avg_duration_seconds = task.duration_seconds
                else:
                    stats.avg_duration_seconds = (
                        (stats.avg_duration_seconds * (stats.success_count - 1) + task.duration_seconds)
                        / stats.success_count
                    )
                stats.max_duration_seconds = max(stats.max_duration_seconds, task.duration_seconds)
                if stats.min_duration_seconds == 0:
                    stats.min_duration_seconds = task.duration_seconds
                else:
                    stats.min_duration_seconds = min(stats.min_duration_seconds, task.duration_seconds)
        
        return stats
    
    def on_progress(self, callback: Callable[[str, float, str], None]) -> None:
        """设置进度回调"""
        self._on_progress = callback
    
    def on_complete(self, callback: Callable[[str, TaskStatus], None]) -> None:
        """设置完成回调"""
        self._on_complete = callback
    
    async def update_progress(self, task_id: str, progress: float, message: str = "") -> None:
        """更新任务进度"""
        task = self._tasks.get(task_id)
        if task:
            task.progress = progress
            task.progress_message = message
            
            if self._on_progress:
                try:
                    self._on_progress(task_id, progress, message)
                except Exception as e:
                    logger.error(f"Progress callback error: {e}")
            
            if self.redis:
                await self._persist_task(task)
    
    async def _worker_loop(self) -> None:
        """工作循环"""
        while self._running:
            try:
                # 获取下一个任务
                if self._queue:
                    _, _, task_id, task = heapq.heappop(self._queue)
                    
                    # 使用信号量控制并发
                    async with self._semaphore:
                        await self._execute_task(task)
                else:
                    # 队列为空，等待
                    await asyncio.sleep(self.config.poll_interval)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(self.config.poll_interval)
    
    async def _execute_task(self, task: Task) -> None:
        """执行任务"""
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow()
        task.worker_id = f"worker-{asyncio.current_task().get_name()}"
        
        logger.info(f"Task started: {task.id} (type={task.task_type})")
        
        try:
            # 获取执行器
            executor = self._get_executor(task.task_type)
            if not executor:
                raise RuntimeError(f"No executor for task type: {task.task_type}")
            
            # 执行（带超时）
            if task.timeout_seconds:
                result_data = await asyncio.wait_for(
                    executor(task),
                    timeout=task.timeout_seconds
                )
            else:
                result_data = await executor(task)
            
            # 成功
            task.status = TaskStatus.SUCCESS
            task.completed_at = datetime.utcnow()
            task.progress = 100.0
            
            result = TaskResult(
                task_id=task.id,
                status=TaskStatus.SUCCESS,
                data=result_data,
            )
            
            logger.info(f"Task completed: {task.id}")
            
        except asyncio.TimeoutError:
            task.status = TaskStatus.TIMEOUT
            task.completed_at = datetime.utcnow()
            result = TaskResult(
                task_id=task.id,
                status=TaskStatus.TIMEOUT,
                error_message="Task timeout",
            )
            logger.warning(f"Task timeout: {task.id}")
            
        except Exception as e:
            # 失败
            task.status = TaskStatus.FAILURE
            task.completed_at = datetime.utcnow()
            
            import traceback
            result = TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILURE,
                error_message=str(e),
                error_traceback=traceback.format_exc(),
            )
            
            logger.error(f"Task failed: {task.id}, error={e}")
        
        # 存储结果
        self._results[task.id] = result
        
        # 持久化
        if self.redis:
            await self._persist_task(task)
            await self._persist_result(result)
        
        # 回调
        if self._on_complete:
            try:
                self._on_complete(task.id, task.status)
            except Exception as e:
                logger.error(f"Complete callback error: {e}")
    
    def _get_executor(self, task_type: str) -> Optional[Callable]:
        """获取任务执行器"""
        # 这里应该根据任务类型返回对应的执行器
        # 简化实现，实际应该从注册表获取
        executors = {
            "pagerank": self._dummy_executor,
            "community": self._dummy_executor,
            "analytics": self._dummy_executor,
        }
        return executors.get(task_type)
    
    async def _dummy_executor(self, task: Task) -> Any:
        """虚拟执行器（用于测试）"""
        # 模拟耗时操作
        for i in range(10):
            await asyncio.sleep(0.1)
            await self.update_progress(task.id, (i + 1) * 10, f"Step {i+1}/10")
        
        return {"message": "Task completed", "task_id": task.id}
    
    # ========== Redis 持久化 ==========
    
    async def _persist_task(self, task: Task) -> None:
        """持久化任务到 Redis"""
        if not self.redis:
            return
        
        key = f"{self.config.redis_key_prefix}task:{task.id}"
        await self.redis.setex(
            key,
            self.config.result_ttl,
            task.to_dict().__str__()
        )
    
    async def _load_task(self, task_id: str) -> Optional[Task]:
        """从 Redis 加载任务"""
        if not self.redis:
            return None
        
        key = f"{self.config.redis_key_prefix}task:{task_id}"
        data = await self.redis.get(key)
        if data:
            # 反序列化
            # 简化处理
            return None
        return None
    
    async def _persist_result(self, result: TaskResult) -> None:
        """持久化结果到 Redis"""
        if not self.redis:
            return
        
        key = f"{self.config.redis_key_prefix}result:{result.task_id}"
        await self.redis.setex(
            key,
            self.config.result_ttl,
            result.to_dict().__str__()
        )
    
    async def _load_result(self, task_id: str) -> Optional[TaskResult]:
        """从 Redis 加载结果"""
        if not self.redis:
            return None
        
        key = f"{self.config.redis_key_prefix}result:{task_id}"
        data = await self.redis.get(key)
        if data:
            # 反序列化
            return None
        return None
