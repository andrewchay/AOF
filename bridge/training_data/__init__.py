"""AOF 训练数据生成器.

基于文档和知识图谱，自动生成用于 AI/Agent 训练的结构化数据集.

支持三种训练数据类型:
- SFT 微调数据: instruction-response 对话对
- RAG 评估数据: question-answer-context 三元组
- Agent 工具调用数据: function calling 格式样本

使用示例:
    from bridge.training_data import TrainingDataPipeline
    from bridge.training_data.generators import SFTGenerator, RAGEvalGenerator

    pipeline = TrainingDataPipeline()
    result = await pipeline.run(
        dataset_name="my_dataset",
        generators=[SFTGenerator(), RAGEvalGenerator()],
        output_path=Path("./training_data/"),
    )
"""

from __future__ import annotations

from .models import (
    SampleSource,
    TrainingSample,
    SFTSample,
    RAGEvalSample,
    AgentToolSample,
    PipelineResult,
    GeneratorConfig,
    QualityConfig,
)
from .pipeline import TrainingDataPipeline
from .quality import QualityFilter
from .formatters import JSONLFormatter

__all__ = [
    "SampleSource",
    "TrainingSample",
    "SFTSample",
    "RAGEvalSample",
    "AgentToolSample",
    "PipelineResult",
    "GeneratorConfig",
    "QualityConfig",
    "TrainingDataPipeline",
    "QualityFilter",
    "JSONLFormatter",
]
