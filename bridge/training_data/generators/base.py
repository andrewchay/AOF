"""训练数据生成器 - 抽象基类.

定义所有生成器的统一接口.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Optional

from ..models import TrainingSample, GeneratorConfig
from ..loaders import GraphLoader, DocumentLoader

logger = logging.getLogger(__name__)


class GeneratorBase(ABC):
    """训练数据生成器抽象基类.

    所有具体生成器必须继承此类并实现抽象方法.

    使用示例:
        class MyGenerator(GeneratorBase):
            @property
            def sample_type(self) -> str:
                return "my_type"

            async def generate(self, graph_loader, doc_loader, config, progress_callback=None):
                # 实现生成逻辑
                yield TrainingSample(...)
    """

    def __init__(self, name: Optional[str] = None):
        self.name = name or self.__class__.__name__

    @property
    def requires_data_loaders(self) -> bool:
        """该生成器是否必须依赖图谱/文档加载器.

        默认为 True（既有 sft/rag_eval/agent_tool 都吃图谱/文档）。
        不依赖加载器的生成器（如 RawTrajectoryGenerator 直接从原始文件读取）可覆写为 False，
        以便在无 graph_backend/dataset_manager 时也能运行。
        """
        return True

    @property
    def bypass_quality_filter(self) -> bool:
        """该生成器的样本是否跳过合成样本质量过滤.

        默认为 False。忠实于真实原始数据的生成器（如 RawTrajectoryGenerator）
        可覆写为 True，避免被为「合成样本」设计的长度阈值（如 min_answer_length=10）
        误杀真实且短的对话消息。
        """
        return False

    @property
    @abstractmethod
    def sample_type(self) -> str:
        """返回此生成器产生的样本类型标识.

        Returns:
            样本类型字符串，如 "sft", "rag_eval", "agent_tool"
        """
        ...

    @abstractmethod
    async def generate(
        self,
        graph_loader: GraphLoader,
        doc_loader: DocumentLoader,
        config: GeneratorConfig,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> AsyncIterator[TrainingSample]:
        """生成训练样本.

        Args:
            graph_loader: 图谱数据加载器
            doc_loader: 文档数据加载器
            config: 生成器配置
            progress_callback: 可选的进度回调函数
                签名: callback(current: int, total: int, message: str) -> None

        Yields:
            TrainingSample 及其子类的实例
        """
        ...

    @abstractmethod
    async def estimate_yield(
        self,
        node_count: int,
        edge_count: int,
        document_count: int,
    ) -> int:
        """估算预期产出数量.

        用于在生成前向用户展示预计生成的样本数.

        Args:
            node_count: 图谱节点数量
            edge_count: 图谱边数量
            document_count: 文档数量

        Returns:
            预计生成的样本数量
        """
        ...

    def _report_progress(
        self,
        current: int,
        total: int,
        message: str,
        callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        """报告进度（内部辅助方法）."""
        if callback:
            try:
                callback(current, total, message)
            except Exception as e:
                logger.warning(f"Progress callback error: {e}")
        elif current % max(total // 10, 1) == 0 or current == total:
            logger.info(f"[{self.name}] {current}/{total}: {message}")
