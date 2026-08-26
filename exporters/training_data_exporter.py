"""训练数据导出器.

将 AOF 知识图谱和文档导出为 AI/Agent 训练数据集（JSONL 格式）.

使用示例:
    exporter = TrainingDataExporter()
    result = await exporter.export(
        dataset_id="my_dataset",
        output_dir="./training_data/",
        generators=["sft", "rag_eval"],
    )

    # 便捷函数
    result = await export_dataset_to_training_data(
        dataset_id="my_dataset",
        output_dir="./training_data/",
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from bridge.training_data import TrainingDataPipeline
from bridge.training_data.generators import SFTGenerator, RAGEvalGenerator, AgentToolGenerator
from bridge.training_data.raw_trajectory import RawTrajectoryGenerator
from bridge.training_data.models import QualityConfig

logger = logging.getLogger(__name__)


@dataclass
class ExportResult:
    """导出结果统计."""
    output_dir: Path
    total_samples: int = 0
    samples_by_type: dict[str, int] = field(default_factory=dict)
    files_created: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    mode: str = "training_data"
    duration_seconds: float = 0.0
    quality_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "total_samples": self.total_samples,
            "samples_by_type": self.samples_by_type,
            "files_created": [str(p) for p in self.files_created],
            "errors": self.errors,
            "mode": self.mode,
            "duration_seconds": self.duration_seconds,
            "quality_metrics": self.quality_metrics,
        }


class TrainingDataExporter:
    """AOF 训练数据导出器.

    将 Cognee 知识图谱和文档导出为结构化训练数据集，支持：
    - SFT 微调数据（instruction-response）
    - RAG 评估数据（question-answer-context）
    - Agent 工具调用数据（function calling）
    """

    def __init__(
        self,
        graph_backend=None,
        dataset_manager=None,
    ):
        self.graph_backend = graph_backend
        self.dataset_manager = dataset_manager

    async def export(
        self,
        dataset_id: str,
        output_dir: Path,
        generators: Optional[list[str]] = None,
        max_samples: Optional[int] = None,
        quality_config: Optional[QualityConfig] = None,
        enable_split: bool = False,
        raw_sources: Optional[list[str]] = None,
    ) -> ExportResult:
        """导出训练数据集.

        Args:
            dataset_id: 数据集标识
            output_dir: 输出目录
            generators: 生成器类型列表，如 ["sft", "rag_eval", "agent_tool", "raw"]
            max_samples: 最大样本数
            quality_config: 质量控制配置
            enable_split: 是否拆分为 train/val/test
            raw_sources: 真实对话消息 / Agent trajectory 源文件路径（generators 含 "raw" 时必填）

        Returns:
            ExportResult 包含导出统计
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 解析生成器
        generator_instances = self._resolve_generators(generators or ["sft", "rag_eval"], raw_sources)

        # 创建流水线
        pipeline = TrainingDataPipeline(
            graph_backend=self.graph_backend,
            dataset_manager=self.dataset_manager,
        )

        # 执行生成
        result = await pipeline.run(
            dataset_name=dataset_id,
            generators=generator_instances,
            output_path=output_dir,
            quality_config=quality_config,
            max_samples=max_samples,
            enable_split=enable_split,
        )

        # 转换为 ExportResult
        files_created = [Path(f) for f in result.output_files]
        return ExportResult(
            output_dir=output_dir,
            total_samples=result.total_samples,
            samples_by_type=result.samples_by_type,
            files_created=files_created,
            errors=result.errors,
            duration_seconds=result.duration_seconds,
            quality_metrics=result.quality_metrics,
        )

    @staticmethod
    def _resolve_generators(names: list[str], raw_sources: Optional[list[str]] = None) -> list:
        """将生成器名称解析为实例."""
        mapping = {
            "sft": SFTGenerator,
            "rag_eval": RAGEvalGenerator,
            "agent_tool": AgentToolGenerator,
            "raw": RawTrajectoryGenerator,
        }
        instances = []
        for name in names:
            cls = mapping.get(name)
            if cls:
                gen = cls()
                if name == "raw" and raw_sources:
                    gen = gen.with_sources(raw_sources)
                instances.append(gen)
            else:
                logger.warning(f"Unknown generator type: {name}")
        return instances


# 便捷函数
async def export_dataset_to_training_data(
    dataset_id: str,
    output_dir: str | Path,
    generators: Optional[list[str]] = None,
    max_samples: Optional[int] = None,
    enable_split: bool = False,
    graph_backend=None,
    dataset_manager=None,
    raw_sources: Optional[list[str]] = None,
) -> ExportResult:
    """便捷函数：导出数据集为训练数据.

    Args:
        dataset_id: 数据集标识
        output_dir: 输出目录
        generators: 生成器类型列表
        max_samples: 最大样本数
        enable_split: 是否拆分数据集
        graph_backend: 图后端实例
        dataset_manager: 数据集管理器实例
        raw_sources: 真实对话消息 / Agent trajectory 源文件路径（generators 含 "raw" 时必填）

    Returns:
        ExportResult
    """
    exporter = TrainingDataExporter(
        graph_backend=graph_backend,
        dataset_manager=dataset_manager,
    )
    return await exporter.export(
        dataset_id=dataset_id,
        output_dir=Path(output_dir),
        generators=generators,
        max_samples=max_samples,
        enable_split=enable_split,
        raw_sources=raw_sources,
    )
