# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W10 — Deterministic finance mock dataset and mock ERP connector.

NO real financial data is available yet (W10.05 blocked on authorized
enterprise data + business sign-off). This module provides a
DETERMINISTIC, structurally realistic mock so that:

- W10.03 domain evaluation (holdout questions, refusal cases, citation
  completeness) can run locally against a governed release
- W10.04 connector protocol (invoke / compensate / query_receipt /
  duplicate delivery / unknown outcome) can be verified without an ERP
- the real-data swap later is a data-source change, not a code change

Everything here is explicitly synthetic. It must never be cited as
business validation: W10.05 stays open until real data + signature.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Deterministic dataset
# ---------------------------------------------------------------------------

REGIONS = ("华东", "华北", "华南", "华西")
PERIODS = ("2026-07", "2026-08", "2026-09")
CUSTOMERS = tuple(f"Customer{i:02d}" for i in range(1, 13))
CONTRACT_CLAUSES = ("3.1 付款期限为验收后30天", "3.2 逾期按日万分之三计息", "4.1 预付款不予退还")


def _stable_number(*parts: Any) -> float:
    """Deterministic pseudo-amount from stable inputs (no randomness:
    runs must be reproducible for audit comparison)."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return round(int.from_bytes(digest[:4], "big") / 2**32 * 100 + 20, 2)


@dataclass(frozen=True)
class MockFinanceDataset:
    """The generated dataset + its golden aggregates (same source = audit
    comparability), plus deliberately injected anomalies for refusal /
    reconciliation tests."""

    row_count: int
    rows: tuple[dict[str, Any], ...]
    golden: dict[str, Any]
    anomalies: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "golden": self.golden,
            "anomalies": list(self.anomalies),
        }


def generate_dataset(*, seed: str = "2026-09") -> MockFinanceDataset:
    """Deterministic multi-region/month/customer dataset with known aggregates
    and three injected anomalies."""
    rows: list[dict[str, Any]] = []
    for period in PERIODS:
        for region in REGIONS:
            for customer in CUSTOMERS:
                revenue = _stable_number(seed, period, region, customer, "revenue")
                budget = _stable_number(seed, period, region, customer, "budget")
                # deterministic receipts/outstanding with a relation:
                # outstanding = revenue - receipts (clamped at >= 0)
                receipts = round(min(revenue, budget * 0.8), 2)
                outstanding = round(max(0.0, revenue - receipts), 2)
                rows.append({
                    "customer": customer,
                    "region": region,
                    "period": period,
                    "revenue": revenue,
                    "budget": budget,
                    "receipts": receipts,
                    "outstanding": outstanding,
                    "contract_clause": CONTRACT_CLAUSES[hash(customer) % len(CONTRACT_CLAUSES)],
                    "source_ref": f"mock://finance/{period}/{region}/{customer}",
                })

    # Injected anomalies (deterministic positions)
    rows[0]["outstanding"] = -1.0  # impossible negative outstanding
    rows[1]["revenue"] = None       # missing revenue -> must be excluded/refused
    rows[2]["outstanding"] = 99999.0  # extreme outlier -> reconciliation

    current_period_rows = [r for r in rows if r["period"] == "2026-09"]
    golden = {
        "period": "2026-09",
        "total_revenue": round(sum(r["revenue"] or 0 for r in current_period_rows), 2),
        "total_budget": round(sum(r["budget"] for r in current_period_rows), 2),
        "total_outstanding": round(sum(r["outstanding"] for r in current_period_rows if r["outstanding"] is not None and r["outstanding"] > 0), 2),
        "row_count_current_period": len(current_period_rows),
    }
    anomalies = (
        f"row0 negative outstanding ({rows[0]['customer']}/{rows[0]['region']})",
        f"row1 missing revenue ({rows[1]['customer']}/{rows[1]['region']})",
        f"row2 extreme outstanding {rows[2]['outstanding']} ({rows[2]['customer']})",
    )
    return MockFinanceDataset(
        row_count=len(rows),
        rows=tuple(rows),
        golden=golden,
        anomalies=anomalies,
    )


def write_sqlite(dataset: MockFinanceDataset, path: str) -> str:
    """Materialize the dataset as a read-only style SQLite warehouse table
    (same schema the governed SQL executor consumes)."""
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE finance(customer TEXT, region TEXT, period TEXT, "
            "revenue REAL, budget REAL, receipts REAL, outstanding REAL, contract_clause TEXT, source_ref TEXT)"
        )
        connection.executemany(
            "INSERT INTO finance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(r["customer"], r["region"], r["period"], r["revenue"], r["budget"],
              r["receipts"], r["outstanding"], r["contract_clause"], r["source_ref"])
             for r in dataset.rows],
        )
        connection.commit()
    finally:
        connection.close()
    return path


# ---------------------------------------------------------------------------
# Mock ERP ticket connector (full W02.05/W10.04 protocol)
# ---------------------------------------------------------------------------


class MockErpTicketConnector:
    """Full-protocol ERP ticket connector mock.

    - invoke: creates a ticket (durable), returns a receipt
    - query_receipt: W02.05 receipt lookup by idempotency key
    - compensate: closes the ticket (reversible mock)
    - fault injection: timeout / unknown outcome / duplicate delivery
    """

    def __init__(self, store_path: str) -> None:
        self.store_path = store_path
        self._tickets: dict[str, dict[str, Any]] = {}
        self._by_idempotency: dict[str, str] = {}
        self.calls = 0
        self.receipt_queries = 0
        self.fail_next_n: int = 0  # inject unknown outcomes

    def invoke(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.fail_next_n > 0:
            self.fail_next_n -= 1
            return {"outcome": "unknown", "effect_applied": None, "receipt": {}}
        key = str(request.get("idempotency_key"))
        if key in self._by_idempotency:
            # idempotent replay: return the original receipt
            return {
                "outcome": "succeeded",
                "effect_applied": True,
                "receipt": dict(self._tickets[self._by_idempotency[key]]),
            }
        ticket_id = f"MOCK-TICKET-{self.calls:04d}"
        receipt = {
            "ticket_id": ticket_id,
            "idempotency_key": key,
            "operation": str(request.get("operation")),
            "status": "created",
        }
        self._tickets[ticket_id] = receipt
        self._by_idempotency[key] = ticket_id
        return {"outcome": "succeeded", "effect_applied": True, "receipt": receipt}

    def query_receipt(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """W02.05: settle interrupted executions from receipts."""
        self.receipt_queries += 1
        key = str(request.get("idempotency_key"))
        ticket_id = self._by_idempotency.get(key)
        if ticket_id is None:
            return None  # nothing dispatched: caller moves to reconciliation
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": dict(self._tickets[ticket_id]),
        }

    def compensate(self, request: dict[str, Any]) -> dict[str, Any]:
        receipt = dict(request.get("receipt") or {})
        ticket_id = receipt.get("ticket_id")
        if ticket_id in self._tickets:
            self._tickets[ticket_id]["status"] = "closed_by_compensation"
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": {"ticket_id": ticket_id, "status": "closed"},
        }

    def ticket(self, ticket_id: str) -> dict[str, Any] | None:
        return self._tickets.get(ticket_id)
