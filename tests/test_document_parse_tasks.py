# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""document_parser 异步解析队列测试。

验证 DocumentParseQueue 复用 TaskQueue 机制：提交 → 状态流转 → 完成 → 取结果。
用 md 文件直读路径（不依赖隔离子进程），保证快速、环境无关。
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from bridge.document_parser.config import ParserConfig
from bridge.document_parser.parse_tasks import TASK_TYPE_PARSE, DocumentParseQueue
from bridge.tasks.models import TaskStatus


class TestDocumentParseQueue(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = ParserConfig(enabled=True, cache_enabled=False)

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def _wait_status(
        self, q: DocumentParseQueue, task_id: str, timeout: float = 5.0
    ) -> TaskStatus:
        async def poll():
            while True:
                st = await q.get_status(task_id)
                if st in (
                    TaskStatus.SUCCESS,
                    TaskStatus.FAILURE,
                    TaskStatus.TIMEOUT,
                    TaskStatus.CANCELLED,
                ):
                    return st
                await asyncio.sleep(0.05)

        return await asyncio.wait_for(poll(), timeout=timeout)

    async def test_submit_parse_and_complete(self):
        q = DocumentParseQueue(parser_config=self.cfg)
        await q.start()
        try:
            f = Path(self._tmp.name) / "note.md"
            f.write_text("# 标题\n\n正文内容", encoding="utf-8")
            task_id = await q.submit_parse(str(f))
            status = await self._wait_status(q, task_id)
            self.assertEqual(status, TaskStatus.SUCCESS)

            data = await q.get_parse_result(task_id)
            self.assertIsNotNone(data)
            doc = data["parsed_doc"]
            self.assertEqual(doc["engine"], "direct")
            self.assertFalse(data["use_raw_path"])
            self.assertIn("# 标题", doc["content"])
        finally:
            await q.stop()

    async def test_task_type_and_priority(self):
        q = DocumentParseQueue(parser_config=self.cfg)
        await q.start()
        try:
            f = Path(self._tmp.name) / "a.md"
            f.write_text("x", encoding="utf-8")
            task_id = await q.submit_parse(str(f), priority=10, name="mypdf")
            task = await q.get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.task_type, TASK_TYPE_PARSE)
            self.assertEqual(task.priority.value, 10)
        finally:
            await q.stop()

    async def test_missing_file_task(self):
        q = DocumentParseQueue(parser_config=self.cfg)
        await q.start()
        try:
            task_id = await q.submit_parse(str(Path(self._tmp.name) / "nope.pdf"))
            status = await self._wait_status(q, task_id)
            # 缺失文件 → fallback（use_raw_path=True），任务 SUCCESS
            self.assertEqual(status, TaskStatus.SUCCESS)
            data = await q.get_parse_result(task_id)
            self.assertTrue(data["use_raw_path"])
            self.assertEqual(data["parsed_doc"]["engine"], "fallback")
        finally:
            await q.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
