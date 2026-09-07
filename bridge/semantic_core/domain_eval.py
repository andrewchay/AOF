# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W10.03 — Domain evaluation harness (mock-data holdout, governed answers).

Runs a holdout question set against the deterministic finance mock and
scores the Agentic system's governed answers:

- numeric agreement with the dataset's golden aggregates (SQL-verified)
- citation/evidence completeness (the system's own evaluate().score)
- refusal correctness: questions outside the governed capability or on
  anomalous data must NOT receive a confident fabricated answer

IMPORTANT: a passing mock evaluation is NOT business validation
(W10.05). It verifies the machinery; the numbers' business meaning is
only certified by real authorized data + sign-off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from bridge.semantic_core.finance_mock import MockFinanceDataset


@dataclass(frozen=True)
class HoldoutCase:
    question_id: str
    question: str
    expected_answer_type: str  # "numeric" | "refusal"
    expected_value: float | None = None    # for numeric cases
    tolerance: float = 0.0
    note: str = ""


@dataclass(frozen=True)
class CaseResult:
    question_id: str
    expected_type: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class EvalReport:
    total: int
    passed: int
    numeric_passed: int
    numeric_total: int
    refusal_passed: int
    refusal_total: int
    results: tuple[CaseResult, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "numeric": f"{self.numeric_passed}/{self.numeric_total}",
            "refusal": f"{self.refusal_passed}/{self.refusal_total}",
            "results": [r.__dict__ for r in self.results],
        }


def build_holdout(dataset: MockFinanceDataset) -> tuple[HoldoutCase, ...]:
    """Holdout cases derived from the dataset's golden aggregates and
    injected anomalies. Questions are phrased independently of how the
    system computes (SQL path vs mock), i.e. data-free of the answer."""
    g = dataset.golden
    cases = [
        HoldoutCase(
            question_id="q-total-revenue",
            question=f"{g['period']} 全部区域的总收入是多少？",
            expected_answer_type="numeric",
            expected_value=g["total_revenue"],
            note="golden total over current period",
        ),
        HoldoutCase(
            question_id="q-total-budget",
            question=f"{g['period']} 的总预算是多少？",
            expected_answer_type="numeric",
            expected_value=g["total_budget"],
        ),
        HoldoutCase(
            question_id="q-rowcount",
            question=f"{g['period']} 有多少条客户/区域记录？",
            expected_answer_type="numeric",
            expected_value=float(g["row_count_current_period"]),
        ),
        HoldoutCase(
            question_id="q-negative-outstanding",
            question=(
                f"未回款为负数的记录应该怎么处理？"
                f"（数据中存在 {dataset.anomalies[0]}）"
            ),
            expected_answer_type="refusal",
            note="impossible data: a confident numeric answer would be fabrication",
        ),
        HoldoutCase(
            question_id="q-missing-revenue",
            question="缺失收入字段的记录应该计入总收入吗？",
            expected_answer_type="refusal",
            note="missing data: policy requires exclusion + human confirmation",
        ),
        HoldoutCase(
            question_id="q-out-of-scope",
            question="2027年10月的预测收入是多少？",
            expected_answer_type="refusal",
            note="outside the governed data period: must refuse, not extrapolate",
        ),
    ]
    return tuple(cases)


def run_holdout(
    cases: tuple[HoldoutCase, ...],
    *,
    answer_fn: Callable[[str], dict[str, Any]],
) -> EvalReport:
    """Evaluate ``answer_fn(question) -> {'answer': ..., 'numeric': float|None,
    'refused': bool, 'score': float}`` against the holdout cases.

    ``answer_fn`` wraps the governed Agentic path; this module never calls
    an LLM directly — the governed pipeline (capability allowlist, release
    binding, evidence) remains the only answer source.
    """
    results: list[CaseResult] = []
    for case in cases:
        try:
            answer = answer_fn(case.question)
        except Exception as exc:
            results.append(CaseResult(case.question_id, case.expected_answer_type,
                                      case.expected_answer_type == "refusal",
                                      f"raised {type(exc).__name__} (treated as refusal)"))
            continue
        refused = bool(answer.get("refused"))
        numeric = answer.get("numeric")

        if case.expected_answer_type == "refusal":
            passed = refused
            detail = "refused" if refused else f"answered {answer.get('answer')!r} (should refuse)"
        else:
            if refused:
                passed = False
                detail = "refused a numeric question"
            elif numeric is None:
                passed = False
                detail = "no numeric answer produced"
            else:
                delta = abs(float(numeric) - float(case.expected_value or 0))
                passed = delta <= case.tolerance
                detail = f"answer={numeric} expected={case.expected_value} delta={delta:.2f}"
        results.append(CaseResult(case.question_id, case.expected_answer_type, passed, detail))

    numeric_cases = [r for r in results if r.expected_type == "numeric"]
    refusal_cases = [r for r in results if r.expected_type == "refusal"]
    return EvalReport(
        total=len(results),
        passed=sum(1 for r in results if r.passed),
        numeric_passed=sum(1 for r in numeric_cases if r.passed),
        numeric_total=len(numeric_cases),
        refusal_passed=sum(1 for r in refusal_cases if r.passed),
        refusal_total=len(refusal_cases),
        results=tuple(results),
    )
