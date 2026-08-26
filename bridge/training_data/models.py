"""训练数据生成器 - 数据模型.

定义训练样本的数据结构，包括 SFT、RAG 评估、Agent 工具调用三种类型.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class SampleType(str, Enum):
    """训练样本类型."""
    SFT = "sft"
    RAG_EVAL = "rag_eval"
    AGENT_TOOL = "agent_tool"


class QueryType(str, Enum):
    """RAG 评估问题类型."""
    FACTUAL = "factual"           # 事实型: 基于单个实体/属性
    RELATIONAL = "relational"     # 关系型: 需要连接两个实体
    AGGREGATIONAL = "aggregational"  # 聚合型: 需要汇总多个节点
    REASONING = "reasoning"       # 推理型: 需要多跳推理


class Difficulty(str, Enum):
    """问题难度等级."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass
class SampleSource:
    """样本数据来源信息."""
    dataset_name: str
    source_type: str  # "node" | "edge" | "document" | "triple" | "path"
    source_id: Optional[str] = None
    source_uri: Optional[str] = None
    chunk_index: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_uri": self.source_uri,
            "chunk_index": self.chunk_index,
        }


@dataclass
class TrainingSample:
    """训练样本基类.

    所有训练样本的公共基类，定义统一的序列化接口.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sample_type: SampleType = SampleType.SFT
    source: SampleSource = field(default_factory=lambda: SampleSource("", ""))
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（子类应重写此方法来添加特有字段）."""
        return {
            "id": self.id,
            "sample_type": self.sample_type.value,
            "source": self.source.to_dict(),
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }

    def to_jsonl(self) -> str:
        """转换为 JSONL 格式的单行字符串."""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass
class SFTSample(TrainingSample):
    """SFT 微调训练样本.

    采用 OpenAI chat completions 格式，支持多轮对话.

    示例:
        {
            "messages": [
                {"role": "system", "content": "你是一个企业知识助手..."},
                {"role": "user", "content": "什么是有效渗透率？"},
                {"role": "assistant", "content": "有效渗透率是..."}
            ]
        }
    """
    messages: list[dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""

    def __post_init__(self):
        if self.sample_type == SampleType.SFT:
            pass  # 已由 field default 设置
        else:
            self.sample_type = SampleType.SFT

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "messages": self.messages,
            "system_prompt": self.system_prompt,
        })
        return base


@dataclass
class RAGEvalSample(TrainingSample):
    """RAG 评估训练样本.

    用于评估检索增强生成系统的效果，包含问题、答案和预期上下文.

    示例:
        {
            "question": "6.4 下半网充转化率下降的主要原因是什么？",
            "answer": "主要原因是专项邮件缺位...",
            "contexts": [
                "专项邮件是网充链路中的关键触点...",
                "6.4 下半的邮件发送覆盖率仅为 45%..."
            ],
            "difficulty": "hard",
            "query_type": "reasoning"
        }
    """
    question: str = ""
    answer: str = ""
    contexts: list[str] = field(default_factory=list)
    difficulty: Difficulty = Difficulty.MEDIUM
    query_type: QueryType = QueryType.FACTUAL
    ground_truth_sources: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.sample_type = SampleType.RAG_EVAL

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "question": self.question,
            "answer": self.answer,
            "contexts": self.contexts,
            "difficulty": self.difficulty.value,
            "query_type": self.query_type.value,
            "ground_truth_sources": self.ground_truth_sources,
        })
        return base


@dataclass
class AgentToolSample(TrainingSample):
    """Agent 工具调用训练样本.

    采用 OpenAI function calling 格式，用于训练 Agent 的工具使用能力.

    示例:
        {
            "messages": [
                {"role": "user", "content": "对 6.4 下半纯新玩家执行邮件补足策略"}
            ],
            "tools": [...],
            "tool_calls": [...]
        }
    """
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str = ""

    def __post_init__(self):
        self.sample_type = SampleType.AGENT_TOOL

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "messages": self.messages,
            "tools": self.tools,
            "tool_calls": self.tool_calls,
            "reasoning": self.reasoning,
        })
        return base


@dataclass
class PipelineResult:
    """训练数据生成流水线结果."""
    total_samples: int = 0
    samples_by_type: dict[str, int] = field(default_factory=dict)
    output_files: list[str] = field(default_factory=list)
    quality_metrics: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_samples": self.total_samples,
            "samples_by_type": self.samples_by_type,
            "output_files": self.output_files,
            "quality_metrics": self.quality_metrics,
            "duration_seconds": self.duration_seconds,
            "errors": self.errors,
        }


@dataclass
class GeneratorConfig:
    """生成器配置."""
    max_samples: int = 1000
    system_prompt_template: str = ""
    llm_enhance: bool = False
    include_metadata: bool = True
    dataset_name: str = ""  # 数据集名称，用于加载文档
    # SFT 特有
    enable_multi_turn: bool = False
    max_turns: int = 3
    # RAG 特有
    max_contexts_per_question: int = 3
    context_max_length: int = 2000
    # Agent 特有
    tool_schema_source: Optional[str] = None  # 工具 schema 来源路径
    # Raw 真实轨迹/对话 特有
    raw_sources: Optional[list[str]] = None  # 真实对话/trajectory 源文件路径列表


@dataclass
class QualityConfig:
    """质量控制配置."""
    enable_deduplication: bool = True
    min_question_length: int = 5
    max_question_length: int = 500
    min_answer_length: int = 10
    max_answer_length: int = 8000
    min_diversity_score: float = 0.3
    max_duplicate_ratio: float = 0.1
    enable_llm_scoring: bool = False
