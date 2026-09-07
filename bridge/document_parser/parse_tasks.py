"""异步解析队列：把重解析（GPU/慢引擎）从 API 请求阻塞中解耦（设计稿 §5.5）。

复用现有 ``bridge/tasks.TaskQueue``（优先级队列 + 状态追踪 + 结果存储 + 回调），
通过子类化注入 ``document_parse`` 执行器，不改动 bridge/tasks 核心。

用法::

    queue = DocumentParseQueue()
    await queue.start()
    task_id = await queue.submit_parse("/abs/file.pdf")
    status = await queue.get_status(task_id)          # pending/queued/running/...
    result = await queue.get_parse_result(task_id)    # ParsedDoc dict（完成后）
    await queue.stop()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from bridge.document_parser.config import ParserConfig
from bridge.document_parser.core import parse_document
from bridge.tasks.models import Task, TaskStatus
from bridge.tasks.queue import QueueConfig, TaskQueue

logger = logging.getLogger(__name__)

TASK_TYPE_PARSE = "document_parse"


class DocumentParseQueue(TaskQueue):
    """可提交异步解析任务的队列（复用 TaskQueue 机制）。"""

    def __init__(
        self,
        config: Optional[QueueConfig] = None,
        parser_config: Optional[ParserConfig] = None,
    ):
        super().__init__(config or QueueConfig())
        self._parser_config = parser_config  # 惰性求值，避免 import 循环
        self._executor = self._document_parse_executor

    def _get_executor(self, task_type: str) -> Optional[Any]:
        if task_type == TASK_TYPE_PARSE:
            return self._executor
        return super()._get_executor(task_type)

    async def submit_parse(
        self,
        path: str | Path,
        *,
        priority: int = 5,
        timeout_seconds: Optional[int] = None,
        name: Optional[str] = None,
        principal: Optional[tuple[str, str]] = None,
    ) -> str:
        """提交一个异步解析任务，返回 task_id。

        首次调用自动 start（幂等），便于单例直接 submit。
        ``principal`` 为 (subject_id, tenant_id)：W06.03 要求排队任务记录
        提交者身份，worker 执行前经队列 authorizer 重授权。
        """
        from bridge.tasks.models import Task

        await self.start()

        task = Task(
            task_type=TASK_TYPE_PARSE,
            name=name or f"parse:{Path(path).name}",
            parameters={"path": str(path)},
            timeout_seconds=timeout_seconds,
        )
        if principal is not None:
            subject, tenant = principal
            task.user_id = subject
            task.tenant_id = tenant
        task.priority = _priority(priority)
        return await self.submit(task)

    async def get_parse_result(self, task_id: str) -> Optional[dict]:
        """任务完成后返回 ParsedDoc dict；未完成/不在返回 None。"""
        result = await self.get_result(task_id)
        if result is None or result.status != TaskStatus.SUCCESS:
            return None
        return result.data

    async def _document_parse_executor(self, task: Task) -> dict:
        path = task.parameters.get("path")
        if not path:
            raise ValueError("task.parameters.path 缺失")
        cfg = self._parser_config or ParserConfig.from_env()
        await self.update_progress(task.id, 10, "解析中")
        res = parse_document(path, config=cfg)
        await self.update_progress(task.id, 90, "归一化完成")
        data = {
            "parsed_doc": res.doc.to_dict(),
            "use_raw_path": res.use_raw_path,
            "cached": res.cached,
        }
        await self.update_progress(task.id, 100, "完成")
        return data


def _priority(value: int) -> Any:
    from bridge.tasks.models import TaskPriority

    # 允许传入 TaskPriority 或 int（1..20）
    if isinstance(value, TaskPriority):
        return value
    return TaskPriority(value)
