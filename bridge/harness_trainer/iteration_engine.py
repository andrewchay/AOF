"""Agent Harness Trainer - 迭代引擎.

执行单次迭代：运行 Agent、记录 trace、追踪资产使用（MVP 隐式推断）.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .models import (
    AssetChange,
    AssetType,
    AssetUsage,
    ExpertScore,
    Iteration,
    UsageContext,
)

logger = logging.getLogger(__name__)


class IterationEngine:
    """迭代执行引擎."""
    
    def __init__(self, asset_registry: Optional[dict[str, Any]] = None):
        """
        Args:
            asset_registry: 资产注册表 {asset_name: asset_info}，用于隐式推断
        """
        self.asset_registry = asset_registry or {}
    
    def run_iteration(
        self,
        problem: str,
        agent_response: str,
        asset_version: str = "",
        asset_changes: list[AssetChange] = None,
        expert_score: Optional[ExpertScore] = None,
        expert_feedback: str = "",
        trace: Optional[dict[str, Any]] = None,
    ) -> Iteration:
        """执行一次迭代并记录."""
        # 追踪资产使用（MVP: 隐式推断）
        assets_used = self._track_assets_implicit(agent_response)
        
        return Iteration(
            number=0,  # 由 SessionManager 自动编号
            asset_version=asset_version,
            asset_changes=asset_changes or [],
            agent_response=agent_response,
            trace=trace or {},
            assets_used=assets_used,
            expert_score=expert_score or ExpertScore(),
            expert_feedback=expert_feedback,
        )
    
    def _track_assets_implicit(self, response: str) -> list[AssetUsage]:
        """隐式推断资产使用：通过文本匹配资产名称.
        
        MVP 实现：简单关键词匹配。长期应升级为显式声明（function calling）。
        """
        usages: list[AssetUsage] = []
        response_lower = response.lower()
        
        for asset_name, asset_info in self.asset_registry.items():
            # 简单关键词匹配
            if asset_name.lower() in response_lower:
                usage = AssetUsage(
                    asset_type=AssetType(asset_info.get("type", "knowledge_chunk")),
                    asset_id=asset_info.get("id", asset_name),
                    asset_name=asset_name,
                    asset_version=asset_info.get("version", ""),
                    usage_context=UsageContext.ANSWER_REFERENCE,
                    relevance_score=self._compute_relevance(asset_name, response),
                    is_critical=False,  # MVP 不判断关键性
                )
                usages.append(usage)
        
        return usages
    
    def _compute_relevance(self, asset_name: str, response: str) -> float:
        """计算资产与回答的相关度（简单版：出现次数/回答长度）."""
        count = response.lower().count(asset_name.lower())
        return min(count * 0.1, 1.0)  # 上限 1.0
    
    def register_asset(
        self,
        name: str,
        asset_id: str,
        asset_type: AssetType,
        version: str = "",
    ) -> None:
        """注册资产到推断库."""
        self.asset_registry[name] = {
            "id": asset_id,
            "type": asset_type.value,
            "version": version,
        }
