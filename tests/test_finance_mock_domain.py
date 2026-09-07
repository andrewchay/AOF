"""W10 (mock scope) — Deterministic finance dataset, mock ERP full protocol,
and the domain holdout harness.

These verify the MACHINERY with mock data. Business validation (W10.05,
real authorized data + sign-off) remains open by design.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bridge.semantic_core.action_runs import (
    ActionRunError,
)
from bridge.semantic_core.finance_mock import (
    MockErpTicketConnector,
    generate_dataset,
    write_sqlite,
)
from bridge.semantic_core.domain_eval import build_holdout, run_holdout


# ---------------------------------------------------------------------------
# Deterministic dataset
# ---------------------------------------------------------------------------


def test_dataset_is_deterministic():
    d1, d2 = generate_dataset(), generate_dataset()
    assert d1.rows == d2.rows
    assert d1.golden == d2.golden


def test_dataset_golden_aggregates_consistent_with_rows():
    d = generate_dataset()
    current = [r for r in d.rows if r["period"] == "2026-09"]
    assert d.golden["row_count_current_period"] == len(current)
    assert d.golden["total_revenue"] == round(sum(r["revenue"] or 0 for r in current), 2)


def test_dataset_materializes_to_sqlite_with_source_refs(tmp_path):
    d = generate_dataset()
    path = write_sqlite(d, str(tmp_path / "dwd.sqlite"))
    with sqlite3.connect(path) as conn:
        count, refs = conn.execute(
            "SELECT COUNT(*), COUNT(source_ref) FROM finance"
        ).fetchone()
    assert count == d.row_count
    assert refs == d.row_count, "every row carries source lineage"


def test_dataset_contains_injected_anomalies():
    d = generate_dataset()
    assert len(d.anomalies) == 3
    # row0: negative outstanding; row1: missing revenue; row2: extreme outlier
    assert d.rows[0]["outstanding"] < 0
    assert d.rows[1]["revenue"] is None
    assert d.rows[2]["outstanding"] > 9000


# ---------------------------------------------------------------------------
# Mock ERP connector — full W10.04 protocol
# ---------------------------------------------------------------------------


def _request(key: str, op: str = "create_ticket") -> dict:
    return {
        "execution_id": "run-1",
        "idempotency_key": key,
        "operation": op,
        "object_ids": ["customer:A"],
        "inputs": {"reason": "revenue deviation"},
    }


def test_mock_erp_invoke_creates_ticket_with_receipt(tmp_path):
    connector = MockErpTicketConnector(str(tmp_path / "erp.json"))
    result = connector.invoke(_request("key-1"))

    assert result["outcome"] == "succeeded"
    assert result["effect_applied"] is True
    receipt = result["receipt"]
    assert receipt["status"] == "created"
    assert connector.ticket(receipt["ticket_id"]) is not None


def test_mock_erp_duplicate_delivery_is_idempotent(tmp_path):
    """同一幂等键重复投递（W02.05/W10.04）：返回原回执，不重复创建。"""
    connector = MockErpTicketConnector(str(tmp_path / "erp.json"))
    first = connector.invoke(_request("key-dup"))
    ticket_id = first["receipt"]["ticket_id"]

    second = connector.invoke(_request("key-dup"))
    assert second["receipt"]["ticket_id"] == ticket_id, "idempotent replay"
    # no second ticket created
    assert len({t["ticket_id"] for t in connector._tickets.values()}) == 1


def test_mock_erp_query_receipt_settles_interruption(tmp_path):
    connector = MockErpTicketConnector(str(tmp_path / "erp.json"))
    connector.invoke(_request("key-q"))

    receipt = connector.query_receipt(_request("key-q"))
    assert receipt is not None
    assert receipt["outcome"] == "succeeded"

    # unknown key: nothing was dispatched
    assert connector.query_receipt(_request("key-never")) is None


def test_mock_erp_compensation_closes_ticket(tmp_path):
    connector = MockErpTicketConnector(str(tmp_path / "erp.json"))
    result = connector.invoke(_request("key-c"))
    ticket_id = result["receipt"]["ticket_id"]

    compensation = connector.compensate({
        "execution_id": "run-1",
        "idempotency_key": "key-c",
        "operation": "close_ticket",
        "receipt": result["receipt"],
    })
    assert compensation["outcome"] == "succeeded"
    assert connector.ticket(ticket_id)["status"] == "closed_by_compensation"


def test_mock_erp_unknown_outcome_injection(tmp_path):
    """故障注入：unknown outcome → 服务端必须走 reconciliation 而非盲目重试。"""
    connector = MockErpTicketConnector(str(tmp_path / "erp.json"))
    connector.fail_next_n = 1

    result = connector.invoke(_request("key-unknown"))
    assert result["outcome"] == "unknown"
    assert result["effect_applied"] is None


# ---------------------------------------------------------------------------
# W10.03 holdout harness (governed-answer scoring)
# ---------------------------------------------------------------------------


def test_holdout_cases_derive_from_dataset():
    cases = build_holdout(generate_dataset())
    assert len(cases) == 6
    numeric = [c for c in cases if c.expected_answer_type == "numeric"]
    refusals = [c for c in cases if c.expected_answer_type == "refusal"]
    assert len(numeric) == 3 and len(refusals) == 3
    # numeric expectations come from golden aggregates
    assert all(c.expected_value is not None for c in numeric)


def test_holdout_scores_governed_answer_fn():
    """受治理 answer_fn（由数据集 golden 驱动）应全过。"""
    dataset = generate_dataset()
    golden = dataset.golden

    def governed_answer_fn(question: str) -> dict:
        # stand-in for the governed pipeline: answers only from the
        # golden aggregates, refuses out-of-period and anomaly questions
        if "总收入" in question and golden["period"] in question:
            return {"answer": golden["total_revenue"], "numeric": golden["total_revenue"], "refused": False}
        if "总预算" in question:
            return {"answer": golden["total_budget"], "numeric": golden["total_budget"], "refused": False}
        if "多少条客户" in question:
            return {"answer": golden["row_count_current_period"], "numeric": golden["row_count_current_period"], "refused": False}
        return {"answer": None, "numeric": None, "refused": True}

    report = run_holdout(build_holdout(dataset), answer_fn=governed_answer_fn)
    assert report.passed == report.total == 6
    assert report.to_dict()["numeric"] == "3/3"
    assert report.to_dict()["refusal"] == "3/3"


def test_holdout_catches_fabricating_answer_fn():
    """负例：对'应拒答'的问题给出自信数字 → 评估必须抓住。"""
    dataset = generate_dataset()

    def fabricating_fn(question: str) -> dict:
        # fabricates an answer for everything (the failure mode W10.03 exists to catch)
        return {"answer": 42.0, "numeric": 42.0, "refused": False}

    report = run_holdout(build_holdout(dataset), answer_fn=fabricating_fn)
    assert report.passed < report.total
    refusal_results = [r for r in report.results if r.expected_type == "refusal"]
    assert all(not r.passed for r in refusal_results), "fabrication must fail refusal cases"
