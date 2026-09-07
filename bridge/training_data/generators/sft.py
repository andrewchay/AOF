# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""SFT 微调数据生成器.

基于知识图谱和文档生成 instruction-response 对话对，用于 LLM 监督微调.

生成策略:
1. 实体问答: 基于实体属性生成 "这是什么？" 类的 instruction-response
2. 关系推理: 基于三元组生成 "A 和 B 有什么关系？" 类样本
3. 文档摘要: 基于文档 chunks 生成摘要/要点提取任务
4. 多轮对话: 基于子图路径模拟连贯的对话上下文
"""

from __future__ import annotations

import logging
import random
from typing import Any, AsyncIterator, Callable, Optional

from ..models import (
    GeneratorConfig,
    SFTSample,
    TrainingSample,
    SampleSource,
)
from ..loaders import GraphLoader, DocumentLoader
from .base import GeneratorBase

logger = logging.getLogger(__name__)


# 预设模板库
ENTITY_QA_TEMPLATES = [
    ("什么是{name}？", "{name}是{description}"),
    ("请解释{name}。", "{name}指的是{description}"),
    ("{name}的定义是什么？", "{name}的定义为：{description}"),
    ("介绍一下{name}。", "{name}是{description}"),
]

RELATION_QA_TEMPLATES = [
    ("{subject}和{object}之间有什么关系？", "{subject}通过'{predicate}'关系与{object}相关联。{extra}"),
    ("{subject}如何影响{object}？", "{subject}通过'{predicate}'作用于{object}。{extra}"),
    ("请描述{subject}和{object}的关联。", "{subject}与{object}之间存在'{predicate}'关系。{extra}"),
]

DOC_SUMMARY_TEMPLATES = [
    ("请总结以下内容：\n\n{content}", "总结如下：\n\n{summary}"),
    ("提取以下文本的要点：\n\n{content}", "要点提取：\n\n{summary}"),
    ("用一句话概括：\n\n{content}", "概括：{summary}"),
]

ATTRIBUTE_QA_TEMPLATES = [
    ("{entity}的{attr_name}是多少？", "{entity}的{attr_name}为{attr_value}。"),
    ("{entity}的{attr_name}是什么？", "{entity}的{attr_name}是{attr_value}。"),
]


class SFTGenerator(GeneratorBase):
    """SFT 微调数据生成器."""

    @property
    def sample_type(self) -> str:
        return "sft"

    async def estimate_yield(
        self,
        node_count: int,
        edge_count: int,
        document_count: int,
    ) -> int:
        """估算产出: 每个节点 ~2-4 条，每条边 ~1-2 条，每文档 ~1 条."""
        return min(
            node_count * 3 + edge_count * 1 + document_count * 1,
            10000,
        )

    async def generate(
        self,
        graph_loader: GraphLoader,
        doc_loader: DocumentLoader,
        config: GeneratorConfig,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> AsyncIterator[TrainingSample]:
        """生成 SFT 训练样本."""
        count = 0
        max_samples = config.max_samples

        # 加载数据
        graph_data = await graph_loader.load_all(node_limit=max_samples * 2)
        doc_data = await doc_loader.load(
            dataset_name=config.dataset_name or "",
            max_items=max_samples,
        )

        total_estimate = await self.estimate_yield(
            len(graph_data.nodes),
            len(graph_data.edges),
            len(doc_data.chunks),
        )
        total_estimate = min(total_estimate, max_samples)

        # 1. 基于实体属性生成问答对
        async for sample in self._generate_entity_qa(
            graph_data.nodes, config, graph_loader, max_samples - count
        ):
            if count >= max_samples:
                break
            count += 1
            self._report_progress(count, total_estimate, "entity QA", progress_callback)
            yield sample

        # 2. 基于关系三元组生成推理对
        async for sample in self._generate_relation_qa(
            graph_data.triples, config, max_samples - count
        ):
            if count >= max_samples:
                break
            count += 1
            self._report_progress(count, total_estimate, "relation QA", progress_callback)
            yield sample

        # 3. 基于文档 chunks 生成摘要任务
        async for sample in self._generate_doc_summary(
            doc_data.chunks, config, max_samples - count
        ):
            if count >= max_samples:
                break
            count += 1
            self._report_progress(count, total_estimate, "doc summary", progress_callback)
            yield sample

        # 4. 基于邻居子图生成多轮对话
        if config.enable_multi_turn and count < max_samples:
            async for sample in self._generate_multi_turn(
                graph_data.nodes, graph_loader, config, max_samples - count
            ):
                if count >= max_samples:
                    break
                count += 1
                self._report_progress(count, total_estimate, "multi-turn", progress_callback)
                yield sample

        logger.info(f"SFTGenerator produced {count} samples")

    async def _generate_entity_qa(
        self,
        nodes: list,
        config: GeneratorConfig,
        graph_loader: GraphLoader,
        remaining: int,
    ) -> AsyncIterator[SFTSample]:
        """基于实体属性生成问答对."""
        random.seed(42)
        shuffled = nodes.copy()
        random.shuffle(shuffled)

        for node in shuffled[:remaining]:
            name = self._get_entity_name(node)
            description = self._get_entity_description(node)

            if not name or not description or len(description) < 10:
                continue

            # 选择模板
            tmpl_idx = random.randint(0, len(ENTITY_QA_TEMPLATES) - 1)
            q_tmpl, a_tmpl = ENTITY_QA_TEMPLATES[tmpl_idx]

            question = q_tmpl.format(name=name)
            answer = a_tmpl.format(name=name, description=description)

            messages = [
                {"role": "system", "content": config.system_prompt_template or "你是一个企业知识助手，基于知识图谱回答用户问题。"},
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ]

            # 尝试生成属性问答
            for attr_name, attr_value in self._extract_key_attributes(node):
                if remaining <= 0:
                    break
                if isinstance(attr_value, (str, int, float)) and len(str(attr_value)) > 0:
                    attr_q_tmpl, attr_a_tmpl = random.choice(ATTRIBUTE_QA_TEMPLATES)
                    attr_question = attr_q_tmpl.format(entity=name, attr_name=attr_name)
                    attr_answer = attr_a_tmpl.format(entity=name, attr_name=attr_name, attr_value=attr_value)

                    yield SFTSample(
                        messages=[
                            {"role": "system", "content": config.system_prompt_template or "你是一个企业知识助手，基于知识图谱回答用户问题。"},
                            {"role": "user", "content": attr_question},
                            {"role": "assistant", "content": attr_answer},
                        ],
                        source=SampleSource(
                            dataset_name="",
                            source_type="node",
                            source_id=str(node.id),
                        ),
                    )

            yield SFTSample(
                messages=messages,
                source=SampleSource(
                    dataset_name="",
                    source_type="node",
                    source_id=str(node.id),
                ),
            )

    async def _generate_relation_qa(
        self,
        triples: list,
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[SFTSample]:
        """基于关系三元组生成推理对."""
        random.seed(43)
        shuffled = triples.copy()
        random.shuffle(shuffled)

        for triple in shuffled[:remaining]:
            subject_name = self._get_entity_name(triple.subject)
            object_name = self._get_entity_name(triple.object)
            predicate = triple.predicate

            if not subject_name or not object_name or not predicate:
                continue

            # 构建 extra 信息
            extra_props = ""
            if triple.edge_properties:
                props = [f"{k}={v}" for k, v in triple.edge_properties.items() if k not in ("id",)]
                if props:
                    extra_props = f"相关属性：{', '.join(props)}。"

            tmpl_idx = random.randint(0, len(RELATION_QA_TEMPLATES) - 1)
            q_tmpl, a_tmpl = RELATION_QA_TEMPLATES[tmpl_idx]

            question = q_tmpl.format(subject=subject_name, object=object_name)
            answer = a_tmpl.format(
                subject=subject_name,
                object=object_name,
                predicate=predicate,
                extra=extra_props,
            )

            yield SFTSample(
                messages=[
                    {"role": "system", "content": config.system_prompt_template or "你是一个企业知识助手，基于知识图谱回答用户问题。"},
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ],
                source=SampleSource(
                    dataset_name="",
                    source_type="triple",
                    source_id=f"{triple.subject.id}-{predicate}-{triple.object.id}",
                ),
            )

    async def _generate_doc_summary(
        self,
        chunks: list[str],
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[SFTSample]:
        """基于文档 chunks 生成摘要任务."""
        random.seed(44)
        shuffled = chunks.copy()
        random.shuffle(shuffled)

        for idx, chunk in enumerate(shuffled[:remaining]):
            if len(chunk) < 100:
                continue

            # 生成伪摘要（取前几句 + 关键信息）
            sentences = chunk.split("。")
            summary_sentences = sentences[:min(3, len(sentences))]
            summary = "。".join(s.strip() for s in summary_sentences if s.strip()) + "。"

            tmpl_idx = random.randint(0, len(DOC_SUMMARY_TEMPLATES) - 1)
            q_tmpl, a_tmpl = DOC_SUMMARY_TEMPLATES[tmpl_idx]

            # 截断过长的内容
            content = chunk[:800] + "..." if len(chunk) > 800 else chunk

            question = q_tmpl.format(content=content)
            answer = a_tmpl.format(content=content, summary=summary)

            yield SFTSample(
                messages=[
                    {"role": "system", "content": config.system_prompt_template or "你是一个文档分析助手，帮助用户理解和总结文档内容。"},
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ],
                source=SampleSource(
                    dataset_name="",
                    source_type="document",
                    source_id=f"chunk_{idx}",
                ),
            )

    async def _generate_multi_turn(
        self,
        nodes: list,
        graph_loader: GraphLoader,
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[SFTSample]:
        """基于邻居子图生成多轮对话."""
        random.seed(45)
        shuffled = nodes.copy()
        random.shuffle(shuffled)

        max_turns = config.max_turns
        processed = 0

        for node in shuffled:
            if processed >= remaining:
                break

            name = self._get_entity_name(node)
            if not name:
                continue

            # 加载邻居
            try:
                neighbors, edges = await graph_loader.load_neighbors(
                    str(node.id), depth=1, limit=10
                )
            except Exception:
                continue

            if len(neighbors) < 2:
                continue

            messages: list[dict[str, str]] = [
                {"role": "system", "content": config.system_prompt_template or "你是一个企业知识助手，基于知识图谱回答用户问题。"},
            ]

            # 第一轮：介绍实体
            desc = self._get_entity_description(node)
            messages.append({"role": "user", "content": f"介绍一下{name}。"})
            messages.append({"role": "assistant", "content": f"{name}是{desc}" if desc else f"{name}是一个业务实体。"})

            # 后续轮次：询问邻居关系
            for i, (neighbor, edge) in enumerate(zip(neighbors[:max_turns - 1], edges[:max_turns - 1])):
                if i + 2 >= max_turns * 2:
                    break
                neighbor_name = self._get_entity_name(neighbor)
                if not neighbor_name:
                    continue

                relation = edge.relation_type if hasattr(edge, "relation_type") else "相关"
                messages.append({
                    "role": "user",
                    "content": f"{name}和{neighbor_name}有什么关系？"
                })
                messages.append({
                    "role": "assistant",
                    "content": f"{name}通过'{relation}'与{neighbor_name}相关联。"
                })

            if len(messages) >= 5:  # 至少 2 轮对话
                yield SFTSample(
                    messages=messages,
                    source=SampleSource(
                        dataset_name="",
                        source_type="path",
                        source_id=str(node.id),
                    ),
                )
                processed += 1

    @staticmethod
    def _get_entity_name(node) -> str:
        """从节点中提取名称."""
        props = node.properties if hasattr(node, "properties") else {}
        return (
            props.get("name")
            or props.get("title")
            or props.get("label")
            or str(getattr(node, "id", ""))
        )

    @staticmethod
    def _get_entity_description(node) -> str:
        """从节点中提取描述."""
        props = node.properties if hasattr(node, "properties") else {}
        return (
            props.get("description")
            or props.get("summary")
            or props.get("definition")
            or ""
        )

    @staticmethod
    def _extract_key_attributes(node) -> list[tuple[str, Any]]:
        """提取节点的关键属性（排除 id/name/description 等元字段）."""
        props = node.properties if hasattr(node, "properties") else {}
        skip_keys = {"id", "name", "title", "label", "description", "summary", "definition", "type"}
        return [
            (k, v) for k, v in props.items()
            if k not in skip_keys and isinstance(v, (str, int, float))
        ][:5]  # 最多 5 个属性
