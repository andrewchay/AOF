# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""训练数据生成流水线.

协调多种生成器的执行，提供统一的异步生成入口.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..storage.base import GraphBackend
from ..dataset_manager import DatasetManager

from .models import (
    GeneratorConfig,
    PipelineResult,
    QualityConfig,
    TrainingSample,
)
from .loaders import GraphLoader, DocumentLoader
from .quality import QualityFilter
from .formatters import JSONLFormatter, SplitFormatter
from .generators.base import GeneratorBase

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """流水线配置."""
    dataset_name: str = ""
    output_dir: Path = field(default_factory=lambda: Path("./training_data"))
    generators: list[GeneratorBase] = field(default_factory=list)
    generator_config: GeneratorConfig = field(default_factory=GeneratorConfig)
    quality_config: QualityConfig = field(default_factory=QualityConfig)
    max_samples: Optional[int] = None
    enable_train_val_test_split: bool = False
    split_ratios: tuple[float, float, float] = (0.8, 0.1, 0.1)
    progress_callback: Optional[Callable[[int, int, str], None]] = None


class TrainingDataPipeline:
    """训练数据生成流水线.

    协调数据加载、样本生成、质量过滤和格式化的完整流程.

    使用示例:
        pipeline = TrainingDataPipeline(graph_backend, dataset_manager)
        result = await pipeline.run(
            dataset_name="my_dataset",
            generators=[SFTGenerator(), RAGEvalGenerator()],
            output_path=Path("./output/"),
        )
    """

    def __init__(
        self,
        graph_backend: Optional[GraphBackend] = None,
        dataset_manager: Optional[DatasetManager] = None,
    ):
        self.graph_backend = graph_backend
        self.dataset_manager = dataset_manager
        self.graph_loader = GraphLoader(graph_backend) if graph_backend else None
        self.doc_loader = DocumentLoader(dataset_manager) if dataset_manager else None

    async def run(
        self,
        dataset_name: str,
        generators: list[GeneratorBase],
        output_path: Path,
        quality_config: Optional[QualityConfig] = None,
        generator_config: Optional[GeneratorConfig] = None,
        max_samples: Optional[int] = None,
        enable_split: bool = False,
        split_ratios: tuple[float, float, float] = (0.8, 0.1, 0.1),
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> PipelineResult:
        """执行训练数据生成流水线.

        Args:
            dataset_name: 数据集名称
            generators: 生成器列表
            output_path: 输出目录
            quality_config: 质量控制配置
            generator_config: 生成器配置
            max_samples: 总样本上限
            enable_split: 是否拆分为 train/val/test
            split_ratios: 拆分比例 (train, val, test)
            progress_callback: 进度回调

        Returns:
            PipelineResult 包含生成统计
        """
        start_time = time.time()
        result = PipelineResult()

        if not generators:
            result.errors.append("No generators provided")
            return result

        config = generator_config or GeneratorConfig()
        config.dataset_name = dataset_name
        if max_samples:
            config.max_samples = max_samples // len(generators) if len(generators) > 1 else max_samples

        q_config = quality_config or QualityConfig()
        quality_filter = QualityFilter(q_config)

        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)

        formatter = JSONLFormatter()
        all_samples: list[TrainingSample] = []

        # 检查数据加载器可用性
        # 若全部生成器都不依赖加载器（如 raw 直接从原始文件读），则无需 graph/doc 后端也可运行
        needs_loaders = any(getattr(gen, "requires_data_loaders", True) for gen in generators)
        if needs_loaders and self.graph_loader is None and self.doc_loader is None:
            result.errors.append("No data loaders available (graph_backend and dataset_manager are None)")
            return result

        # 为没有加载器的创建 mock（仅当有生成器需要加载器时）
        if needs_loaders:
            graph_loader = self.graph_loader or GraphLoader(_MockBackend())
            doc_loader = self.doc_loader or DocumentLoader()
        else:
            # 全部生成器不依赖加载器（如 raw）：直接传 None，生成器会忽略它们
            graph_loader = self.graph_loader
            doc_loader = self.doc_loader

        # 并行执行各生成器
        logger.info(f"Starting pipeline with {len(generators)} generators for dataset '{dataset_name}'")

        generator_tasks = []
        for gen in generators:
            task = asyncio.create_task(
                self._run_generator(
                    gen, graph_loader, doc_loader, config, dataset_name
                ),
                name=gen.name,
            )
            generator_tasks.append((gen, task))

        # 收集所有样本
        for gen, task in generator_tasks:
            try:
                samples = await task
                sample_type = gen.sample_type
                result.samples_by_type[sample_type] = len(samples)
                logger.info(f"Generator '{gen.name}' produced {len(samples)} {sample_type} samples")

                # 质量过滤（忠实数据的生成器如 raw 可跳过合成样本质量过滤）
                bypass = bool(getattr(gen, "bypass_quality_filter", False))
                if not bypass and (q_config.enable_deduplication or q_config.min_question_length > 0):
                    filtered_samples, report = quality_filter.filter(samples)
                    logger.info(
                        f"Quality filter for {sample_type}: {report.passed}/{report.total_input} passed"
                    )
                    result.quality_metrics[sample_type] = report.to_dict()
                    samples = filtered_samples

                all_samples.extend(samples)
            except Exception as e:
                logger.error(f"Generator '{gen.name}' failed: {e}", exc_info=True)
                result.errors.append(f"{gen.name}: {str(e)}")

        # 全局去重（跨生成器）
        if q_config.enable_deduplication and len(generators) > 1:
            all_samples = self._global_deduplicate(all_samples)

        result.total_samples = len(all_samples)
        logger.info(f"Total samples after filtering: {result.total_samples}")

        # 输出
        if all_samples:
            if enable_split:
                split_formatter = SplitFormatter(
                    train_ratio=split_ratios[0],
                    val_ratio=split_ratios[1],
                    test_ratio=split_ratios[2],
                )
                split_paths = await split_formatter.write_splits(
                    all_samples, output_path, formatter
                )
                result.output_files = list(split_paths.values())
            else:
                output_file = output_path / f"{dataset_name}_training_data.jsonl"
                await formatter.write_samples(all_samples, output_file)
                result.output_files = [str(output_file)]

        result.duration_seconds = round(time.time() - start_time, 2)
        logger.info(f"Pipeline completed in {result.duration_seconds}s")

        return result

    async def _run_generator(
        self,
        generator: GeneratorBase,
        graph_loader: GraphLoader,
        doc_loader: DocumentLoader,
        config: GeneratorConfig,
        dataset_name: str,
    ) -> list[TrainingSample]:
        """运行单个生成器并收集所有样本."""
        samples: list[TrainingSample] = []
        async for sample in generator.generate(graph_loader, doc_loader, config):
            # 设置数据集名称
            sample.source.dataset_name = dataset_name
            samples.append(sample)
        return samples

    @staticmethod
    def _global_deduplicate(samples: list[TrainingSample]) -> list[TrainingSample]:
        """全局去重（跨生成器）."""
        seen: set[str] = set()
        unique: list[TrainingSample] = []

        for sample in samples:
            # 使用 sample 的内容哈希
            h = _compute_content_hash(sample)
            if h not in seen:
                seen.add(h)
                unique.append(sample)

        logger.info(f"Global deduplication: {len(samples)} -> {len(unique)}")
        return unique

    async def estimate(
        self,
        generators: list[GeneratorBase],
        node_count: int = 0,
        edge_count: int = 0,
        document_count: int = 0,
    ) -> dict[str, int]:
        """估算各生成器的产出数量.

        Returns:
            {generator_name: estimated_count}
        """
        estimates = {}
        for gen in generators:
            try:
                est = await gen.estimate_yield(node_count, edge_count, document_count)
                estimates[gen.name] = est
            except Exception as e:
                logger.warning(f"Failed to estimate yield for {gen.name}: {e}")
                estimates[gen.name] = 0
        return estimates


# Mock backend for when graph_backend is not available
class _MockBackend(GraphBackend):
    """Mock 图后端，用于无图谱数据时的降级."""

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True

    async def add_node(self, node) -> None:
        pass

    async def add_edge(self, edge) -> None:
        pass

    async def add_triples(self, triples) -> None:
        pass

    async def merge_node(self, node) -> None:
        pass

    async def get_nodes(self, limit=100, offset=0):
        return []

    async def get_edges(self, limit=100, offset=0):
        return []

    async def get_node(self, node_id):
        return None

    async def get_neighbors(self, node_id, depth=1, limit=50):
        return [], []

    async def find_paths(self, source_id, target_id, max_length=4):
        return []

    async def shortest_path(self, source_id, target_id):
        return None

    async def execute_cypher(self, query, parameters=None):
        return []

    async def pagerank(self, top_k=100):
        return []

    async def community_detection(self, algorithm="louvain"):
        return []

    async def centrality(self, centrality_type="degree", top_k=100):
        return []

    async def get_statistics(self):
        from ..storage.base import GraphStatistics
        return GraphStatistics()

    async def count_nodes(self):
        return 0

    async def count_edges(self):
        return 0

    async def delete_node(self, node_id):
        pass

    async def delete_edge(self, edge_id):
        pass

    async def clear(self):
        pass


def _compute_content_hash(sample: TrainingSample) -> str:
    """计算样本内容哈希."""
    import hashlib
    content = ""

    if hasattr(sample, "question"):
        content = getattr(sample, "question", "").strip().lower()
    elif hasattr(sample, "messages") and sample.messages:
        for msg in reversed(sample.messages):
            if msg.get("role") == "user":
                content = msg.get("content", "").strip().lower()
                break

    return hashlib.blake2b(content.encode("utf-8"), digest_size=16).hexdigest()
