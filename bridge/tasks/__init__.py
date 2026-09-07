# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""异步任务队列模块

提供后台任务处理能力：
- 任务队列管理
- 分布式任务执行
- 任务状态追踪
- 结果存储

使用示例:
    from bridge.tasks import TaskQueue, AnalysisTask
    
    queue = TaskQueue()
    task = await queue.submit(
        AnalysisTask(
            task_type="pagerank",
            dataset_name="my_graph"
        )
    )
    
    # 查询状态
    status = await queue.get_status(task.id)
"""

from .queue import TaskQueue, TaskStatus
from .models import Task, TaskResult, TaskPriority
from .executors import GraphAnalyticsExecutor

__all__ = [
    "TaskQueue",
    "TaskStatus",
    "Task",
    "TaskResult",
    "TaskPriority",
    "GraphAnalyticsExecutor",
]
