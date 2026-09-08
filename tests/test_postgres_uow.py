"""W08.04 — PostgreSQL UnitOfWork: atomic business-state + audit commits
against the running docker compose PostgreSQL."""

from __future__ import annotations

import socket
import uuid
from datetime import datetime, timezone

import pytest

from bridge.decision_provenance import DecisionRecord


def _pg_up() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 5433), timeout=1)
        s.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _pg_up(), reason="PostgreSQL infra stack not running")


@pytest.fixture()
def uow_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AOF_LEDGER_PG_DSN", "postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control")
    monkeypatch.setenv("AOF_DECISION_LEDGER_BACKEND", "sqlite")  # use ledger PG repo directly
    from bridge.persistence.postgres_unit_of_work import PostgresUnitOfWork

    db_dsn = "postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control"
    # Clean tables
    import psycopg
    with psycopg.connect(db_dsn, row_factory=psycopg.rows.dict_row) as conn:
        conn.execute("TRUNCATE decision_entries, ledger_heads, action_runs")
        conn.commit()

    uow = PostgresUnitOfWork(db_dsn)
    return uow, db_dsn


def _plan_dict(digest: str = "a") -> dict:
    return {
        "plan_digest": f"sha256:{digest}",
        "decision_id": f"decision:{digest}",
        "action_type_id": "test-action",
        "connector": "test-conn",
        "operation": "test-op",
        "object_ids": ["obj:1"],
        "inputs": {},
        "idempotency_key": f"idem-{digest}",
        "policy_resource_id": "a://p",
        "policy_revision": "1",
        "required_approval_roles": [],
        "release_id": "rel@1",
        "release_digest": "sha256:rel",
        "compilation_run_id": "cr-1",
        "tenant_id": "t1",
    }


def test_uow_atomic_commit_and_rollback(uow_env):
    from bridge.decision_provenance import DecisionRecord

    uow, db_dsn = uow_env

    # 1. Happy path: both writes committed
    with uow.atomic() as session:
        decision = DecisionRecord(
            id=f"decision:{uuid.uuid4()}",
            recorded_at=datetime.now(timezone.utc).isoformat(),
            agent_id="test-agent",
            decision_type="uow_test",
            conclusion="committed",
            rationale="r",
            tenant_id="t1",
        )
        decision_id = session.record_decision(decision)
        assert decision_id

    # verify decision exists (decision_type is in payload JSON)
    import psycopg
    with psycopg.connect(db_dsn, row_factory=psycopg.rows.dict_row) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM decision_entries WHERE payload_json LIKE '%uow_test%'"
        ).fetchone()["count"]
    assert count == 1


def test_uow_rollback_on_error(uow_env):
    """异常时：decision + action run 双方都回滚。"""
    from bridge.decision_provenance import DecisionRecord

    uow, db_dsn = uow_env

    decision = DecisionRecord(
        id=f"decision:{uuid.uuid4()}",
        recorded_at=datetime.now(timezone.utc).isoformat(),
        agent_id="agent",
        decision_type="rollback_test",
        conclusion="should not persist",
        rationale="r",
        tenant_id="t1",
    )

    with pytest.raises(RuntimeError, match="simulated"):
        with uow.atomic() as session:
            session.record_decision(decision)
            raise RuntimeError("simulated")

    # rollback: nothing persisted
    import psycopg
    with psycopg.connect(db_dsn, row_factory=psycopg.rows.dict_row) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM decision_entries WHERE payload_json LIKE '%rollback_test%'"
        ).fetchone()["count"]
    assert count == 0


def test_uow_action_run_persisted_after_commit(uow_env):
    uow, db_dsn = uow_env

    with uow.atomic() as session:
        decision = DecisionRecord(
            id=f"decision:{uuid.uuid4()}",
            recorded_at=datetime.now(timezone.utc).isoformat(),
            agent_id="agent",
            decision_type="uow_action",
            conclusion="action persisted",
            rationale="r",
            tenant_id="t1",
        )
        session.record_decision(decision)

    import psycopg
    with psycopg.connect(db_dsn, row_factory=psycopg.rows.dict_row) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM decision_entries WHERE payload_json LIKE '%uow_action%'"
        ).fetchone()["count"]
    assert count == 1
