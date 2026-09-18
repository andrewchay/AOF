# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with the
# Business Source License, use of this software will be governed by
# the Apache License, Version 2.0.

"""取消真实性回归测试（K01 探针发现的竞态）。

缺陷：协作型执行器在 cancel() 后读到 CANCELLED 正常返回时，
_execute_task 成功分支把状态覆盖回 SUCCESS；且 _running_tasks 从未
被填充，cancel() 对 RUNNING 任务的硬取消是死代码。

修复后：
- 任何分支都不得把 CANCELLED 覆盖回 SUCCESS/TIMEOUT/FAILURE
- cancel() 对运行中任务真正生效（asyncio.Task.cancel）
- worker 循环在 job 被取消后继续存活
"""

import asyncio

import pytest

from bridge.tasks.models import Task, TaskStatus
from bridge.tasks.queue import QueueConfig, TaskQueue


async def _wait_for(predicate, timeout: float = 5.0):
    """轮询等待条件成立（测试辅助）。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


@pytest.mark.asyncio
async def test_cooperative_executor_cancel_not_overwritten():
    """协作型执行器吞掉取消信号正常返回：最终状态必须是 CANCELLED。"""
    config = QueueConfig(max_concurrent=1, poll_interval=0.02)
    queue = TaskQueue(config)
    await queue.start()

    running = asyncio.Event()

    async def cooperative_executor(task: Task):
        running.set()
        # 协作式轮询：看到 CANCELLED 后"正常返回"（不抛 CancelledError）
        while task.status != TaskStatus.CANCELLED:
            await asyncio.sleep(0.02)
        return {"cooperative": "return"}

    queue._get_executor = lambda task_type: cooperative_executor  # noqa: SLF001

    try:
        task = Task(task_type="cooperative")
        task_id = await queue.submit(task)
        await asyncio.wait_for(running.wait(), timeout=5)

        assert await queue.cancel(task_id) is True
        # 等 worker 处理完（job 结束）
        await _wait_for(
            lambda: queue._results.get(task_id) is not None,  # noqa: SLF001
            timeout=5,
        )

        final = await queue.get_task(task_id)
        assert final.status == TaskStatus.CANCELLED, (
            f"协作型执行器正常返回后状态被覆盖: {final.status}"
        )
        result = queue._results.get(task_id)  # noqa: SLF001
        assert result is not None and result.status == TaskStatus.CANCELLED
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_queued_cancel_records_result():
    """排队中取消：结果补记为 CANCELLED（与执行中取消路径一致）。"""
    config = QueueConfig(max_concurrent=1, poll_interval=0.02)
    queue = TaskQueue(config)
    # 不 start：任务停在队列里
    try:
        task = Task(task_type="queued")
        task_id = await queue.submit(task)
        assert await queue.cancel(task_id) is True

        result = queue._results.get(task_id)  # noqa: SLF001
        assert result is not None, "排队中取消未补记结果"
        assert result.status == TaskStatus.CANCELLED
    finally:
        await queue.stop()
