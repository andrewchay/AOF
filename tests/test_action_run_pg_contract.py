"""W08.04 — Action-run repository: same contract on SQLite AND PostgreSQL.

Parametrized against both backends against a REAL PostgreSQL instance.
"""

from __future__ import annotations

import socket

import pytest


def _pg_up() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 5433), timeout=1)
        s.close()
        return True
    except OSError:
        return False


@pytest.fixture(params=["sqlite", "postgres"])
def service(request, tmp_path):
    from bridge.decision_provenance import DecisionProvenanceStore
    from bridge.semantic_core import ActionConnectorRegistry, ActionRunService
    from tests.test_action_runs import SuccessfulCrmConnector, _plan

    connectors = ActionConnectorRegistry()
    connectors.register("crm-core", SuccessfulCrmConnector())

    if request.param == "sqlite":
        from bridge.semantic_core.action_runs import SqliteActionRunRepository
        repo = SqliteActionRunRepository(tmp_path / "ar.sqlite")
    elif _pg_up():
        from bridge.persistence.postgres_action_run_repository import (
            PostgresActionRunRepository,
        )
        repo = PostgresActionRunRepository()
        with repo._connect() as connection:
            connection.execute("TRUNCATE action_runs")
            connection.commit()
    else:
        pytest.skip("PostgreSQL infra not running")

    service = ActionRunService(
        repo, connectors=connectors,
        decision_store=DecisionProvenanceStore(tmp_path / "dp.jsonl"),
    )
    return service, _plan(tmp_path)


def test_put_new_and_get(service):
    svc, plan = service
    run = svc.submit(plan, actor="operator:bob", rationale="r")
    assert run.status in ("awaiting_approval", "approved")


def test_idempotent_submit(service):
    svc, plan = service
    first = svc.submit(plan, actor="operator:bob", rationale="r")
    second = svc.submit(plan, actor="operator:bob", rationale="r")
    assert first.run_id == second.run_id


def test_same_plan_resubmitted_is_idempotent(service):
    """同幂等键同 plan → 返回已有 run（幂等，非冲突）。"""
    svc, plan = service
    first = svc.submit(plan, actor="operator:bob", rationale="r")
    second = svc.submit(plan, actor="operator:bob", rationale="r")
    assert first.run_id == second.run_id


def test_full_approval_cycle_both_backends(service):
    svc, plan = service
    run = svc.submit(plan, actor="operator:bob", rationale="r")
    approved = svc.approve(run.run_id, actor="reviewer:alice", roles=["risk-reviewer"], rationale="ok")
    assert approved.status == "approved"
    succeeded = svc.execute(approved.run_id, actor="operator:worker")
    assert succeeded.status == "succeeded"



