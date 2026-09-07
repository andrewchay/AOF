# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""训练数据生成器 - 质量过滤与去重.

提供训练样本的质量控制，包括去重、长度过滤、多样性评分等.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import TrainingSample, SFTSample, RAGEvalSample, AgentToolSample, QualityConfig

logger = logging.getLogger(__name__)


@dataclass
class QualityReport:
    """质量检查报告."""
    total_input: int = 0
    passed: int = 0
    filtered: int = 0
    filter_reasons: dict[str, int] = field(default_factory=dict)
    duplicate_count: int = 0
    diversity_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_input": self.total_input,
            "passed": self.passed,
            "filtered": self.filtered,
            "filter_reasons": self.filter_reasons,
            "duplicate_count": self.duplicate_count,
            "diversity_score": self.diversity_score,
        }


class QualityFilter:
    """训练数据质量过滤器.

    对生成的训练样本进行多维度质量检查与过滤.

    使用示例:
        config = QualityConfig(
            enable_deduplication=True,
            min_question_length=5,
            max_question_length=500,
        )
        filter = QualityFilter(config)
        samples = [...]  # 生成的样本
        clean_samples, report = filter.filter(samples)
    """

    def __init__(self, config: Optional[QualityConfig] = None):
        self.config = config or QualityConfig()
        self._seen_hashes: set[str] = set()

    def filter(
        self,
        samples: list[TrainingSample],
    ) -> tuple[list[TrainingSample], QualityReport]:
        """过滤样本列表.

        Args:
            samples: 原始样本列表

        Returns:
            (通过过滤的样本列表, 质量报告)
        """
        report = QualityReport(total_input=len(samples))
        passed: list[TrainingSample] = []

        for sample in samples:
            reason = self._check_sample(sample)
            if reason:
                report.filtered += 1
                report.filter_reasons[reason] = report.filter_reasons.get(reason, 0) + 1
                continue

            # 去重检查
            if self.config.enable_deduplication:
                h = self._compute_hash(sample)
                if h in self._seen_hashes:
                    report.duplicate_count += 1
                    report.filtered += 1
                    report.filter_reasons["duplicate"] = (
                        report.filter_reasons.get("duplicate", 0) + 1
                    )
                    continue
                self._seen_hashes.add(h)

            passed.append(sample)
            report.passed += 1

        # 计算多样性评分
        report.diversity_score = self._compute_diversity(passed)

        logger.info(
            f"Quality filter: {report.total_input} in -> {report.passed} passed "
            f"({report.filtered} filtered, {report.duplicate_count} duplicates)"
        )
        return passed, report

    def _check_sample(self, sample: TrainingSample) -> Optional[str]:
        """检查单个样本，返回过滤原因或 None.

        Args:
            sample: 待检查的样本

        Returns:
            过滤原因字符串，如果通过则返回 None
        """
        # SFT 样本检查
        if isinstance(sample, SFTSample):
            return self._check_sft(sample)

        # RAG 评估样本检查
        if isinstance(sample, RAGEvalSample):
            return self._check_rag_eval(sample)

        # Agent 工具调用样本检查
        if isinstance(sample, AgentToolSample):
            return self._check_agent_tool(sample)

        return None

    def _check_sft(self, sample: SFTSample) -> Optional[str]:
        """检查 SFT 样本."""
        if not sample.messages:
            return "empty_messages"

        # 检查 user message 长度
        user_contents = [
            m.get("content", "") for m in sample.messages if m.get("role") == "user"
        ]
        if not user_contents:
            return "no_user_message"

        for content in user_contents:
            length = len(content.strip())
            if length < self.config.min_question_length:
                return "question_too_short"
            if length > self.config.max_question_length:
                return "question_too_long"

        # 检查 assistant message 长度
        assistant_contents = [
            m.get("content", "") for m in sample.messages if m.get("role") == "assistant"
        ]
        for content in assistant_contents:
            length = len(content.strip())
            if length < self.config.min_answer_length:
                return "answer_too_short"
            if length > self.config.max_answer_length:
                return "answer_too_long"

        return None

    def _check_rag_eval(self, sample: RAGEvalSample) -> Optional[str]:
        """检查 RAG 评估样本."""
        q_len = len(sample.question.strip())
        if q_len < self.config.min_question_length:
            return "question_too_short"
        if q_len > self.config.max_question_length:
            return "question_too_long"

        a_len = len(sample.answer.strip())
        if a_len < self.config.min_answer_length:
            return "answer_too_short"
        if a_len > self.config.max_answer_length:
            return "answer_too_long"

        if not sample.contexts:
            return "no_contexts"

        # 检查上下文总长度
        total_context_len = sum(len(c) for c in sample.contexts)
        if total_context_len < 10:
            return "contexts_too_short"

        return None

    def _check_agent_tool(self, sample: AgentToolSample) -> Optional[str]:
        """检查 Agent 工具调用样本."""
        if not sample.messages:
            return "empty_messages"

        if not sample.tools:
            return "no_tools"

        if not sample.tool_calls:
            return "no_tool_calls"

        # 验证 tool_calls 引用的工具是否存在
        tool_names = {t.get("function", {}).get("name", "") for t in sample.tools}
        for tc in sample.tool_calls:
            tc_name = tc.get("function", {}).get("name", "")
            if tc_name and tc_name not in tool_names:
                return "tool_call_mismatch"

        return None

    @staticmethod
    def _compute_hash(sample: TrainingSample) -> str:
        """计算样本内容哈希用于去重.

        不同类型的样本使用不同的去重键：
        - SFT: user message 内容
        - RAG Eval: question 内容
        - Agent Tool: user message + 工具名组合
        """
        content = ""

        if isinstance(sample, RAGEvalSample):
            content = sample.question.strip().lower()
        elif isinstance(sample, SFTSample):
            for msg in reversed(sample.messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "").strip().lower()
                    break
        elif isinstance(sample, AgentToolSample):
            user_msg = ""
            for msg in sample.messages:
                if msg.get("role") == "user":
                    user_msg = msg.get("content", "").strip().lower()
                    break
            tool_names = sorted(
                t.get("function", {}).get("name", "") for t in sample.tools
            )
            content = f"{user_msg}|{','.join(tool_names)}"

        return hashlib.blake2b(content.encode("utf-8"), digest_size=16).hexdigest()

    def _compute_diversity(self, samples: list[TrainingSample]) -> float:
        """计算样本集合的多样性评分.

        基于简单的 n-gram 重叠率估算。实际生产环境可替换为嵌入向量相似度.

        Returns:
            0.0 ~ 1.0 的多样性评分，越高表示多样性越好
        """
        if len(samples) < 2:
            return 1.0

        texts: list[str] = []
        for s in samples:
            if isinstance(s, RAGEvalSample):
                texts.append(s.question)
            elif isinstance(s, SFTSample):
                for msg in reversed(s.messages):
                    if msg.get("role") == "user":
                        texts.append(msg.get("content", ""))
                        break
            elif isinstance(s, AgentToolSample):
                for msg in s.messages:
                    if msg.get("role") == "user":
                        texts.append(msg.get("content", ""))
                        break

        if len(texts) < 2:
            return 1.0

        # 计算两两之间的字符级 Jaccard 相似度，取平均值
        similarities = []
        sample_size = min(len(texts), 100)  # 采样避免 O(n^2)
        import random

        random.seed(42)
        sampled = random.sample(texts, sample_size)

        for i in range(sample_size):
            for j in range(i + 1, sample_size):
                sim = self._jaccard_similarity(sampled[i], sampled[j])
                similarities.append(sim)

        if not similarities:
            return 1.0

        avg_sim = sum(similarities) / len(similarities)
        diversity = 1.0 - avg_sim
        return round(diversity, 3)

    @staticmethod
    def _jaccard_similarity(a: str, b: str, n: int = 3) -> float:
        """计算两个字符串的 n-gram Jaccard 相似度."""
        def get_ngrams(text: str, n: int) -> set[str]:
            text = text.lower().strip()
            return set(text[i : i + n] for i in range(len(text) - n + 1)) if len(text) >= n else set()

        set_a = get_ngrams(a, n)
        set_b = get_ngrams(b, n)
        if not set_a and not set_b:
            return 1.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    def reset(self) -> None:
        """重置去重状态，允许重新过滤新批次."""
        self._seen_hashes.clear()
