# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""RAG 评估数据生成器.

基于知识图谱生成 question-answer-context 三元组，用于评估 RAG 系统效果.

生成策略:
1. 事实型: 基于单个实体/属性生成可直接回答的问题
2. 关系型: 基于边生成需要连接两个实体的问题
3. 聚合型: 基于同类节点生成需要汇总的问题
4. 推理型: 基于路径生成需要多跳推理的问题
"""

from __future__ import annotations

import logging
import random
from typing import Any, AsyncIterator, Callable, Optional

from ..models import (
    GeneratorConfig,
    RAGEvalSample,
    TrainingSample,
    SampleSource,
    QueryType,
    Difficulty,
)
from ..loaders import GraphLoader, DocumentLoader
from .base import GeneratorBase

logger = logging.getLogger(__name__)


# 问题模板
FACTUAL_TEMPLATES = [
    ("{entity}是什么？", "{entity}是{description}"),
    ("{entity}的{attr}是什么？", "{entity}的{attr}为{value}"),
    ("什么是{entity}？", "{entity}指的是{description}"),
    ("{entity}的定义是什么？", "{entity}的定义为{description}"),
]

RELATIONAL_TEMPLATES = [
    ("{subject}和{object}之间有什么关系？", "{subject}与{object}通过'{relation}'相关联"),
    ("{subject}如何影响{object}？", "{subject}通过'{relation}'作用于{object}"),
    ("{object}和{subject}有什么联系？", "{object}通过'{relation}'与{subject}相关联"),
]

AGGREGATIONAL_TEMPLATES = [
    ("列举所有{type}。", "{type}包括：{list}"),
    ("有哪些{type}？", "{type}有：{list}"),
    ("请列出{type}。", "以下是{type}：{list}"),
]

REASONING_TEMPLATES = [
    ("{start}通过什么路径可以到达{end}？", "{start}通过以下路径到达{end}：{path_desc}"),
    ("{start}如何间接影响{end}？", "{start}通过{path_desc}间接影响{end}"),
    ("从{start}到{end}需要经过哪些步骤？", "从{start}到{end}的路径为：{path_desc}"),
]


class RAGEvalGenerator(GeneratorBase):
    """RAG 评估数据生成器."""

    @property
    def sample_type(self) -> str:
        return "rag_eval"

    async def estimate_yield(
        self,
        node_count: int,
        edge_count: int,
        document_count: int,
    ) -> int:
        """估算产出: 节点 ~1 条事实型 + 边 ~1 条关系型 + 路径 ~少量推理型."""
        return min(
            node_count * 1 + edge_count * 1 + min(node_count // 10, 100),
            10000,
        )

    async def generate(
        self,
        graph_loader: GraphLoader,
        doc_loader: DocumentLoader,
        config: GeneratorConfig,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> AsyncIterator[TrainingSample]:
        """生成 RAG 评估样本."""
        count = 0
        max_samples = config.max_samples

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

        # 1. 事实型问题
        async for sample in self._generate_factual(
            graph_data.nodes, doc_data.chunks, config, max_samples - count
        ):
            if count >= max_samples:
                break
            count += 1
            self._report_progress(count, total_estimate, "factual", progress_callback)
            yield sample

        # 2. 关系型问题
        async for sample in self._generate_relational(
            graph_data.triples, config, max_samples - count
        ):
            if count >= max_samples:
                break
            count += 1
            self._report_progress(count, total_estimate, "relational", progress_callback)
            yield sample

        # 3. 聚合型问题
        async for sample in self._generate_aggregational(
            graph_data.nodes, config, max_samples - count
        ):
            if count >= max_samples:
                break
            count += 1
            self._report_progress(count, total_estimate, "aggregational", progress_callback)
            yield sample

        # 4. 推理型问题（如果还有余量）
        if count < max_samples:
            async for sample in self._generate_reasoning(
                graph_data.nodes, graph_loader, config, max_samples - count
            ):
                if count >= max_samples:
                    break
                count += 1
                self._report_progress(count, total_estimate, "reasoning", progress_callback)
                yield sample

        logger.info(f"RAGEvalGenerator produced {count} samples")

    async def _generate_factual(
        self,
        nodes: list,
        doc_chunks: list[str],
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[RAGEvalSample]:
        """生成事实型问题."""
        random.seed(50)
        generated = 0

        # 基于节点属性
        shuffled_nodes = nodes.copy()
        random.shuffle(shuffled_nodes)

        for node in shuffled_nodes:
            if generated >= remaining:
                break

            name = self._get_name(node)
            description = self._get_description(node)
            if not name:
                continue

            # 实体描述型问题
            if description and len(description) > 10:
                tmpl = random.choice(FACTUAL_TEMPLATES)
                question = tmpl[0].format(entity=name, attr="", value="")
                answer = tmpl[1].format(entity=name, description=description, attr="", value="")

                contexts = [description]
                if doc_chunks:
                    # 找最相关的 doc chunk（简单匹配）
                    related = self._find_related_chunk(name, doc_chunks)
                    if related:
                        contexts.append(related)

                yield RAGEvalSample(
                    question=question,
                    answer=answer,
                    contexts=contexts[: config.max_contexts_per_question],
                    difficulty=Difficulty.EASY,
                    query_type=QueryType.FACTUAL,
                    ground_truth_sources=[f"node://{getattr(node, 'id', '')}"],
                    source=SampleSource(
                        dataset_name="",
                        source_type="node",
                        source_id=str(getattr(node, "id", "")),
                    ),
                )
                generated += 1

            # 属性型问题
            for attr_name, attr_value in self._get_attributes(node):
                if generated >= remaining:
                    break
                if not attr_value:
                    continue

                question = f"{name}的{attr_name}是什么？"
                answer = f"{name}的{attr_name}为{attr_value}。"

                yield RAGEvalSample(
                    question=question,
                    answer=answer,
                    contexts=[description] if description else [],
                    difficulty=Difficulty.EASY,
                    query_type=QueryType.FACTUAL,
                    ground_truth_sources=[f"node://{getattr(node, 'id', '')}"],
                    source=SampleSource(
                        dataset_name="",
                        source_type="node",
                        source_id=str(getattr(node, "id", "")),
                    ),
                )
                generated += 1

        # 基于文档 chunks 生成事实型问题
        for chunk in doc_chunks[: max(0, remaining - generated)]:
            if generated >= remaining:
                break
            if len(chunk) < 100:
                continue

            # 从 chunk 中提取一个"关键信息"作为问题
            sentences = chunk.split("。")
            if len(sentences) < 2:
                continue

            # 取第一句作为上下文，第二句作为答案
            context = sentences[0].strip() + "。"
            answer_sent = sentences[1].strip() + "。"

            # 生成问题：提取关键词
            words = context.split()
            if len(words) >= 3:
                keyword = words[0] if len(words[0]) > 2 else "".join(words[:2])
                question = f"关于{keyword}，文档提到了什么？"

                yield RAGEvalSample(
                    question=question,
                    answer=answer_sent,
                    contexts=[context],
                    difficulty=Difficulty.EASY,
                    query_type=QueryType.FACTUAL,
                    ground_truth_sources=["document://chunk"],
                    source=SampleSource(
                        dataset_name="",
                        source_type="document",
                    ),
                )
                generated += 1

    async def _generate_relational(
        self,
        triples: list,
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[RAGEvalSample]:
        """生成关系型问题."""
        random.seed(51)
        shuffled = triples.copy()
        random.shuffle(shuffled)

        for triple in shuffled[:remaining]:
            subject_name = self._get_name(triple.subject)
            object_name = self._get_name(triple.object)
            predicate = triple.predicate

            if not subject_name or not object_name or not predicate:
                continue

            tmpl = random.choice(RELATIONAL_TEMPLATES)
            question = tmpl[0].format(subject=subject_name, object=object_name)
            answer = tmpl[1].format(
                subject=subject_name,
                object=object_name,
                relation=predicate,
            )

            # 构建上下文：包含两个实体的描述
            contexts = []
            subj_desc = self._get_description(triple.subject)
            obj_desc = self._get_description(triple.object)
            if subj_desc:
                contexts.append(f"{subject_name}：{subj_desc}")
            if obj_desc:
                contexts.append(f"{object_name}：{obj_desc}")

            # 难度评估
            difficulty = self._assess_difficulty(
                subject_name, object_name, predicate, contexts
            )

            yield RAGEvalSample(
                question=question,
                answer=answer,
                contexts=contexts[: config.max_contexts_per_question],
                difficulty=difficulty,
                query_type=QueryType.RELATIONAL,
                ground_truth_sources=[
                    f"edge://{getattr(triple.subject, 'id', '')}-{predicate}-{getattr(triple.object, 'id', '')}"
                ],
                source=SampleSource(
                    dataset_name="",
                    source_type="edge",
                    source_id=f"{getattr(triple.subject, 'id', '')}-{predicate}-{getattr(triple.object, 'id', '')}",
                ),
            )

    async def _generate_aggregational(
        self,
        nodes: list,
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[RAGEvalSample]:
        """生成聚合型问题."""
        # 按标签分组
        from collections import defaultdict
        label_groups: defaultdict[str, list] = defaultdict(list)
        for node in nodes:
            labels = getattr(node, "labels", [])
            if not labels:
                labels = ["entity"]
            for label in labels:
                label_groups[label].append(node)

        generated = 0
        random.seed(52)

        for label, group in label_groups.items():
            if generated >= remaining:
                break
            if len(group) < 2:
                continue

            # 随机选取组内节点
            selected = random.sample(group, min(5, len(group)))
            names = [self._get_name(n) for n in selected if self._get_name(n)]
            if len(names) < 2:
                continue

            type_label = label.capitalize()
            tmpl = random.choice(AGGREGATIONAL_TEMPLATES)
            question = tmpl[0].format(type=type_label, list="")
            answer = tmpl[1].format(type=type_label, list="、".join(names))

            # 上下文：各节点的描述
            contexts = []
            for node in selected:
                desc = self._get_description(node)
                name = self._get_name(node)
                if name and desc:
                    contexts.append(f"{name}：{desc}")

            yield RAGEvalSample(
                question=question,
                answer=answer,
                contexts=contexts[: config.max_contexts_per_question],
                difficulty=Difficulty.MEDIUM,
                query_type=QueryType.AGGREGATIONAL,
                ground_truth_sources=[f"nodes://{label}"],
                source=SampleSource(
                    dataset_name="",
                    source_type="node_group",
                ),
            )
            generated += 1

    async def _generate_reasoning(
        self,
        nodes: list,
        graph_loader: GraphLoader,
        config: GeneratorConfig,
        remaining: int,
    ) -> AsyncIterator[RAGEvalSample]:
        """生成推理型问题（需要多跳推理）."""
        random.seed(53)
        shuffled = nodes.copy()
        random.shuffle(shuffled)

        generated = 0
        for start_node in shuffled[: min(len(shuffled), remaining * 3)]:
            if generated >= remaining:
                break

            start_name = self._get_name(start_node)
            if not start_name:
                continue

            # 尝试找 2-3 跳的邻居
            try:
                neighbors, edges = await graph_loader.load_neighbors(
                    str(getattr(start_node, "id", "")), depth=2, limit=20
                )
            except Exception:
                continue

            if len(neighbors) < 2 or len(edges) < 2:
                continue

            # 找一个 2-hop 的终点
            for end_node in neighbors[2:]:  # 跳过直接邻居
                if generated >= remaining:
                    break

                end_name = self._get_name(end_node)
                if not end_name or end_name == start_name:
                    continue

                # 尝试获取路径
                try:
                    paths = await graph_loader.load_paths(
                        str(getattr(start_node, "id", "")),
                        str(getattr(end_node, "id", "")),
                        max_length=3,
                    )
                except Exception:
                    continue

                if not paths:
                    continue

                # 描述路径
                path_desc = f"从{start_name}出发"
                for edge in edges[:3]:
                    rel = getattr(edge, "relation_type", "关联")
                    path_desc += f"，通过'{rel}'关系"
                path_desc += f"，最终到达{end_name}。"

                tmpl = random.choice(REASONING_TEMPLATES)
                question = tmpl[0].format(start=start_name, end=end_name, path_desc="")
                answer = tmpl[1].format(start=start_name, end=end_name, path_desc=path_desc)

                # 上下文：路径上各节点的描述
                contexts = []
                for node in [start_node, end_node]:
                    desc = self._get_description(node)
                    name = self._get_name(node)
                    if name and desc:
                        contexts.append(f"{name}：{desc}")

                yield RAGEvalSample(
                    question=question,
                    answer=answer,
                    contexts=contexts[: config.max_contexts_per_question],
                    difficulty=Difficulty.HARD,
                    query_type=QueryType.REASONING,
                    ground_truth_sources=[
                        f"path://{getattr(start_node, 'id', '')}->{getattr(end_node, 'id', '')}"
                    ],
                    source=SampleSource(
                        dataset_name="",
                        source_type="path",
                    ),
                )
                generated += 1
                break  # 每个起点只生成一个问题

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
    def _get_description(node) -> str:
        """获取节点描述."""
        props = getattr(node, "properties", {}) or {}
        return (
            props.get("description")
            or props.get("summary")
            or ""
        )

    @staticmethod
    def _get_attributes(node) -> list[tuple[str, Any]]:
        """获取节点属性."""
        props = getattr(node, "properties", {}) or {}
        skip = {"id", "name", "title", "label", "description", "summary", "type"}
        return [(k, v) for k, v in props.items() if k not in skip][:3]

    @staticmethod
    def _find_related_chunk(keyword: str, chunks: list[str]) -> str:
        """找到包含关键词的文档 chunk."""
        for chunk in chunks:
            if keyword in chunk:
                return chunk[:500]
        return ""

    @staticmethod
    def _assess_difficulty(
        subject: str,
        object_: str,
        relation: str,
        contexts: list[str],
    ) -> Difficulty:
        """评估问题难度."""
        # 简单启发式规则
        total_context_len = sum(len(c) for c in contexts)
        if total_context_len > 1000:
            return Difficulty.HARD
        if len(contexts) > 1:
            return Difficulty.MEDIUM
        return Difficulty.EASY
