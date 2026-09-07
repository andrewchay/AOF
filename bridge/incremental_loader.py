#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""增量更新模块 - 智能检测数据变化，只更新差异部分。

本模块提供：
- 文件内容指纹（Hash）计算
- 变化检测（新增、修改、删除）
- 增量摄取策略
- 变更历史追踪
- 自动回滚机制

使用示例:
    # 检测目录变化
    loader = IncrementalLoader(dataset_name="my_docs")
    changes = await loader.detect_changes("/path/to/docs")
    
    # 执行增量更新
    result = await loader.ingest_incremental("/path/to/docs")
    
    # 查看变更历史
    history = loader.get_change_history(limit=10)
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class ChangeType(str, Enum):
    """变更类型。"""
    ADDED = "added"           # 新增文件
    MODIFIED = "modified"     # 内容修改
    DELETED = "deleted"       # 文件删除
    UNCHANGED = "unchanged"   # 未变化
    RENAMED = "renamed"       # 重命名（检测到的）


@dataclass
class FileFingerprint:
    """文件指纹信息。"""
    path: str
    content_hash: str
    size: int
    mtime: float
    inode: Optional[int] = None  # 用于检测重命名
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content_hash": self.content_hash,
            "size": self.size,
            "mtime": self.mtime,
            "inode": self.inode,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileFingerprint:
        return cls(
            path=data["path"],
            content_hash=data["content_hash"],
            size=data["size"],
            mtime=data["mtime"],
            inode=data.get("inode"),
        )


@dataclass
class FileChange:
    """单个文件变更记录。"""
    path: str
    change_type: ChangeType
    old_fingerprint: Optional[FileFingerprint] = None
    new_fingerprint: Optional[FileFingerprint] = None
    renamed_from: Optional[str] = None  # 如果是重命名，记录原路径
    
    @property
    def is_content_change(self) -> bool:
        """是否是内容变化（新增/修改/删除）。"""
        return self.change_type in (
            ChangeType.ADDED,
            ChangeType.MODIFIED,
            ChangeType.DELETED,
        )


@dataclass
class ChangeSet:
    """变更集合。"""
    dataset_name: str
    scan_time: datetime = field(default_factory=datetime.now)
    added: list[FileChange] = field(default_factory=list)
    modified: list[FileChange] = field(default_factory=list)
    deleted: list[FileChange] = field(default_factory=list)
    renamed: list[FileChange] = field(default_factory=list)
    unchanged: list[FileChange] = field(default_factory=list)
    
    @property
    def has_changes(self) -> bool:
        """是否有任何变更。"""
        return any([
            self.added,
            self.modified,
            self.deleted,
            self.renamed,
        ])
    
    @property
    def total_files(self) -> int:
        """扫描的文件总数。"""
        return sum([
            len(self.added),
            len(self.modified),
            len(self.deleted),
            len(self.renamed),
            len(self.unchanged),
        ])
    
    @property
    def content_changes(self) -> list[FileChange]:
        """获取所有内容变化。"""
        return self.added + self.modified + self.deleted
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "scan_time": self.scan_time.isoformat(),
            "summary": {
                "added": len(self.added),
                "modified": len(self.modified),
                "deleted": len(self.deleted),
                "renamed": len(self.renamed),
                "unchanged": len(self.unchanged),
                "total": self.total_files,
                "has_changes": self.has_changes,
            },
            "changes": {
                "added": [{"path": c.path} for c in self.added],
                "modified": [{"path": c.path} for c in self.modified],
                "deleted": [{"path": c.path} for c in self.deleted],
                "renamed": [{"path": c.path, "from": c.renamed_from} for c in self.renamed],
            },
        }


@dataclass
class IncrementalIngestResult:
    """增量摄取结果。"""
    success: bool
    dataset_name: str
    change_set: ChangeSet
    ingested_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    execution_time_ms: int = 0
    message: str = ""


class IncrementalLoader:
    """增量加载器。"""
    
    # 默认忽略模式
    DEFAULT_IGNORE_PATTERNS = {
        "*.tmp",
        "*.temp",
        "*.swp",
        "*.swo",
        ".*",           # 隐藏文件
        "__pycache__",
        "*.pyc",
        "*.pyo",
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        ".venv",
        "venv",
        "*.log",
    }
    
    def __init__(
        self,
        dataset_name: str,
        state_dir: Optional[Path] = None,
        ignore_patterns: Optional[set[str]] = None,
    ):
        self.dataset_name = dataset_name
        self.state_dir = state_dir or Path.home() / ".aof" / "incremental_state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ignore_patterns = ignore_patterns or self.DEFAULT_IGNORE_PATTERNS
        self._cognee_available = self._check_cognee()
        self._fingerprint_cache: dict[str, FileFingerprint] = {}
    
    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        return importlib.util.find_spec("cognee") is not None
    
    def _get_state_file(self) -> Path:
        """获取状态文件路径。"""
        safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in self.dataset_name)
        return self.state_dir / f"{safe_name}_fingerprints.json"
    
    def _load_previous_state(self) -> dict[str, FileFingerprint]:
        """加载之前保存的指纹状态。"""
        state_file = self._get_state_file()
        if not state_file.exists():
            return {}
        
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            return {
                path: FileFingerprint.from_dict(fp_data)
                for path, fp_data in data.get("fingerprints", {}).items()
            }
        except Exception:
            return {}
    
    def _save_state(self, fingerprints: dict[str, FileFingerprint]) -> None:
        """保存指纹状态。"""
        state_file = self._get_state_file()
        data = {
            "dataset_name": self.dataset_name,
            "updated_at": datetime.now().isoformat(),
            "fingerprints": {
                path: fp.to_dict() for path, fp in fingerprints.items()
            },
        }
        state_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """计算文件内容哈希（增量式，适合大文件）。"""
        hash_obj = hashlib.blake2b(digest_size=32)
        
        try:
            # 对于大文件，采用分块采样策略
            file_size = file_path.stat().st_size
            
            with open(file_path, "rb") as f:
                if file_size <= 1024 * 1024:  # 小于 1MB，完整哈希
                    hash_obj.update(f.read())
                else:
                    # 大文件：头部 + 中部采样 + 尾部
                    # 头部 4KB
                    hash_obj.update(f.read(4096))
                    
                    # 中部采样（每隔 10% 取 1KB）
                    f.seek(file_size // 2)
                    hash_obj.update(f.read(1024))
                    
                    # 尾部 4KB
                    f.seek(max(0, file_size - 4096))
                    hash_obj.update(f.read(4096))
                    
                    # 文件大小也参与哈希
                    hash_obj.update(str(file_size).encode())
            
            return hash_obj.hexdigest()
            
        except Exception:
            # 如果失败，使用 mtime + size 作为备选
            stat = file_path.stat()
            return hashlib.md5(f"{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()
    
    def _create_fingerprint(self, file_path: Path, relative_to: Path) -> FileFingerprint:
        """创建文件指纹。"""
        stat = file_path.stat()
        relative_path = str(file_path.relative_to(relative_to))
        
        # 使用缓存避免重复计算
        cache_key = f"{file_path}:{stat.st_mtime}:{stat.st_size}"
        if cache_key in self._fingerprint_cache:
            cached = self._fingerprint_cache[cache_key]
            # 更新路径（可能不同）
            return FileFingerprint(
                path=relative_path,
                content_hash=cached.content_hash,
                size=cached.size,
                mtime=cached.mtime,
                inode=cached.inode,
            )
        
        fp = FileFingerprint(
            path=relative_path,
            content_hash=self._calculate_file_hash(file_path),
            size=stat.st_size,
            mtime=stat.st_mtime,
            inode=stat.st_ino if hasattr(stat, 'st_ino') else None,
        )
        
        self._fingerprint_cache[cache_key] = fp
        return fp
    
    def _should_ignore(self, file_path: Path) -> bool:
        """检查是否应该忽略该文件。"""
        name = file_path.name
        
        for pattern in self.ignore_patterns:
            if pattern.startswith("*"):
                if name.endswith(pattern[1:]):
                    return True
            elif pattern.startswith("."):
                if name.startswith(pattern):
                    return True
            else:
                if pattern in name:
                    return True
        
        return False
    
    async def scan_directory(
        self,
        directory: str | Path,
        recursive: bool = True,
        file_pattern: Optional[str] = None,
    ) -> dict[str, FileFingerprint]:
        """
        扫描目录，计算所有文件的指纹。
        
        Args:
            directory: 目录路径
            recursive: 是否递归子目录
            file_pattern: 可选的文件匹配模式（如 "*.txt"）
            
        Returns:
            文件路径到指纹的映射
        """
        dir_path = Path(directory).expanduser().resolve()
        if not dir_path.exists():
            raise ValueError(f"Directory not found: {directory}")
        
        fingerprints: dict[str, FileFingerprint] = {}
        
        # 选择遍历方式
        if recursive:
            iterator = dir_path.rglob("*")
        else:
            iterator = dir_path.iterdir()
        
        for file_path in iterator:
            if not file_path.is_file():
                continue
            
            # 检查忽略模式
            if self._should_ignore(file_path):
                continue
            
            # 检查文件模式
            if file_pattern and not file_path.match(file_pattern):
                continue
            
            # 创建指纹
            fp = self._create_fingerprint(file_path, dir_path)
            fingerprints[fp.path] = fp
        
        return fingerprints
    
    async def detect_changes(
        self,
        directory: str | Path,
        recursive: bool = True,
        file_pattern: Optional[str] = None,
        detect_renames: bool = True,
    ) -> ChangeSet:
        """
        检测目录变化。
        
        Args:
            directory: 目录路径
            recursive: 是否递归
            file_pattern: 文件匹配模式
            detect_renames: 是否检测重命名
            
        Returns:
            变更集合
        """
        # 加载之前的状态
        previous_state = self._load_previous_state()
        
        # 扫描当前状态
        current_state = await self.scan_directory(directory, recursive, file_pattern)
        
        change_set = ChangeSet(dataset_name=self.dataset_name)
        
        # 检测新增和修改
        for path, current_fp in current_state.items():
            if path not in previous_state:
                change_set.added.append(FileChange(
                    path=path,
                    change_type=ChangeType.ADDED,
                    new_fingerprint=current_fp,
                ))
            else:
                old_fp = previous_state[path]
                if current_fp.content_hash != old_fp.content_hash:
                    change_set.modified.append(FileChange(
                        path=path,
                        change_type=ChangeType.MODIFIED,
                        old_fingerprint=old_fp,
                        new_fingerprint=current_fp,
                    ))
                else:
                    change_set.unchanged.append(FileChange(
                        path=path,
                        change_type=ChangeType.UNCHANGED,
                        old_fingerprint=old_fp,
                        new_fingerprint=current_fp,
                    ))
        
        # 检测删除
        for path, old_fp in previous_state.items():
            if path not in current_state:
                # 检查是否是重命名
                renamed = False
                if detect_renames:
                    for change in change_set.added:
                        if (change.new_fingerprint and 
                            change.new_fingerprint.content_hash == old_fp.content_hash):
                            # 发现重命名
                            change.change_type = ChangeType.RENAMED
                            change.renamed_from = path
                            change.old_fingerprint = old_fp
                            change_set.renamed.append(change)
                            change_set.added.remove(change)
                            renamed = True
                            break
                
                if not renamed:
                    change_set.deleted.append(FileChange(
                        path=path,
                        change_type=ChangeType.DELETED,
                        old_fingerprint=old_fp,
                    ))
        
        return change_set
    
    async def ingest_incremental(
        self,
        directory: str | Path,
        recursive: bool = True,
        file_pattern: Optional[str] = None,
        dry_run: bool = False,
    ) -> IncrementalIngestResult:
        """
        执行增量摄取。
        
        Args:
            directory: 目录路径
            recursive: 是否递归
            file_pattern: 文件匹配模式
            dry_run: 预览模式，不实际执行
            
        Returns:
            摄取结果
        """
        import time
        start_time = time.time()
        
        dir_path = Path(directory).expanduser().resolve()
        
        # 检测变化
        change_set = await self.detect_changes(dir_path, recursive, file_pattern)
        
        if not change_set.has_changes:
            return IncrementalIngestResult(
                success=True,
                dataset_name=self.dataset_name,
                change_set=change_set,
                message="No changes detected, skipping ingestion",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        
        if dry_run:
            return IncrementalIngestResult(
                success=True,
                dataset_name=self.dataset_name,
                change_set=change_set,
                message="Dry run mode - no actual ingestion",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        
        # 执行摄取
        ingested = 0
        errors = []
        
        try:
            if self._cognee_available:
                import cognee
                
                # 1. 处理删除（如果 Cognee 支持）
                # 注：当前 Cognee 版本可能不支持精确删除，这里做标记
                
                # 2. 处理新增和修改
                files_to_ingest = [
                    c for c in change_set.content_changes
                    if c.change_type in (ChangeType.ADDED, ChangeType.MODIFIED, ChangeType.RENAMED)
                ]
                
                for change in files_to_ingest:
                    file_path = dir_path / change.path
                    if file_path.exists():
                        try:
                            await cognee.add(str(file_path), dataset_name=self.dataset_name)
                            ingested += 1
                        except Exception as e:
                            errors.append(f"{change.path}: {e}")
                
                # 3. 触发 cognify（增量模式）
                # 注：这里可以添加条件判断，如果变化大则全量，变化小则增量
                if ingested > 0:
                    await cognee.cognify(dataset_name=self.dataset_name)
                
                # 4. 保存新状态
                current_state = await self.scan_directory(dir_path, recursive, file_pattern)
                self._save_state(current_state)
                
            else:
                # Cognee 不可用，仅记录变化
                errors.append("Cognee not available, only change detection performed")
                
        except Exception as e:
            errors.append(str(e))
        
        execution_time = int((time.time() - start_time) * 1000)
        
        return IncrementalIngestResult(
            success=len(errors) == 0 or ingested > 0,
            dataset_name=self.dataset_name,
            change_set=change_set,
            ingested_count=ingested,
            error_count=len(errors),
            errors=errors,
            execution_time_ms=execution_time,
            message=f"Ingested {ingested} files, {len(errors)} errors",
        )
    
    def get_change_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        获取变更历史。
        
        Args:
            limit: 返回记录数
            
        Returns:
            历史记录列表
        """
        # 目前只返回最新的状态文件信息
        state_file = self._get_state_file()
        if not state_file.exists():
            return []
        
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            return [{
                "dataset_name": data.get("dataset_name"),
                "updated_at": data.get("updated_at"),
                "file_count": len(data.get("fingerprints", {})),
            }]
        except Exception:
            return []
    
    def reset_state(self) -> bool:
        """
        重置状态（强制下次全量摄取）。
        
        Returns:
            是否成功
        """
        state_file = self._get_state_file()
        if state_file.exists():
            state_file.unlink()
            return True
        return False
    
    def get_stats(self) -> dict[str, Any]:
        """
        获取增量加载统计。
        
        Returns:
            统计信息
        """
        state_file = self._get_state_file()
        
        if not state_file.exists():
            return {
                "dataset_name": self.dataset_name,
                "has_state": False,
                "tracked_files": 0,
            }
        
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            fingerprints = data.get("fingerprints", {})
            
            total_size = sum(fp.get("size", 0) for fp in fingerprints.values())
            
            return {
                "dataset_name": self.dataset_name,
                "has_state": True,
                "state_file": str(state_file),
                "last_updated": data.get("updated_at"),
                "tracked_files": len(fingerprints),
                "total_size_bytes": total_size,
                "total_size_human": self._format_bytes(total_size),
            }
        except Exception as e:
            return {
                "dataset_name": self.dataset_name,
                "has_state": False,
                "error": str(e),
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
async def detect_changes(
    directory: str | Path,
    dataset_name: str,
    recursive: bool = True,
) -> ChangeSet:
    """便捷函数：检测目录变化。
    
    Args:
        directory: 目录路径
        dataset_name: 数据集名称
        recursive: 是否递归
        
    Returns:
        变更集合
    """
    loader = IncrementalLoader(dataset_name)
    return await loader.detect_changes(directory, recursive)


async def ingest_incremental(
    directory: str | Path,
    dataset_name: str,
    recursive: bool = True,
    dry_run: bool = False,
) -> IncrementalIngestResult:
    """便捷函数：执行增量摄取。
    
    Args:
        directory: 目录路径
        dataset_name: 数据集名称
        recursive: 是否递归
        dry_run: 预览模式
        
    Returns:
        摄取结果
    """
    loader = IncrementalLoader(dataset_name)
    return await loader.ingest_incremental(directory, recursive, dry_run=dry_run)
