#!/usr/bin/env python3
"""批量数据摄取模块 - 支持目录批量摄取和自动数据集发现。

本模块提供：
- 目录自动扫描和数据集发现
- 批量文件摄取
- 递归子目录处理
- 增量摄取支持

使用示例:
    ingestor = BatchIngestor()
    
    # 批量摄取目录
    result = await ingestor.ingest_directory(
        directory_path="/path/to/data",
        dataset_name="my_dataset",
        recursive=True,
    )
    
    # 自动发现数据集
    datasets = ingestor.discover_datasets("/path/to/data")
"""

from __future__ import annotations

import mimetypes
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class IngestedFile:
    """已摄取的文件信息。"""
    path: Path
    dataset_name: str
    mime_type: Optional[str] = None
    status: str = "pending"  # pending, success, error
    message: Optional[str] = None
    data_id: Optional[str] = None


@dataclass
class BatchIngestionResult:
    """批量摄取结果。"""
    dataset_name: str
    total_files: int = 0
    success_count: int = 0
    error_count: int = 0
    files: list[IngestedFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class DiscoveredDataset:
    """发现的数据集。"""
    name: str
    path: Path
    files: list[Path] = field(default_factory=list)
    file_count: int = 0
    total_size_bytes: int = 0


class BatchIngestor:
    """批量数据摄取器。"""
    
    # 支持的文件扩展名
    SUPPORTED_EXTENSIONS = {
        # 文本文件
        ".txt", ".md", ".markdown", ".rst",
        # 代码文件
        ".py", ".js", ".ts", ".java", ".go", ".rs", ".c", ".cpp", ".h",
        # 数据文件
        ".json", ".jsonl", ".csv", ".yaml", ".yml", ".xml",
        # 文档文件
        ".pdf", ".doc", ".docx", ".ppt", ".pptx",
        # SQL
        ".sql",
        # Jupyter
        ".ipynb",
    }
    
    # 最大文件大小（100MB）
    MAX_FILE_SIZE = 100 * 1024 * 1024
    
    def __init__(self):
        self._cognee_available = self._check_cognee()
    
    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        return importlib.util.find_spec("cognee") is not None
    
    def discover_datasets(
        self,
        root_path: Path | str,
        recursive: bool = True,
        min_files: int = 1,
    ) -> list[DiscoveredDataset]:
        """
        自动发现目录中的数据集。
        
        数据集定义：
        - 非空目录（包含至少 min_files 个支持文件）
        - 目录名作为数据集名
        - 递归处理子目录（如果 recursive=True）
        
        Args:
            root_path: 根目录路径
            recursive: 是否递归子目录
            min_files: 最小文件数量
            
        Returns:
            发现的数据集列表
        """
        root_path = Path(root_path)
        
        if not root_path.exists():
            raise FileNotFoundError(f"目录不存在: {root_path}")
        
        if not root_path.is_dir():
            raise NotADirectoryError(f"不是目录: {root_path}")
        
        datasets = []
        
        if recursive:
            # 递归扫描所有子目录
            for dir_path in root_path.rglob("*"):
                if dir_path.is_dir():
                    dataset = self._analyze_directory(dir_path, root_path)
                    if dataset and dataset.file_count >= min_files:
                        datasets.append(dataset)
        else:
            # 只扫描直接子目录
            for dir_path in root_path.iterdir():
                if dir_path.is_dir():
                    dataset = self._analyze_directory(dir_path, root_path)
                    if dataset and dataset.file_count >= min_files:
                        datasets.append(dataset)
        
        # 也检查根目录本身
        root_dataset = self._analyze_directory(root_path, root_path)
        if root_dataset and root_dataset.file_count >= min_files:
            datasets.insert(0, root_dataset)
        
        # 按文件数量排序
        datasets.sort(key=lambda d: d.file_count, reverse=True)
        
        return datasets
    
    def _analyze_directory(self, dir_path: Path, root_path: Path) -> Optional[DiscoveredDataset]:
        """分析目录，返回数据集信息。"""
        files = []
        total_size = 0
        
        for file_path in dir_path.iterdir():
            if not file_path.is_file():
                continue
            
            # 检查扩展名
            if file_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                continue
            
            # 检查文件大小
            try:
                size = file_path.stat().st_size
                if size > self.MAX_FILE_SIZE:
                    continue
                total_size += size
                files.append(file_path)
            except Exception:
                continue
        
        if not files:
            return None
        
        # 生成数据集名称
        relative_path = dir_path.relative_to(root_path)
        if relative_path == Path("."):
            dataset_name = root_path.name or "root"
        else:
            dataset_name = str(relative_path).replace("/", "_").replace("\\", "_")
        
        return DiscoveredDataset(
            name=dataset_name,
            path=dir_path,
            files=files,
            file_count=len(files),
            total_size_bytes=total_size,
        )
    
    async def ingest_directory(
        self,
        directory_path: Path | str,
        dataset_name: Optional[str] = None,
        recursive: bool = True,
        file_pattern: Optional[str] = None,
        spec: Optional[dict] = None,
    ) -> BatchIngestionResult:
        """
        批量摄取目录中的文件。
        
        Args:
            directory_path: 目录路径
            dataset_name: 数据集名称（默认使用目录名）
            recursive: 是否递归子目录
            file_pattern: 可选的文件匹配模式（如 "*.txt"）
            spec: AOF spec 配置（可选）
            
        Returns:
            批量摄取结果
        """
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        directory_path = Path(directory_path)
        
        if not directory_path.exists():
            raise FileNotFoundError(f"目录不存在: {directory_path}")
        
        if dataset_name is None:
            dataset_name = directory_path.name or "batch_ingestion"
        
        # 收集文件
        if file_pattern:
            files = list(directory_path.rglob(file_pattern) if recursive else directory_path.glob(file_pattern))
        else:
            # 使用支持的扩展名
            files = []
            for ext in self.SUPPORTED_EXTENSIONS:
                files.extend(directory_path.rglob(f"*{ext}") if recursive else directory_path.glob(f"*{ext}"))
        
        # 过滤有效文件
        valid_files = [f for f in files if f.is_file() and f.stat().st_size <= self.MAX_FILE_SIZE]
        
        result = BatchIngestionResult(dataset_name=dataset_name, total_files=len(valid_files))
        
        # 批量摄取
        for file_path in valid_files:
            ingested = await self._ingest_single_file(
                file_path=file_path,
                dataset_name=dataset_name,
                spec=spec,
            )
            result.files.append(ingested)
            
            if ingested.status == "success":
                result.success_count += 1
            else:
                result.error_count += 1
                if ingested.message:
                    result.errors.append(f"{file_path}: {ingested.message}")
        
        return result
    
    async def _ingest_single_file(
        self,
        file_path: Path,
        dataset_name: str,
        spec: Optional[dict],
    ) -> IngestedFile:
        """摄取单个文件。"""
        ingested = IngestedFile(
            path=file_path,
            dataset_name=dataset_name,
            mime_type=mimetypes.guess_type(str(file_path))[0],
        )
        
        try:
            # 使用 cognee.add
            import cognee
            
            # 构建添加参数
            add_kwargs = {"dataset_name": dataset_name}
            
            # 添加文件
            await cognee.add(str(file_path), **add_kwargs)
            
            ingested.status = "success"
            ingested.message = "Successfully ingested"
            
        except Exception as e:
            ingested.status = "error"
            ingested.message = str(e)
        
        return ingested
    
    async def ingest_discovered_datasets(
        self,
        root_path: Path | str,
        recursive: bool = True,
        min_files: int = 1,
        spec: Optional[dict] = None,
    ) -> dict[str, BatchIngestionResult]:
        """
        自动发现数据集并批量摄取。
        
        Args:
            root_path: 根目录路径
            recursive: 是否递归子目录
            min_files: 最小文件数量
            spec: AOF spec 配置
            
        Returns:
            数据集名称 -> 摄取结果 的映射
        """
        # 发现数据集
        discovered = self.discover_datasets(root_path, recursive, min_files)
        
        results = {}
        
        for dataset in discovered:
            # 为每个发现的数据集摄取
            result = await self.ingest_directory(
                directory_path=dataset.path,
                dataset_name=dataset.name,
                recursive=False,  # 已经处理了递归
                spec=spec,
            )
            results[dataset.name] = result
        
        return results
    
    def preview_ingestion(
        self,
        directory_path: Path | str,
        recursive: bool = True,
        file_pattern: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        预览将要摄取的内容（不实际执行）。
        
        Args:
            directory_path: 目录路径
            recursive: 是否递归
            file_pattern: 文件匹配模式
            
        Returns:
            预览信息
        """
        directory_path = Path(directory_path)
        
        if not directory_path.exists():
            return {"error": f"目录不存在: {directory_path}"}
        
        # 收集文件
        if file_pattern:
            files = list(directory_path.rglob(file_pattern) if recursive else directory_path.glob(file_pattern))
        else:
            files = []
            for ext in self.SUPPORTED_EXTENSIONS:
                files.extend(directory_path.rglob(f"*{ext}") if recursive else directory_path.glob(f"*{ext}"))
        
        # 分析
        valid_files = []
        oversized_files = []
        total_size = 0
        
        for f in files:
            if not f.is_file():
                continue
            
            try:
                size = f.stat().st_size
                if size > self.MAX_FILE_SIZE:
                    oversized_files.append({"path": str(f), "size": size})
                else:
                    valid_files.append({"path": str(f), "size": size})
                    total_size += size
            except Exception:
                pass
        
        # 按类型分组
        by_extension = {}
        for f in valid_files:
            ext = Path(f["path"]).suffix.lower()
            by_extension.setdefault(ext, []).append(f)
        
        return {
            "directory": str(directory_path),
            "recursive": recursive,
            "pattern": file_pattern,
            "summary": {
                "total_files": len(valid_files) + len(oversized_files),
                "valid_files": len(valid_files),
                "oversized_files": len(oversized_files),
                "total_size_bytes": total_size,
                "total_size_human": self._format_bytes(total_size),
            },
            "by_extension": {ext: len(files) for ext, files in by_extension.items()},
            "sample_files": [f["path"] for f in valid_files[:5]],
            "warnings": [f"文件过大: {f['path']}" for f in oversized_files[:3]],
        }
    
    @staticmethod
    def _format_bytes(size_bytes: int) -> str:
        """格式化字节大小。"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.2f} TB"


# 便捷函数
async def quick_batch_ingest(
    directory: str,
    dataset_name: Optional[str] = None,
    recursive: bool = True,
) -> BatchIngestionResult:
    """快速批量摄取。
    
    Args:
        directory: 目录路径
        dataset_name: 数据集名称
        recursive: 是否递归
        
    Returns:
        摄取结果
    """
    ingestor = BatchIngestor()
    return await ingestor.ingest_directory(
        directory_path=directory,
        dataset_name=dataset_name,
        recursive=recursive,
    )


def preview_directory(
    directory: str,
    recursive: bool = True,
) -> dict[str, Any]:
    """预览目录内容。
    
    Args:
        directory: 目录路径
        recursive: 是否递归
        
    Returns:
        预览信息
    """
    ingestor = BatchIngestor()
    return ingestor.preview_ingestion(directory, recursive)


def scan_for_datasets(
    directory: str,
    recursive: bool = True,
    min_files: int = 1,
) -> list[DiscoveredDataset]:
    """扫描目录发现数据集。
    
    Args:
        directory: 目录路径
        recursive: 是否递归
        min_files: 最小文件数
        
    Returns:
        数据集列表
    """
    ingestor = BatchIngestor()
    return ingestor.discover_datasets(directory, recursive, min_files)
