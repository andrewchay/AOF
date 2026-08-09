"""检索增益评估指标计算测试（eval.compute_metrics，纯函数、环境无关）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.retrieval_gain.eval import _match, _match_text, compute_metrics  # noqa: E402

pytestmark = [pytest.mark.unit]


def _item(source: str) -> dict:
    return {"source": source}


class TestMatch:
    def test_exact(self):
        assert _match("member_klub.pdf", "member_klub")

    def test_substring_either_dir(self):
        assert _match("/data/docs/member_klub", "member_klub")
        assert _match("member_klub", "/data/member_klub/full.pdf")

    def test_no_match(self):
        assert not _match("points_system", "member_klub")

    def test_match_text_by_title(self):
        # 结果 source 为 rrf 时，靠 text 里的 doc_title 归属判定
        assert _match_text("## KLUB 会员体系设计\n### 一、会员分层", "KLUB 会员体系")
        assert _match_text("##分站点核心运营数据", "分站点核心运营")
        assert not _match_text("## KPOS 门店交易系统", "微信商圈积分")


class TestComputeMetrics:
    def test_all_hit_top1(self):
        results = {"1": [_item("member_klub"), _item("points_system")]}
        goldens = {"1": "member_klub"}
        m = compute_metrics(results, goldens, k=5)
        assert m["hit_count"] == 1
        assert m["hit_rate"] == 1.0
        assert m["mrr"] == 1.0

    def test_hit_at_rank3_mrr_third(self):
        results = {
            "1": [_item("points_system"), _item("kpos_store"), _item("member_klub")]
        }
        goldens = {"1": "member_klub"}
        m = compute_metrics(results, goldens, k=5)
        assert m["hit_count"] == 1
        assert m["mrr"] == pytest.approx(round(1 / 3, 4))
        assert m["per_query"][0]["rank"] == 3

    def test_missed_mrr_zero(self):
        results = {"1": [_item("points_system"), _item("kpos_store")]}
        goldens = {"1": "member_klub"}
        m = compute_metrics(results, goldens, k=5)
        assert m["hit_count"] == 0
        assert m["hit_rate"] == 0.0
        assert m["mrr"] == 0.0

    def test_k_limits(self):
        # golden 在 rank 6，k=5 时未命中
        results = {
            "1": [_item(f"doc{i}") for i in range(1, 8)] + [_item("member_klub")]
        }
        goldens = {"1": "member_klub"}
        m = compute_metrics(results, goldens, k=5)
        assert m["hit_count"] == 0
        m10 = compute_metrics(results, goldens, k=8)
        assert m10["hit_count"] == 1

    def test_empty_results(self):
        results = {"1": []}
        goldens = {"1": "member_klub"}
        m = compute_metrics(results, goldens, k=5)
        assert m["hit_count"] == 0
        assert m["mrr"] == 0.0

    def test_multiple_queries_average(self):
        results = {
            "1": [_item("member_klub")],
            "2": [_item("points_system"), _item("member_klub")],
        }
        goldens = {"1": "member_klub", "2": "member_klub"}
        m = compute_metrics(results, goldens, k=5)
        assert m["hit_count"] == 2
        assert m["hit_rate"] == 1.0
        assert m["mrr"] == pytest.approx((1.0 + 0.5) / 2)

    def test_hit_by_doc_title_text(self):
        # cognee 结果 source=rrf 时，靠 text 含 doc_title 判定命中
        results = {
            "1": [{"source": "rrf", "text": "## KLUB 会员体系设计\n### 一、会员分层"}],
            "2": [{"source": "rrf", "text": "## 分站点核心运营数据\n## 一、数据概览"}],
        }
        goldens = {"1": "member_klub", "2": "site_data"}
        doc_titles = {"member_klub": "KLUB 会员体系", "site_data": "分站点核心运营"}
        m = compute_metrics(results, goldens, k=5, doc_titles=doc_titles)
        assert m["hit_count"] == 2
        assert m["hit_rate"] == 1.0
        assert m["mrr"] == 1.0

    def test_miss_by_doc_title(self):
        # text 不含 doc_title → miss
        results = {"1": [{"source": "rrf", "text": "## KPOS 门店交易系统"}]}
        goldens = {"1": "member_klub"}
        doc_titles = {"member_klub": "KLUB 会员体系"}
        m = compute_metrics(results, goldens, k=5, doc_titles=doc_titles)
        assert m["hit_count"] == 0
