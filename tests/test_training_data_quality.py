"""训练数据生成器 - 质量过滤单元测试."""

from __future__ import annotations


from bridge.training_data.models import (
    SFTSample,
    RAGEvalSample,
    AgentToolSample,
    QualityConfig,
)
from bridge.training_data.quality import QualityFilter, QualityReport


class TestQualityFilter:
    """测试 QualityFilter."""

    def test_pass_valid_sft_sample(self):
        config = QualityConfig()
        qf = QualityFilter(config)

        sample = SFTSample(
            messages=[
                {"role": "user", "content": "What is this?"},
                {"role": "assistant", "content": "This is a test answer with enough length."},
            ],
        )
        passed, report = qf.filter([sample])
        assert len(passed) == 1
        assert report.passed == 1
        assert report.filtered == 0

    def test_filter_empty_messages(self):
        config = QualityConfig()
        qf = QualityFilter(config)

        sample = SFTSample(messages=[])
        passed, report = qf.filter([sample])
        assert len(passed) == 0
        assert report.filter_reasons.get("empty_messages") == 1

    def test_filter_question_too_short(self):
        config = QualityConfig(min_question_length=10)
        qf = QualityFilter(config)

        sample = SFTSample(
            messages=[
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello there, how can I help you today?"},
            ],
        )
        passed, report = qf.filter([sample])
        assert len(passed) == 0
        assert report.filter_reasons.get("question_too_short") == 1

    def test_filter_answer_too_short(self):
        config = QualityConfig(min_answer_length=50)
        qf = QualityFilter(config)

        sample = SFTSample(
            messages=[
                {"role": "user", "content": "What is the meaning of life?"},
                {"role": "assistant", "content": "42"},
            ],
        )
        passed, report = qf.filter([sample])
        assert len(passed) == 0
        assert report.filter_reasons.get("answer_too_short") == 1

    def test_deduplication(self):
        config = QualityConfig(enable_deduplication=True)
        qf = QualityFilter(config)

        samples = [
            SFTSample(
                messages=[
                    {"role": "user", "content": "What is X?"},
                    {"role": "assistant", "content": "X is Y and it has many interesting properties."},
                ],
            ),
            SFTSample(
                messages=[
                    {"role": "user", "content": "What is X?"},  # 重复
                    {"role": "assistant", "content": "X is Z and something else entirely."},
                ],
            ),
        ]
        passed, report = qf.filter(samples)
        assert len(passed) == 1
        assert report.duplicate_count == 1

    def test_rag_eval_no_contexts(self):
        config = QualityConfig()
        qf = QualityFilter(config)

        sample = RAGEvalSample(
            question="What is X in the context of this document?",
            answer="X is Y and it represents an important concept in this domain.",
            contexts=[],  # 空上下文
        )
        passed, report = qf.filter([sample])
        assert len(passed) == 0
        assert report.filter_reasons.get("no_contexts") == 1

    def test_agent_tool_no_tools(self):
        config = QualityConfig()
        qf = QualityFilter(config)

        sample = AgentToolSample(
            messages=[{"role": "user", "content": "Do it"}],
            tools=[],  # 空工具
            tool_calls=[],
        )
        passed, report = qf.filter([sample])
        assert len(passed) == 0
        assert report.filter_reasons.get("no_tools") == 1

    def test_agent_tool_mismatch(self):
        config = QualityConfig()
        qf = QualityFilter(config)

        sample = AgentToolSample(
            messages=[{"role": "user", "content": "Do it"}],
            tools=[{"type": "function", "function": {"name": "tool_a"}}],
            tool_calls=[{"function": {"name": "tool_b"}}],  # 不匹配
        )
        passed, report = qf.filter([sample])
        assert len(passed) == 0
        assert report.filter_reasons.get("tool_call_mismatch") == 1

    def test_diversity_score(self):
        config = QualityConfig()
        qf = QualityFilter(config)

        # 创建差异较大的样本
        topics = [
            "量子计算的基本原理是什么",
            "如何制作意大利面",
            "19世纪法国文学的主要特征",
            "深海生物如何在高压环境下生存",
            "区块链技术的共识机制有哪些",
        ]
        samples = [
            SFTSample(messages=[{"role": "user", "content": topic}])
            for topic in topics
        ]
        passed, report = qf.filter(samples)
        assert report.diversity_score > 0.3  # 差异大的样本多样性应该较高

    def test_reset(self):
        config = QualityConfig(enable_deduplication=True)
        qf = QualityFilter(config)

        sample = SFTSample(
            messages=[{"role": "user", "content": "Same question"}],
        )
        qf.filter([sample])

        # 重置后应该可以再次通过
        qf.reset()
        passed, _ = qf.filter([sample])
        assert len(passed) == 1


class TestQualityReport:
    """测试 QualityReport."""

    def test_to_dict(self):
        report = QualityReport(
            total_input=100,
            passed=80,
            filtered=20,
            filter_reasons={"too_short": 10, "duplicate": 10},
            duplicate_count=10,
            diversity_score=0.75,
        )
        d = report.to_dict()
        assert d["total_input"] == 100
        assert d["passed"] == 80
        assert d["diversity_score"] == 0.75
