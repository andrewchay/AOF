"""W06.03 — Queued tasks re-authorize at execution time.

Acceptance: a task queued while its subject was authorized must be
REJECTED by the worker when the subject/session has been revoked in the
meantime — the executor never runs. Revocations persist across
processes; replay-style re-runs get the current decision, not the old one.
"""

from __future__ import annotations

import asyncio

import pytest

from bridge.access.revocations import RevocationRegistry
from bridge.tasks.models import Task, TaskAuthorizationError, TaskStatus
from bridge.tasks.queue import TaskQueue


def _sync_spy_executor(executed):
    async def executor(task):
        executed.append(task.id)
        return {"ok": True}

    return executor


# ---------------------------------------------------------------------------
# Queue-level authorizer
# ---------------------------------------------------------------------------


def test_authorizer_denial_prevents_executor(monkeypatch):
    """撤权后 worker 拒绝：executor 未被调用，任务失败并说明原因。"""
    queue = TaskQueue()
    executed = []
    queue._get_executor = lambda task_type: _sync_spy_executor(executed)

    async def deny_after_queueing(task):
        if task.user_id == "revoked-user":
            raise TaskAuthorizationError(f"subject revoked since submission: {task.user_id}")

    queue.set_authorizer(deny_after_queueing)

    task = Task(task_type="pagerank", user_id="revoked-user", tenant_id="t1")

    async def run_one():
        await queue._execute_task(task)

    asyncio.run(run_one())

    assert not executed, "executor must never run for a denied task"
    assert task.status == TaskStatus.FAILURE
    result = queue._results[task.id]
    assert "revoked" in (result.error_message or "")


def test_authorizer_allows_authorized_task():
    queue = TaskQueue()
    executed = []
    queue._get_executor = lambda task_type: _sync_spy_executor(executed)

    async def allow(task):
        return None

    queue.set_authorizer(allow)

    task = Task(task_type="pagerank", user_id="active-user")

    async def run_one():
        await queue._execute_task(task)

    asyncio.run(run_one())

    assert executed == [task.id]
    assert task.status == TaskStatus.SUCCESS


def test_no_authorizer_keeps_legacy_behavior():
    queue = TaskQueue()
    executed = []
    queue._get_executor = lambda task_type: _sync_spy_executor(executed)

    task = Task(task_type="pagerank")

    async def run_one():
        await queue._execute_task(task)

    asyncio.run(run_one())

    assert executed == [task.id]
    assert task.status == TaskStatus.SUCCESS


# ---------------------------------------------------------------------------
# RevocationRegistry — persistence & kinds
# ---------------------------------------------------------------------------


def test_revocation_persists_across_instances(tmp_path):
    path = tmp_path / "revocations.sqlite"
    registry1 = RevocationRegistry(path)
    registry1.revoke("subject", "alice", reason="offboarding")

    registry2 = RevocationRegistry(path)  # new instance = new process simulation
    assert registry2.is_revoked("subject", "alice") is True
    assert registry2.is_revoked("subject", "bob") is False


def test_revocation_unrevoke(tmp_path):
    path = tmp_path / "revocations.sqlite"
    registry = RevocationRegistry(path)
    registry.revoke("session", "s-1")
    assert registry.is_revoked("session", "s-1")
    assert registry.unrevoke("session", "s-1") is True
    assert registry.is_revoked("session", "s-1") is False
    assert registry.unrevoke("session", "s-1") is False  # already gone


def test_revocation_rejects_unknown_kind(tmp_path):
    registry = RevocationRegistry(tmp_path / "revocations.sqlite")
    from bridge.access.revocations import RevocationError

    with pytest.raises(RevocationError):
        registry.revoke("galaxy", "m31")
    with pytest.raises(RevocationError):
        registry.is_revoked("galaxy", "m31")


# ---------------------------------------------------------------------------
# Parse queue end-to-end: revoke between submit and run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_task_rejected_after_subject_revocation(tmp_path, monkeypatch):
    """提交后、执行前撤销 subject → worker 拒绝，解析从未发生。"""
    from bridge.document_parser.parse_tasks import DocumentParseQueue

    parse_calls = []
    monkeypatch.setattr(
        "bridge.document_parser.parse_tasks.parse_document",
        lambda path, config=None: parse_calls.append(path)
        or pytest.fail("parse_document must not run for a denied task"),
    )

    revocations = RevocationRegistry(tmp_path / "revocations.sqlite")
    queue = DocumentParseQueue()
    queue.set_authorizer(
        _make_registry_authorizer(revocations)
    )

    doc = tmp_path / "doc.pdf"
    doc.write_bytes(b"%PDF-1.4 fake")
    task_id = await queue.submit_parse(doc, principal=("queued-user", "tenant-a"))

    # 排队之后、执行之前：撤销该 subject
    revocations.revoke("subject", "queued-user", reason="offboarded while queued")

    await queue._execute_task(queue._tasks[task_id])

    result = await queue.get_result(task_id)
    assert result is not None and result.status == TaskStatus.FAILURE
    assert "revoked" in (result.error_message or "")
    assert parse_calls == [], "parser must never be invoked for a denied task"


@pytest.mark.asyncio
async def test_parse_task_runs_when_not_revoked(tmp_path, monkeypatch):
    from bridge.document_parser.parse_tasks import DocumentParseQueue

    class _FakeResult:
        doc = type("D", (), {"to_dict": lambda self: {"content": "x"}})()
        use_raw_path = False
        cached = False

    monkeypatch.setattr(
        "bridge.document_parser.parse_tasks.parse_document",
        lambda path, config=None: _FakeResult(),
    )

    revocations = RevocationRegistry(tmp_path / "revocations.sqlite")
    queue = DocumentParseQueue()
    queue.set_authorizer(_make_registry_authorizer(revocations))

    doc = tmp_path / "doc.txt"
    doc.write_text("hello")
    task_id = await queue.submit_parse(doc, principal=("good-user", "tenant-a"))

    await queue._execute_task(queue._tasks[task_id])

    result = await queue.get_result(task_id)
    assert result is not None and result.status == TaskStatus.SUCCESS


def _make_registry_authorizer(registry):
    from bridge.tasks.models import TaskAuthorizationError

    async def authorize(task):
        if task.user_id and registry.is_revoked("subject", task.user_id):
            raise TaskAuthorizationError(f"subject revoked since submission: {task.user_id}")
        if task.tenant_id and registry.is_revoked("tenant", task.tenant_id):
            raise TaskAuthorizationError(f"tenant suspended since submission: {task.tenant_id}")

    return authorize
