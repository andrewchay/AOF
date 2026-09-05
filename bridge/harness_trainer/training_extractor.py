"""Agent Harness Trainer - 训练数据提取器.

从满意迭代中提取 Agent-Tool + 资产引用 训练样本.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from bridge.training_data.models import AgentToolSample, SampleSource

from .models import AssetUsage, HarnessSession, Iteration

logger = logging.getLogger(__name__)


class TrainingDataExtractor:
    """训练数据提取器.
    
    从满意迭代中提取 Agent-Tool 格式的训练样本：
    - messages: [system, user(问题), assistant(回答+推理)]
    - tools: 资产查询工具 schema
    - tool_calls: 实际调用的资产查询
    """
    
    def extract_from_session(
        self,
        session: HarnessSession,
        include_all_satisfactory: bool = True,
    ) -> list[AgentToolSample]:
        """从会话中提取训练样本."""
        iterations = (
            session.satisfactory_iterations
            if include_all_satisfactory
            else ([session.best_iteration] if session.best_iteration else [])
        )
        
        samples: list[AgentToolSample] = []
        for iteration in iterations:
            sample = self._extract_from_iteration(session, iteration)
            if sample:
                samples.append(sample)
        
        logger.info(f"[Harness] Extracted {len(samples)} training samples from session {session.id}")
        return samples
    
    def _extract_from_iteration(
        self,
        session: HarnessSession,
        iteration: Iteration,
    ) -> Optional[AgentToolSample]:
        """从单次迭代提取样本."""
        if not iteration.is_satisfactory:
            return None
        
        # 1. 构建资产查询工具 schema
        tools = self._build_asset_query_tools(iteration.assets_used)
        
        # 2. 构建 messages
        system_prompt = self._build_system_prompt(session)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": session.problem_statement},
        ]
        
        # 3. 构建 assistant 消息（含推理过程）
        reasoning = self._extract_reasoning(iteration)
        assistant_content = f"{reasoning}\n\n{iteration.agent_response}".strip()
        messages.append({"role": "assistant", "content": assistant_content})
        
        # 4. 构建 tool_calls
        tool_calls = self._build_tool_calls(iteration.assets_used)
        
        return AgentToolSample(
            messages=messages,
            tools=tools,
            tool_calls=tool_calls,
            reasoning=reasoning,
            source=SampleSource(
                dataset_name=session.pattern_type or "harness_training",
                source_type="harness_iteration",
                source_id=f"{session.id}_iter{iteration.number}",
            ),
            metadata={
                "session_id": session.id,
                "iteration_number": iteration.number,
                "pattern_type": session.pattern_type,
                "expert_score": iteration.expert_score.to_dict(),
                "assets_used_count": len(iteration.assets_used),
            },
        )
    
    def _build_system_prompt(self, session: HarnessSession) -> str:
        """构建 system prompt."""
        domain = session.domain or "专业"
        return (
            f"你是一个{domain}领域的智能助手。"
            f"在回答问题时，你应该先分析需要哪些专业知识，"
            f"然后调用相应的资产查询工具获取信息，最后给出完整回答。"
        )
    
    def _build_asset_query_tools(
        self,
        assets_used: list[AssetUsage],
    ) -> list[dict[str, Any]]:
        """构建资产查询工具 schema."""
        tools = []
        for usage in assets_used:
            tool = {
                "type": "function",
                "function": {
                    "name": "query_asset",
                    "description": f"查询{usage.asset_name}相关信息",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "asset_type": {
                                "type": "string",
                                "enum": ["ontology_class", "playbook_step", "knowledge_chunk", "tool_schema"],
                            },
                            "asset_id": {"type": "string"},
                            "query": {"type": "string", "description": "查询意图"},
                        },
                        "required": ["asset_type", "asset_id"],
                    },
                },
            }
            tools.append(tool)
        return tools
    
    def _build_tool_calls(
        self,
        assets_used: list[AssetUsage],
    ) -> list[dict[str, Any]]:
        """构建 tool_calls（记录实际调用的资产查询）."""
        tool_calls = []
        for i, usage in enumerate(assets_used):
            tool_calls.append({
                "id": f"call_{usage.asset_id}_{i}",
                "type": "function",
                "function": {
                    "name": "query_asset",
                    "arguments": json.dumps({
                        "asset_type": usage.asset_type.value,
                        "asset_id": usage.asset_id,
                        "query": usage.usage_context.value,
                    }, ensure_ascii=False),
                },
            })
        return tool_calls
    
    def _extract_reasoning(self, iteration: Iteration) -> str:
        """从迭代中提取推理过程."""
        # MVP: 从 expert_feedback 或 trace 中提取
        # 长期: 从 Agent 的思考链中提取
        if iteration.expert_feedback:
            return f"[专家反馈] {iteration.expert_feedback[:200]}"
        
        # 从资产使用构建推理描述
        if iteration.assets_used:
            asset_names = [u.asset_name for u in iteration.assets_used]
            return f"[推理] 回答时参考了: {', '.join(asset_names)}"
        
        return ""
