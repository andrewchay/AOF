"""训练数据生成器 - 模型单元测试."""

from __future__ import annotations

import json

from bridge.training_data.models import (
    SampleSource,
    TrainingSample,
    SFTSample,
    RAGEvalSample,
    AgentToolSample,
    SampleType,
    QueryType,
    Difficulty,
    PipelineResult,
    GeneratorConfig,
    QualityConfig,
)


class TestSampleSource:
    """测试 SampleSource."""

    def test_creation(self):
        source = SampleSource(
            dataset_name="test_dataset",
            source_type="node",
            source_id="node_123",
        )
        assert source.dataset_name == "test_dataset"
        assert source.source_type == "node"
        assert source.source_id == "node_123"

    def test_to_dict(self):
        source = SampleSource(
            dataset_name="ds",
            source_type="edge",
            source_id="e1",
            source_uri="http://test",
            chunk_index=0,
        )
        d = source.to_dict()
        assert d["dataset_name"] == "ds"
        assert d["source_type"] == "edge"
        assert d["source_uri"] == "http://test"
        assert d["chunk_index"] == 0


class TestTrainingSample:
    """测试 TrainingSample 基类."""

    def test_default_creation(self):
        sample = TrainingSample()
        assert sample.id is not None
        assert sample.sample_type == SampleType.SFT
        assert sample.created_at is not None

    def test_to_dict(self):
        sample = TrainingSample(
            sample_type=SampleType.RAG_EVAL,
            source=SampleSource("ds", "node", "n1"),
            metadata={"key": "value"},
        )
        d = sample.to_dict()
        assert d["sample_type"] == "rag_eval"
        assert d["source"]["source_type"] == "node"
        assert d["metadata"]["key"] == "value"

    def test_to_jsonl(self):
        sample = TrainingSample(
            source=SampleSource("ds", "document"),
        )
        line = sample.to_jsonl()
        # 验证是有效的 JSON
        parsed = json.loads(line)
        assert parsed["sample_type"] == "sft"


class TestSFTSample:
    """测试 SFTSample."""

    def test_creation(self):
        sample = SFTSample(
            messages=[
                {"role": "system", "content": "You are a helper."},
                {"role": "user", "content": "Hello?"},
                {"role": "assistant", "content": "Hi!"},
            ],
            system_prompt="You are a helper.",
        )
        assert sample.sample_type == SampleType.SFT
        assert len(sample.messages) == 3
        assert sample.messages[0]["role"] == "system"

    def test_to_dict(self):
        sample = SFTSample(
            messages=[{"role": "user", "content": "Q"}, {"role": "assistant", "content": "A"}],
            system_prompt="Test prompt",
        )
        d = sample.to_dict()
        assert d["sample_type"] == "sft"
        assert d["system_prompt"] == "Test prompt"
        assert len(d["messages"]) == 2

    def test_to_jsonl_roundtrip(self):
        sample = SFTSample(
            messages=[{"role": "user", "content": "测试"}],
        )
        line = sample.to_jsonl()
        parsed = json.loads(line)
        assert parsed["messages"][0]["content"] == "测试"


class TestRAGEvalSample:
    """测试 RAGEvalSample."""

    def test_creation(self):
        sample = RAGEvalSample(
            question="What is X?",
            answer="X is Y.",
            contexts=["Context 1", "Context 2"],
            difficulty=Difficulty.HARD,
            query_type=QueryType.REASONING,
            ground_truth_sources=["node://1"],
        )
        assert sample.sample_type == SampleType.RAG_EVAL
        assert sample.difficulty == Difficulty.HARD
        assert sample.query_type == QueryType.REASONING

    def test_to_dict(self):
        sample = RAGEvalSample(
            question="Q1",
            answer="A1",
            contexts=["C1"],
        )
        d = sample.to_dict()
        assert d["question"] == "Q1"
        assert d["answer"] == "A1"
        assert d["difficulty"] == "medium"
        assert d["query_type"] == "factual"


class TestAgentToolSample:
    """测试 AgentToolSample."""

    def test_creation(self):
        sample = AgentToolSample(
            messages=[{"role": "user", "content": "Do something"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
            tool_calls=[{"id": "call_1", "type": "function"}],
            reasoning="Need to execute test function.",
        )
        assert sample.sample_type == SampleType.AGENT_TOOL
        assert sample.reasoning == "Need to execute test function."

    def test_to_dict(self):
        sample = AgentToolSample(
            messages=[{"role": "user", "content": "Go"}],
            tools=[{"type": "function"}],
            tool_calls=[{"id": "c1"}],
        )
        d = sample.to_dict()
        assert d["sample_type"] == "agent_tool"
        assert len(d["tools"]) == 1


class TestPipelineResult:
    """测试 PipelineResult."""

    def test_creation(self):
        result = PipelineResult(
            total_samples=100,
            samples_by_type={"sft": 50, "rag_eval": 50},
            output_files=["/tmp/out.jsonl"],
        )
        assert result.total_samples == 100
        assert result.duration_seconds == 0.0

    def test_to_dict(self):
        result = PipelineResult(
            total_samples=10,
            quality_metrics={"diversity": 0.8},
        )
        d = result.to_dict()
        assert d["total_samples"] == 10
        assert d["quality_metrics"]["diversity"] == 0.8


class TestGeneratorConfig:
    """测试 GeneratorConfig."""

    def test_defaults(self):
        config = GeneratorConfig()
        assert config.max_samples == 1000
        assert config.llm_enhance is False
        assert config.enable_multi_turn is False


class TestQualityConfig:
    """测试 QualityConfig."""

    def test_defaults(self):
        config = QualityConfig()
        assert config.enable_deduplication is True
        assert config.min_question_length == 5
        assert config.max_duplicate_ratio == 0.1
