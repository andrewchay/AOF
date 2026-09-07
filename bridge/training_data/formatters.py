"""训练数据生成器 - 格式化输出.

支持 JSONL 格式的序列化与流式写入.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .models import TrainingSample

logger = logging.getLogger(__name__)


@dataclass
class FormatterConfig:
    """格式化配置."""
    ensure_ascii: bool = False
    indent: Optional[int] = None  # None 表示紧凑格式（JSONL）
    sort_keys: bool = False
    chunk_size: int = 1000  # 每写入多少条 sample 后刷盘


class JSONLFormatter:
    """JSONL 格式化器.

    将 TrainingSample 序列化为 JSONL 格式，支持流式写入大文件.
    """

    def __init__(self, config: Optional[FormatterConfig] = None):
        self.config = config or FormatterConfig()

    def serialize(self, sample: TrainingSample) -> str:
        """将单个样本序列化为 JSON 字符串."""
        data = sample.to_dict()
        return json.dumps(
            data,
            ensure_ascii=self.config.ensure_ascii,
            indent=self.config.indent,
            sort_keys=self.config.sort_keys,
            default=self._json_default,
        )

    async def write_samples(
        self,
        samples: list[TrainingSample],
        output_path: Path,
        mode: str = "w",
    ) -> int:
        """将样本列表写入 JSONL 文件.

        Args:
            samples: 样本列表
            output_path: 输出文件路径
            mode: 文件打开模式，"w" 覆盖，"a" 追加

        Returns:
            写入的样本数
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        written = 0
        with open(output_path, mode, encoding="utf-8") as f:
            for sample in samples:
                line = self.serialize(sample)
                f.write(line + "\n")
                written += 1

        logger.info(f"Wrote {written} samples to {output_path}")
        return written

    async def write_stream(
        self,
        sample_iterator,
        output_path: Path,
        max_samples: Optional[int] = None,
    ) -> tuple[int, list[str]]:
        """流式写入样本，适用于大数据集.

        Args:
            sample_iterator: 异步样本迭代器
            output_path: 输出文件路径
            max_samples: 最大写入样本数

        Returns:
            (写入总数, 内容哈希列表用于去重)
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        written = 0
        hashes: list[str] = []
        buffer: list[str] = []

        async for sample in sample_iterator:
            if max_samples and written >= max_samples:
                break

            line = self.serialize(sample)
            buffer.append(line)
            hashes.append(self._compute_hash(sample))
            written += 1

            # 批量刷盘
            if len(buffer) >= self.config.chunk_size:
                with open(output_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(buffer) + "\n")
                buffer.clear()

        # 刷入剩余
        if buffer:
            with open(output_path, "a", encoding="utf-8") as f:
                f.write("\n".join(buffer) + "\n")

        logger.info(f"Streamed {written} samples to {output_path}")
        return written, hashes

    @staticmethod
    def _compute_hash(sample: TrainingSample) -> str:
        """计算样本内容哈希，用于去重.

        使用 BLAKE2b 算法（与 incremental_loader.py 保持一致）.
        """
        content = ""
        if hasattr(sample, "question"):
            content = sample.question  # type: ignore
        elif hasattr(sample, "messages") and sample.messages:
            # 取最后一条 user message 作为去键
            for msg in reversed(sample.messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    break

        return hashlib.blake2b(content.encode("utf-8"), digest_size=16).hexdigest()

    @staticmethod
    def _json_default(obj: Any) -> Any:
        """处理 JSON 序列化中的非标准类型."""
        if isinstance(obj, set):
            return list(obj)
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class SplitFormatter:
    """数据集拆分格式化器.

    将样本按指定比例拆分为 train/validation/test 集.
    """

    def __init__(
        self,
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
        test_ratio: float = 0.1,
        random_seed: int = 42,
    ):
        assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 0.001
        self.train_ratio = train_ratio
        self.val_ratio = val_ratio
        self.test_ratio = test_ratio
        self.random_seed = random_seed

    async def write_splits(
        self,
        samples: list[TrainingSample],
        output_dir: Path,
        base_formatter: Optional[JSONLFormatter] = None,
    ) -> dict[str, str]:
        """将样本拆分为训练/验证/测试集并写入.

        Returns:
            {"train": "path/to/train.jsonl", "validation": "...", "test": "..."}
        """
        import random

        formatter = base_formatter or JSONLFormatter()
        random.seed(self.random_seed)
        shuffled = samples.copy()
        random.shuffle(shuffled)

        n = len(shuffled)
        n_train = int(n * self.train_ratio)
        n_val = int(n * self.val_ratio)

        train_samples = shuffled[:n_train]
        val_samples = shuffled[n_train : n_train + n_val]
        test_samples = shuffled[n_train + n_val :]

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        paths = {}
        for split_name, split_samples in [
            ("train", train_samples),
            ("validation", val_samples),
            ("test", test_samples),
        ]:
            path = output_dir / f"{split_name}.jsonl"
            await formatter.write_samples(split_samples, path)
            paths[split_name] = str(path)
            logger.info(f"{split_name}: {len(split_samples)} samples -> {path}")

        return paths
