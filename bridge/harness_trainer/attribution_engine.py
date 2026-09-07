# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Agent Harness Trainer - 归因引擎.

分析资产改动与满意度提升的因果关系，输出归因报告（含置信度）.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from .models import (
    AttributionDetail,
    AttributionReport,
    HarnessSession,
    Iteration,
)

logger = logging.getLogger(__name__)


class AttributionEngine:
    """归因分析引擎.
    
    MVP 实现：简单的前后对比 + 置信度标注。
    长期：控制实验 + 因果推断。
    """
    
    def analyze(self, session: HarnessSession) -> AttributionReport:
        """分析会话的归因关系."""
        if len(session.iterations) < 2:
            return AttributionReport(
                key_insights=["迭代次数不足，无法归因（至少需要 2 轮迭代）"],
                overall_confidence=0.0,
            )
        
        # 1. 按资产改动分组迭代
        change_groups = self._group_by_change(session.iterations)
        
        # 2. 计算每种改动的效果
        details: list[AttributionDetail] = []
        for change, iterations in change_groups.items():
            detail = self._compute_attribution(change, iterations)
            if detail:
                details.append(detail)
        
        # 3. 排序，提取关键洞察
        details.sort(key=lambda d: d.improvement, reverse=True)
        
        # 4. 计算相关性统计
        change_type_corr = self._compute_change_type_correlation(details)
        asset_type_corr = self._compute_asset_type_correlation(details)
        
        # 5. 生成关键洞察
        insights = self._generate_insights(details)
        
        # 6. 计算整体置信度
        overall_confidence = self._compute_overall_confidence(details, len(session.iterations))
        
        return AttributionReport(
            change_type_correlation=change_type_corr,
            asset_type_correlation=asset_type_corr,
            key_insights=insights,
            attribution_details=details,
            overall_confidence=overall_confidence,
        )
    
    def _group_by_change(
        self,
        iterations: list[Iteration],
    ) -> dict[str, list[Iteration]]:
        """按资产改动（asset_id）分组迭代."""
        groups: dict[str, list[Iteration]] = defaultdict(list)
        
        for i, iteration in enumerate(iterations):
            for change in iteration.asset_changes:
                # 用 asset_id 作为 key（AssetChange 本身不可 hash）
                key = f"{change.asset_type.value}:{change.asset_id}"
                # 该改动影响从本轮开始的所有后续迭代
                groups[key].extend(iterations[i:])
        
        return groups
    
    def _compute_attribution(
        self,
        change_key: str,
        iterations: list[Iteration],
    ) -> Optional[AttributionDetail]:
        """计算单条改动的归因效果."""
        if not iterations:
            return None
        
        # 从 key 解析出改动信息
        change = None
        for it in iterations:
            for c in it.asset_changes:
                if f"{c.asset_type.value}:{c.asset_id}" == change_key:
                    change = c
                    break
            if change:
                break
        
        if not change:
            return None
        
        # 改动前的评分（改动所在迭代的前一轮）
        first_iter = iterations[0]
        before_iterations = [it for it in iterations if it.number < first_iter.number]
        before_score = (
            sum(it.expert_score.overall for it in before_iterations) / len(before_iterations)
            if before_iterations else 0.0
        )
        
        # 改动后的评分（改动所在迭代及之后）
        after_iterations = [it for it in iterations if it.number >= first_iter.number]
        after_score = (
            sum(it.expert_score.overall for it in after_iterations) / len(after_iterations)
            if after_iterations else 0.0
        )
        
        improvement = after_score - before_score
        
        # 置信度计算（基于样本量，至少 1 轮就有基础置信度）
        confidence = min(max(len(after_iterations), 1) * 0.2, 1.0)
        
        return AttributionDetail(
            asset_change=change,
            before_score=round(before_score, 2),
            after_score=round(after_score, 2),
            improvement=round(improvement, 2),
            evidence=[f"迭代 {it.number}: score={it.expert_score.overall}" for it in after_iterations[:3]],
            confidence=round(confidence, 2),
        )
    
    def _compute_change_type_correlation(
        self,
        details: list[AttributionDetail],
    ) -> dict[str, float]:
        """计算改动类型与效果的相关性."""
        type_scores: dict[str, list[float]] = defaultdict(list)
        for d in details:
            ct = d.asset_change.change_type.value
            type_scores[ct].append(d.improvement)
        
        return {
            ct: round(sum(scores) / len(scores), 2)
            for ct, scores in type_scores.items()
        }
    
    def _compute_asset_type_correlation(
        self,
        details: list[AttributionDetail],
    ) -> dict[str, float]:
        """计算资产类型与效果的相关性."""
        type_scores: dict[str, list[float]] = defaultdict(list)
        for d in details:
            at = d.asset_change.asset_type.value
            type_scores[at].append(d.improvement)
        
        return {
            at: round(sum(scores) / len(scores), 2)
            for at, scores in type_scores.items()
        }
    
    def _generate_insights(self, details: list[AttributionDetail]) -> list[str]:
        """生成关键洞察."""
        insights: list[str] = []
        
        if not details:
            return insights
        
        # 最佳改动
        best = details[0]
        if best.improvement > 0:
            insights.append(
                f"「{best.asset_change.diff_summary[:40]}」带来最大提升 "
                f"(+{best.improvement}分，置信度{best.confidence})"
            )
        
        # 有效改动类型
        positive_changes = [d for d in details if d.improvement > 0]
        if positive_changes:
            change_types = set(d.asset_change.change_type.value for d in positive_changes)
            insights.append(
                f"有效的改动类型: {', '.join(change_types)} "
                f"(共{len(positive_changes)}条正向归因)"
            )
        
        # 无效改动
        negative_changes = [d for d in details if d.improvement < 0]
        if negative_changes:
            insights.append(
                f"注意：{len(negative_changes)}条改动导致满意度下降，"
                f"建议回滚或重新设计"
            )
        
        return insights
    
    def _compute_overall_confidence(
        self,
        details: list[AttributionDetail],
        total_iterations: int,
    ) -> float:
        """计算整体置信度."""
        if not details:
            return 0.0
        
        # 基于：归因数量、平均置信度、迭代次数
        avg_confidence = sum(d.confidence for d in details) / len(details)
        iteration_factor = min(total_iterations / 5, 1.0)  # 5轮以上满分
        
        return round(avg_confidence * iteration_factor, 2)
