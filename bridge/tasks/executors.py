"""任务执行器

定义各种任务类型的执行逻辑。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from .models import Task

logger = logging.getLogger(__name__)


class GraphAnalyticsExecutor:
    """图分析任务执行器"""
    
    def __init__(self, backend):
        self.backend = backend
    
    async def execute(self, task: Task) -> Any:
        """执行任务"""
        if task.task_type == "pagerank":
            return await self._execute_pagerank(task)
        elif task.task_type == "community":
            return await self._execute_community(task)
        elif task.task_type == "analytics":
            return await self._execute_analytics(task)
        else:
            raise ValueError(f"Unknown task type: {task.task_type}")
    
    async def _execute_pagerank(self, task: Task) -> Dict[str, Any]:
        """执行 PageRank"""
        params = task.parameters
        top_k = params.get("top_k", 100)
        
        result = await self.backend.pagerank(top_k=top_k)
        
        return {
            "algorithm": "pagerank",
            "top_k": top_k,
            "results": [r.__dict__ for r in result],
        }
    
    async def _execute_community(self, task: Task) -> Dict[str, Any]:
        """执行社区检测"""
        params = task.parameters
        algorithm = params.get("algorithm", "louvain")
        
        result = await self.backend.community_detection(algorithm=algorithm)
        
        return {
            "algorithm": algorithm,
            "communities": [c.__dict__ for c in result],
        }
    
    async def _execute_analytics(self, task: Task) -> Dict[str, Any]:
        """执行综合统计"""
        stats = await self.backend.get_statistics()
        
        return {
            "statistics": stats.__dict__,
        }


# 执行器注册表
EXECUTORS: Dict[str, Callable] = {}


def register_executor(task_type: str, executor: Callable):
    """注册执行器"""
    EXECUTORS[task_type] = executor
    logger.info(f"Registered executor for task type: {task_type}")


def get_executor(task_type: str) -> Callable:
    """获取执行器"""
    return EXECUTORS.get(task_type)
