"""Agent Harness Trainer - 显式资产追踪器.

Phase 2 升级: 通过 function calling 方式显式追踪资产使用.

Agent 回答时，通过调用 query_asset 工具显式声明需要查询哪些资产，
工具调用记录被捕获为资产使用的显式证据，比 MVP 的隐式推断更精确.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .models import AssetType, AssetUsage, UsageContext

logger = logging.getLogger(__name__)


class ExplicitAssetTracker:
    """显式资产追踪器.
    
    通过解析 Agent 的 function calling 记录，提取显式资产查询.
    """
    
    def __init__(self):
        self._query_tool_name = "query_asset"
    
    def track_from_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        asset_registry: Optional[dict[str, Any]] = None,
    ) -> list[AssetUsage]:
        """从 tool_calls 中提取显式资产使用.
        
        Args:
            tool_calls: Agent 的 function calling 记录
            asset_registry: 资产注册表（用于补充资产信息）
            
        Returns:
            资产使用列表
        """
        usages: list[AssetUsage] = []
        
        for call in tool_calls:
            func = call.get("function", {})
            if func.get("name") != self._query_tool_name:
                continue
            
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                logger.warning(f"[ExplicitAssetTracker] invalid arguments: {func.get('arguments')}")
                continue
            
            asset_type = args.get("asset_type", "knowledge_chunk")
            asset_id = args.get("asset_id", "")
            _ = args.get("query", "")  # 保留用于日志或扩展
            
            if not asset_id:
                continue
            
            # 从注册表补充资产信息
            asset_name = asset_id
            asset_version = ""
            if asset_registry and asset_id in asset_registry:
                info = asset_registry[asset_id]
                asset_name = info.get("name", asset_id)
                asset_version = info.get("version", "")
            
            usage = AssetUsage(
                asset_type=AssetType(asset_type),
                asset_id=asset_id,
                asset_name=asset_name,
                asset_version=asset_version,
                usage_context=UsageContext.TOOL_ARGUMENT,
                relevance_score=1.0,  # 显式查询的相关度为 1.0
                is_critical=True,     # 显式查询通常是关键支撑
            )
            usages.append(usage)
        
        return usages
    
    def build_query_tool_schema(self) -> dict[str, Any]:
        """构建 query_asset 工具的 schema（供 Agent 使用）."""
        return {
            "type": "function",
            "function": {
                "name": self._query_tool_name,
                "description": "查询特定资产以获取回答所需的专业知识",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "asset_type": {
                            "type": "string",
                            "enum": [t.value for t in AssetType],
                            "description": "资产类型",
                        },
                        "asset_id": {
                            "type": "string",
                            "description": "资产唯一标识",
                        },
                        "query": {
                            "type": "string",
                            "description": "查询意图（为什么需要这个资产）",
                        },
                    },
                    "required": ["asset_type", "asset_id"],
                },
            },
        }
