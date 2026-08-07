"""训练数据生成器 - 流水线集成测试."""

from __future__ import annotations

import pytest
from pathlib import Path

from bridge.storage.base import Node, Edge
from bridge.training_data.models import (
    GeneratorConfig,
    QualityConfig,
)
from bridge.training_data.loaders import GraphData
from bridge.training_data.generators.sft import SFTGenerator
from bridge.training_data.generators.rag_eval import RAGEvalGenerator
from bridge.training_data.generators.agent_tool import AgentToolGenerator
from bridge.training_data.pipeline import TrainingDataPipeline
from bridge.training_data.quality import QualityFilter


# 构建测试用图谱数据
def _make_test_nodes() -> list[Node]:
    return [
        Node(
            id="node_1",
            labels=["Person"],
            properties={"name": "Alice", "description": "A software engineer", "age": 30},
        ),
        Node(
            id="node_2",
            labels=["Person"],
            properties={"name": "Bob", "description": "A product manager", "age": 35},
        ),
        Node(
            id="node_3",
            labels=["Company"],
            properties={"name": "TechCorp", "description": "A technology company", "founded": 2010},
        ),
    ]


def _make_test_edges() -> list[Edge]:
    return [
        Edge(
            id="edge_1",
            source_id="node_1",
            target_id="node_2",
            relation_type="colleague",
            properties={"since": 2020},
        ),
        Edge(
            id="edge_2",
            source_id="node_1",
            target_id="node_3",
            relation_type="works_at",
            properties={"role": "engineer"},
        ),
    ]


class TestGenerators:
    """测试各生成器."""

    @pytest.mark.asyncio
    async def test_sft_generator(self):
        gen = SFTGenerator()
        nodes = _make_test_nodes()
        edges = _make_test_edges()

        # 创建 mock loaders
        from bridge.training_data.loaders import GraphLoader

        # 使用真实 GraphLoader 但需要 mock backend
        # 这里我们直接测试生成方法
        graph_data = GraphData(nodes=nodes, edges=edges)
        graph_data.triples = GraphLoader._build_triples(nodes, edges)

        # 测试 estimate_yield
        est = await gen.estimate_yield(len(nodes), len(edges), 0)
        assert est > 0

    @pytest.mark.asyncio
    async def test_rag_eval_generator(self):
        gen = RAGEvalGenerator()
        nodes = _make_test_nodes()
        edges = _make_test_edges()

        est = await gen.estimate_yield(len(nodes), len(edges), 0)
        assert est > 0

    @pytest.mark.asyncio
    async def test_agent_tool_generator(self):
        gen = AgentToolGenerator()
        nodes = _make_test_nodes()

        est = await gen.estimate_yield(len(nodes), 0, 0)
        assert est >= 0

    @pytest.mark.asyncio
    async def test_sft_generator_entity_qa(self):
        """测试 SFT 实体问答生成."""
        gen = SFTGenerator()
        nodes = _make_test_nodes()

        from bridge.training_data.loaders import GraphLoader

        # 手动调用内部生成方法
        config = GeneratorConfig(max_samples=10)
        samples = []
        async for sample in gen._generate_entity_qa(
            nodes, config, GraphLoader(_MockBackend()), 10
        ):
            samples.append(sample)

        assert len(samples) > 0
        for s in samples:
            assert s.sample_type.value == "sft"
            assert len(s.messages) >= 2

    @pytest.mark.asyncio
    async def test_rag_eval_generator_factual(self):
        """测试 RAG 事实型问题生成."""
        gen = RAGEvalGenerator()
        nodes = _make_test_nodes()

        config = GeneratorConfig(max_samples=10)
        samples = []
        async for sample in gen._generate_factual(nodes, [], config, 10):
            samples.append(sample)

        assert len(samples) > 0
        for s in samples:
            assert s.sample_type.value == "rag_eval"
            assert s.question
            assert s.answer
            assert s.query_type.value in {"factual", "relational", "aggregational", "reasoning"}


class TestPipeline:
    """测试 TrainingDataPipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_with_mock_backend(self, tmp_path: Path):
        """测试流水线使用 mock backend."""
        pipeline = TrainingDataPipeline()

        result = await pipeline.run(
            dataset_name="test_dataset",
            generators=[SFTGenerator(), RAGEvalGenerator()],
            output_path=tmp_path,
            max_samples=20,
        )

        # mock backend 返回空数据，所以应该没有样本但有结果
        assert result is not None
        assert isinstance(result.duration_seconds, float)

    @pytest.mark.asyncio
    async def test_pipeline_empty_generators(self, tmp_path: Path):
        """测试空生成器列表."""
        pipeline = TrainingDataPipeline()

        result = await pipeline.run(
            dataset_name="test",
            generators=[],
            output_path=tmp_path,
        )

        assert result.total_samples == 0
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_estimate(self):
        pipeline = TrainingDataPipeline()

        estimates = await pipeline.estimate(
            generators=[SFTGenerator(), RAGEvalGenerator()],
            node_count=100,
            edge_count=50,
            document_count=20,
        )

        assert "SFTGenerator" in estimates
        assert "RAGEvalGenerator" in estimates
        assert all(v >= 0 for v in estimates.values())


class TestQualityFilterIntegration:
    """测试质量过滤集成."""

    def test_filter_mixed_samples(self):
        from bridge.training_data.models import SFTSample, RAGEvalSample

        samples = [
            SFTSample(
                messages=[
                    {"role": "user", "content": "Good question here?"},
                    {"role": "assistant", "content": "Good answer with enough length to pass."},
                ],
            ),
            SFTSample(messages=[]),  # 应该被过滤
            RAGEvalSample(
                question="What is X?",
                answer="X is Y and it represents an important concept that we need to understand.",
                contexts=["Context with enough information to be useful for retrieval."],
            ),
            RAGEvalSample(
                question="What is X?",
                answer="X is Y and it represents an important concept.",
                contexts=[],  # 应该被过滤
            ),
        ]

        qf = QualityFilter(QualityConfig())
        passed, report = qf.filter(samples)

        assert len(passed) == 2
        assert report.total_input == 4
        assert report.passed == 2
        assert report.filtered == 2


# Mock backend for testing
class _MockBackend:
    """Mock GraphBackend for tests."""

    async def get_nodes(self, limit=100, offset=0):
        return _make_test_nodes()

    async def get_edges(self, limit=100, offset=0):
        return _make_test_edges()

    async def get_neighbors(self, node_id, depth=1, limit=50):
        return [], []

    async def execute_cypher(self, query, parameters=None):
        return []
