# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Agent 工具调用数据生成器.

基于业务策略本体和图谱操作概念，生成 function calling 格式训练样本.

生成策略:
1. 策略部署: 将策略模板转化为 tool_use 调用
2. 诊断查询: 将业务诊断问题转化为数据查询工具调用
3. 监控响应: 基于监控指标阈值生成告警响应的 tool 调用序列

工具 Schema 来源:
- 从图谱中标记为 "Action"/"Operation"/"Strategy" 的节点提取
- 从 config.tool_schema_source 加载外部 JSON schema 文件
- 提供默认的通用工具模板
"""

from __future__ import annotations

import json
import logging
import random
from typing import AsyncIterator, Callable, Optional, Any

from ..models import (
    GeneratorConfig,
    AgentToolSample,
    TrainingSample,
    SampleSource,
)
from ..loaders import GraphLoader, DocumentLoader
from .base import GeneratorBase

logger = logging.getLogger(__name__)


# 默认工具 schema 模板（通用业务操作工具）
DEFAULT_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "query_knowledge_graph",
            "description": "查询知识图谱获取实体信息、关系或执行图分析",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["entity", "relation", "path", "statistics"],
                        "description": "查询类型",
                    },
                    "entity_name": {
                        "type": "string",
                        "description": "实体名称（用于 entity 查询）",
                    },
                    "source_entity": {
                        "type": "string",
                        "description": "起始实体（用于 path 查询）",
                    },
                    "target_entity": {
                        "type": "string",
                        "description": "目标实体（用于 path 查询）",
                    },
                },
                "required": ["query_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "基于知识图谱数据生成业务分析报告",
            "parameters": {
                "type": "object",
                "properties": {
                    "report_type": {
                        "type": "string",
                        "enum": ["summary", "analysis", "comparison"],
                        "description": "报告类型",
                    },
                    "topic": {
                        "type": "string",
                        "description": "报告主题",
                    },
                    "entities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要包含的实体列表",
                    },
                },
                "required": ["report_type", "topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_strategy",
            "description": "执行业务策略，如发送营销邮件、调整投放参数等",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_name": {
                        "type": "string",
                        "description": "策略名称",
                    },
                    "target_population": {
                        "type": "string",
                        "description": "目标人群",
                    },
                    "parameters": {
                        "type": "object",
                        "description": "策略执行参数",
                    },
                },
                "required": ["strategy_name", "target_population"],
            },
        },
    },
]

# 用户意图模板 -> 预期工具调用
TOOL_USE_TEMPLATES = [
    {
        "intent": "查询实体信息",
        "user_messages": [
            "查询{name}的信息",
            "{name}是什么？",
            "给我{name}的详细资料",
        ],
        "tool": "query_knowledge_graph",
        "argument_builder": lambda ctx: {
            "query_type": "entity",
            "entity_name": ctx.get("name", ""),
        },
    },
    {
        "intent": "查询关系",
        "user_messages": [
            "{subject}和{object}有什么关系？",
            "分析一下{subject}与{object}的关联",
        ],
        "tool": "query_knowledge_graph",
        "argument_builder": lambda ctx: {
            "query_type": "relation",
            "entity_name": ctx.get("subject", ""),
        },
    },
    {
        "intent": "生成报告",
        "user_messages": [
            "生成一份关于{topic}的报告",
            "分析一下{topic}的情况",
            "帮我总结{topic}",
        ],
        "tool": "generate_report",
        "argument_builder": lambda ctx: {
            "report_type": "analysis",
            "topic": ctx.get("topic", ""),
            "entities": ctx.get("entities", []),
        },
    },
    {
        "intent": "执行策略",
        "user_messages": [
            "对{target}执行{strategy}",
            "启动{strategy}，目标人群是{target}",
            "执行{strategy}策略",
        ],
        "tool": "execute_strategy",
        "argument_builder": lambda ctx: {
            "strategy_name": ctx.get("strategy", ""),
            "target_population": ctx.get("target", ""),
            "parameters": ctx.get("params", {}),
        },
    },
]


class AgentToolGenerator(GeneratorBase):
    """Agent 工具调用数据生成器."""

    def __init__(self, name: Optional[str] = None):
        super().__init__(name)
        self._tool_schemas: list[dict[str, Any]] = []

    @property
    def sample_type(self) -> str:
        return "agent_tool"

    async def estimate_yield(
        self,
        node_count: int,
        edge_count: int,
        document_count: int,
    ) -> int:
        """估算产出: 基于节点和工具模板组合生成."""
        return min(node_count * 2 + 50, 5000)

    async def generate(
        self,
        graph_loader: GraphLoader,
        doc_loader: DocumentLoader,
        config: GeneratorConfig,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> AsyncIterator[TrainingSample]:
        """生成 Agent 工具调用样本."""
        count = 0
        max_samples = config.max_samples

        # 加载工具 schemas
        self._tool_schemas = await self._load_tool_schemas(config)

        graph_data = await graph_loader.load_all(node_limit=max_samples * 2)

        total_estimate = await self.estimate_yield(
            len(graph_data.nodes),
            len(graph_data.edges),
            0,
        )
        total_estimate = min(total_estimate, max_samples)

        # 1. 基于实体生成查询类工具调用
        async for sample in self._generate_entity_queries(
            graph_data.nodes, config, max_samples - count
        ):
            if count >= max_samples:
                break
            count += 1
            self._report_progress(count, total_estimate, "entity query", progress_callback)
            yield sample

        # 2. 基于关系生成分析类工具调用
        async for sample in self._generate_relation_queries(
            graph_data.triples, config, max_samples - count
        ):
            if count >= max_samples:
                break
            count += 1
            self._report_progress(count, total_estimate, "relation query", progress_callback)
            yield sample

        # 3. 基于策略/操作节点生成执行类工具调用
        async for sample in self._generate_strategy_executions(
            graph_data.nodes, config, max_samples - count
        ):
            if count >= max_samples:
                break
            count += 1
            self._report_progress(count, total_estimate, "strategy execution", progress_callback)
            yield sample

        # 4. 通用工具调用模板
        async for sample in self._generate_generic_templates(
            config, max_samples - count
        ):
            if count >= max_samples:
                break
            count += 1
            self._report_progress(count, total_estimate, "generic template", progress_callback)
            yield sample

        logger.info(f"AgentToolGenerator produced {count} samples")

    async def _load_tool_schemas(
        self,
        config: GeneratorConfig,
    ) -> list[dict[str, Any]]:
        """加载工具 schema 定义."""
        schemas = []

        # 1. 尝试从配置文件加载
        if config.tool_schema_source:
            try:
                import pathlib
                path = pathlib.Path(config.tool_schema_source)
                if path.exists():
                    with open(path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, list):
                            schemas.extend(loaded)
                        elif isinstance(loaded, dict) and "tools" in loaded:
                            schemas.extend(loaded["tools"])
                        logger.info(f"Loaded {len(schemas)} tool schemas from {path}")
            except Exception as e:
                logger.warning(f"Failed to load tool schemas from {config.tool_schema_source}: {e}")

        # 2. 如果没有加载到，使用默认模板
        if not schemas:
            schemas = DEFAULT_TOOL_SCHEMAS.copy()
            logger.info("Using default tool schemas")

        return schemas

    async def _generate_entity_queries(
        self,
        nodes: list,
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[AgentToolSample]:
        """基于实体生成查询工具调用."""
        random.seed(60)
        shuffled = nodes.copy()
        random.shuffle(shuffled)

        # 过滤出有名字的节点
        named_nodes = [n for n in shuffled if self._get_name(n)]

        for node in named_nodes[:remaining]:
            name = self._get_name(node)
            if not name:
                continue

            # 选择查询工具模板
            tmpl = TOOL_USE_TEMPLATES[0]  # "查询实体信息"
            user_msg = random.choice(tmpl["user_messages"]).format(name=name)

            # 构建工具调用参数
            ctx = {"name": name}
            arguments = tmpl["argument_builder"](ctx)

            # 找到对应的工具 schema
            tool_schema = self._find_tool_schema(tmpl["tool"])
            if not tool_schema:
                continue

            yield self._build_sample(
                user_message=user_msg,
                tools=[tool_schema],
                tool_name=tmpl["tool"],
                arguments=arguments,
                reasoning=f"用户想查询实体'{name}'的信息，应调用{tmpl['tool']}工具。",
                source_type="node",
                source_id=str(getattr(node, "id", "")),
            )

    async def _generate_relation_queries(
        self,
        triples: list,
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[AgentToolSample]:
        """基于关系生成分析工具调用."""
        random.seed(61)
        shuffled = triples.copy()
        random.shuffle(shuffled)

        for triple in shuffled[:remaining]:
            subject_name = self._get_name(triple.subject)
            object_name = self._get_name(triple.object)

            if not subject_name or not object_name:
                continue

            # 使用"查询关系"模板
            tmpl = TOOL_USE_TEMPLATES[1]
            user_msg = random.choice(tmpl["user_messages"]).format(
                subject=subject_name, object=object_name
            )

            ctx = {"subject": subject_name, "object": object_name}
            arguments = tmpl["argument_builder"](ctx)

            tool_schema = self._find_tool_schema(tmpl["tool"])
            if not tool_schema:
                continue

            yield self._build_sample(
                user_message=user_msg,
                tools=[tool_schema],
                tool_name=tmpl["tool"],
                arguments=arguments,
                reasoning=f"用户想了解'{subject_name}'和'{object_name}'的关系，应调用图谱查询工具。",
                source_type="triple",
                source_id=f"{getattr(triple.subject, 'id', '')}-{triple.predicate}-{getattr(triple.object, 'id', '')}",
            )

    async def _generate_strategy_executions(
        self,
        nodes: list,
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[AgentToolSample]:
        """基于策略/操作节点生成执行类工具调用."""
        random.seed(62)

        # 识别策略/操作类节点（通过标签或属性）
        strategy_nodes = []
        for node in nodes:
            labels = getattr(node, "labels", []) or []
            props = getattr(node, "properties", {}) or {}
            if any(label.lower() in {"strategy", "action", "operation", "campaign"} for label in labels):
                strategy_nodes.append(node)
            elif props.get("type", "").lower() in {"strategy", "action", "operation"}:
                strategy_nodes.append(node)

        if not strategy_nodes:
            # 如果没有明确的策略节点，从所有节点中随机选取
            shuffled = nodes.copy()
            random.shuffle(shuffled)
            strategy_nodes = shuffled[: min(remaining, len(shuffled) // 5)]

        for node in strategy_nodes[:remaining]:
            name = self._get_name(node)
            if not name:
                continue

            # 使用"执行策略"模板
            tmpl = TOOL_USE_TEMPLATES[3]
            target = "目标用户群体"  # 默认目标
            user_msg = random.choice(tmpl["user_messages"]).format(
                strategy=name, target=target
            )

            ctx = {
                "strategy": name,
                "target": target,
                "params": self._extract_strategy_params(node),
            }
            arguments = tmpl["argument_builder"](ctx)

            tool_schema = self._find_tool_schema(tmpl["tool"])
            if not tool_schema:
                continue

            yield self._build_sample(
                user_message=user_msg,
                tools=[tool_schema],
                tool_name=tmpl["tool"],
                arguments=arguments,
                reasoning=f"用户要求执行'{name}'策略，目标人群为'{target}'，应调用策略执行工具。",
                source_type="node",
                source_id=str(getattr(node, "id", "")),
            )

    async def _generate_generic_templates(
        self,
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[AgentToolSample]:
        """生成通用工具调用模板样本."""
        random.seed(63)

        # 为每个工具 schema 生成几个通用示例
        for tool_schema in self._tool_schemas:
            if remaining <= 0:
                break

            tool_name = tool_schema.get("function", {}).get("name", "")
            if not tool_name:
                continue

            # 找到匹配的模板
            matching_tmpl = None
            for tmpl in TOOL_USE_TEMPLATES:
                if tmpl["tool"] == tool_name:
                    matching_tmpl = tmpl
                    break

            if not matching_tmpl:
                continue

            # 生成 1-2 个样本
            for _ in range(min(2, remaining)):
                if remaining <= 0:
                    break

                # 使用通用上下文
                ctx = {
                    "name": "示例实体",
                    "subject": "实体A",
                    "object": "实体B",
                    "topic": "业务分析主题",
                    "entities": ["实体A", "实体B"],
                    "strategy": "示例策略",
                    "target": "示例目标人群",
                    "params": {},
                }

                user_msg = random.choice(matching_tmpl["user_messages"])
                # 格式化消息（只使用部分占位符）
                try:
                    user_msg = user_msg.format(**ctx)
                except KeyError:
                    user_msg = user_msg.replace("{", "{{").replace("}", "}}")
                    user_msg = user_msg.format(**{k: v for k, v in ctx.items() if isinstance(v, str)})

                arguments = matching_tmpl["argument_builder"](ctx)

                yield self._build_sample(
                    user_message=user_msg,
                    tools=[tool_schema],
                    tool_name=tool_name,
                    arguments=arguments,
                    reasoning=f"通用示例：用户意图为'{matching_tmpl['intent']}'，调用工具'{tool_name}'。",
                    source_type="template",
                )
                remaining -= 1

    def _build_sample(
        self,
        user_message: str,
        tools: list[dict[str, Any]],
        tool_name: str,
        arguments: dict[str, Any],
        reasoning: str,
        source_type: str,
        source_id: str = "",
    ) -> AgentToolSample:
        """构建 AgentToolSample."""
        import uuid

        return AgentToolSample(
            messages=[
                {"role": "user", "content": user_message},
            ],
            tools=tools,
            tool_calls=[
                {
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                },
            ],
            reasoning=reasoning,
            source=SampleSource(
                dataset_name="",
                source_type=source_type,
                source_id=source_id,
            ),
        )

    def _find_tool_schema(self, tool_name: str) -> Optional[dict[str, Any]]:
        """根据工具名查找 schema."""
        for schema in self._tool_schemas:
            func_name = schema.get("function", {}).get("name", "")
            if func_name == tool_name:
                return schema
        return None

    @staticmethod
    def _get_name(node) -> str:
        """获取节点名称."""
        props = getattr(node, "properties", {}) or {}
        return (
            props.get("name")
            or props.get("title")
            or props.get("label")
            or str(getattr(node, "id", ""))
        )

    @staticmethod
    def _extract_strategy_params(node) -> dict[str, Any]:
        """从策略节点提取参数."""
        props = getattr(node, "properties", {}) or {}
        params = {}
        for k, v in props.items():
            if k not in {"id", "name", "title", "label", "description", "type"}:
                params[k] = v
        return params
