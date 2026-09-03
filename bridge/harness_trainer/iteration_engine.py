"""Agent Harness Trainer - 迭代引擎.

执行单次迭代：运行 Agent、记录 trace、追踪资产使用.
Phase 2: 支持混合模式（显式声明 + 隐式推断 fallback）.
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
from .explicit_asset_tracker import ExplicitAssetTracker

logger = logging.getLogger(__name__)


class IterationEngine:
    """迭代执行引擎（Phase 2: 混合模式）."""
    
    def __init__(
        self,
        asset_registry: Optional[dict[str, Any]] = None,
        enable_explicit_tracking: bool = True,
    ):
        """
        Args:
            asset_registry: 资产注册表 {asset_name: asset_info}，用于隐式推断
            enable_explicit_tracking: 是否启用显式资产追踪（function calling）
        """
        self.asset_registry = asset_registry or {}
        self.enable_explicit_tracking = enable_explicit_tracking
        self.explicit_tracker = ExplicitAssetTracker() if enable_explicit_tracking else None
    
    def run_iteration(
        self,
        problem: str,
        agent_response: str,
        asset_version: str = "",
        asset_changes: list[AssetChange] = None,
        expert_score: Optional[ExpertScore] = None,
        expert_feedback: str = "",
        trace: Optional[dict[str, Any]] = None,
        tool_calls: Optional[list[dict[str, Any]]] = None,
    ) -> Iteration:
        """执行一次迭代并记录.
        
        Args:
            tool_calls: Agent 的 function calling 记录（显式追踪用）
        """
        # 追踪资产使用（Phase 2: 混合模式）
        assets_used = self._track_assets(agent_response, tool_calls)
        
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
    
    def _track_assets(
        self,
        response: str,
        tool_calls: Optional[list[dict[str, Any]]] = None,
    ) -> list[AssetUsage]:
        """追踪资产使用（混合模式）.
        
        优先使用显式追踪（function calling），
        无显式记录时 fallback 到隐式推断（文本匹配）.
        """
        usages: list[AssetUsage] = []
        
        # 1. 显式追踪（优先）
        if self.enable_explicit_tracking and tool_calls:
            usages = self.explicit_tracker.track_from_tool_calls(
                tool_calls, self.asset_registry
            )
            if usages:
                logger.info(f"[IterationEngine] Explicit tracking: {len(usages)} assets used")
                return usages
        
        # 2. 隐式推断（fallback）
        usages = self._track_assets_implicit(response)
        if usages:
            logger.info(f"[IterationEngine] Implicit tracking: {len(usages)} assets used")
        
        return usages
    
    def _track_assets_implicit(self, response: str) -> list[AssetUsage]:
        """隐式推断资产使用：通过文本匹配资产名称.
        
        MVP 实现：简单关键词匹配。作为显式追踪的 fallback.
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
                    is_critical=False,  # 隐式推断不判断关键性
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
    
    def get_query_tool_schema(self) -> Optional[dict[str, Any]]:
        """获取 query_asset 工具 schema（供 Agent 使用）."""
        if self.explicit_tracker:
            return self.explicit_tracker.build_query_tool_schema()
        return None
