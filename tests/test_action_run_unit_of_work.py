"""W02.04 — ActionRunService atomic state+audit commits via SqliteUnitOfWork.

Plan 6.4: decision audit and business state must commit in ONE transaction.
A crash between the two writes must leave NEITHER behind (rollback), never
a confirmed decision without its state transition.
"""

from __future__ import annotations

import sqlite3

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.persistence.unit_of_work import SqliteUnitOfWork, SqliteUnitOfWorkSession
from bridge.semantic_core import (
    ActionConnectorRegistry,
    ActionRunError,
    ActionRunService,
    SqliteActionRunRepository,
)
from tests.test_action_runs import SuccessfulCrmConnector, _plan


def _service(tmp_path, *, with_uow: bool = True):
    registry = ActionConnectorRegistry()
    registry.register("crm-core", SuccessfulCrmConnector())
    db = tmp_path / "control.sqlite3"
    repository = SqliteActionRunRepository(db)
    uow = SqliteUnitOfWork(db) if with_uow else None
    service = ActionRunService(
        repository,
        connectors=registry,
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
        unit_of_work=uow,
    )
    return service, db


def _decision_count(db, tenant: str) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM decision_entries WHERE tenant_id = ?", (tenant,)
        ).fetchone()[0]


def test_submit_commits_decision_and_state_atomically(tmp_path):
    service, db = _service(tmp_path)
    plan = _plan(tmp_path)

    run = service.submit(plan, actor="operator:bob", rationale="fraud confirmed")

    assert run.status in {"awaiting_approval", "approved"}
    # decision audit and action state landed together in the SAME database
    assert _decision_count(db, "acme") == 1
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM action_runs").fetchone()[0]
    assert rows == 1


def test_submit_rolls_back_decision_when_state_insert_fails(tmp_path, monkeypatch):
    """Crash between decision append and state insert: NEITHER may persist."""
    service, db = _service(tmp_path)
    plan = _plan(tmp_path)

    def boom(self, run):
        raise RuntimeError("simulated crash during state insert")

    monkeypatch.setattr(SqliteUnitOfWorkSession, "put_action_run", boom)

    with pytest.raises(RuntimeError, match="simulated crash"):
        service.submit(plan, actor="operator:bob", rationale="fraud confirmed")

    # rollback: no decision entry, no action run
    assert _decision_count(db, "acme") == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM action_runs").fetchone()[0] == 0


def test_approve_commits_decision_and_state_atomically(tmp_path):
    service, db = _service(tmp_path)
    plan = _plan(tmp_path)
    run = service.submit(plan, actor="operator:bob", rationale="fraud confirmed")
    assert run.status == "awaiting_approval"
    before = _decision_count(db, "acme")

    approved = service.approve(
        run.run_id, actor="reviewer:alice", roles=["risk-reviewer"], rationale="approved"
    )

    assert approved.status == "approved"
    assert _decision_count(db, "acme") == before + 1


def test_approve_rolls_back_decision_when_state_update_fails(tmp_path, monkeypatch):
    service, db = _service(tmp_path)
    plan = _plan(tmp_path)
    run = service.submit(plan, actor="operator:bob", rationale="fraud confirmed")
    before = _decision_count(db, "acme")

    def boom(self, run, *, expected_digest):
        raise RuntimeError("simulated crash during approval state update")

    monkeypatch.setattr(SqliteUnitOfWorkSession, "update_action_run", boom)

    with pytest.raises(RuntimeError, match="simulated crash"):
        service.approve(
            run.run_id, actor="reviewer:alice", roles=["risk-reviewer"], rationale="approved"
        )

    # rollback: approval decision NOT persisted, run still awaiting_approval
    assert _decision_count(db, "acme") == before
    reloaded = service.repository.get(run.run_id)
    assert reloaded.status == "awaiting_approval"


def test_uow_and_repository_share_one_database_file(tmp_path):
    """The UoW and the action repository operate on the SAME SQLite file:
    reads through the repository see UoW-committed state (single source)."""
    service, db = _service(tmp_path)
    plan = _plan(tmp_path)
    run = service.submit(plan, actor="operator:bob", rationale="r")

    fresh_repo = SqliteActionRunRepository(db)
    assert fresh_repo.get(run.run_id) is not None
