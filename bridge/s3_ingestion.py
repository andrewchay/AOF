#!/usr/bin/env python3
"""S3 云存储摄取模块 - 支持从 AWS S3 摄取数据。

本模块提供：
- S3 桶列表和浏览
- 单文件/多文件下载
- 前缀过滤和模式匹配
- 增量同步（基于 ETag/LastModified）
- 流式处理（支持大文件）
- 支持 S3-compatible 服务（MinIO, Ceph, etc.）

使用示例:
    # 摄取单个文件
    result = await ingest_s3_file(
        bucket="my-bucket",
        key="documents/report.pdf",
    )
    
    # 批量摄取前缀
    results = await ingest_s3_prefix(
        bucket="my-bucket",
        prefix="data/2024/",
        pattern="*.json",
    )
"""

from __future__ import annotations

import asyncio
import mimetypes
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


@dataclass
class S3Config:
    """S3 配置。"""
    endpoint_url: Optional[str] = None  # 自定义端点（用于 MinIO 等）
    region: str = "us-east-1"
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    session_token: Optional[str] = None  # 临时凭证
    use_ssl: bool = True
    verify_ssl: bool = True
    
    @classmethod
    def from_env(cls) -> S3Config:
        """从环境变量创建配置。"""
        import os
        return cls(
            endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
            region=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")),
            access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
            secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            session_token=os.environ.get("AWS_SESSION_TOKEN"),
        )


@dataclass
class S3Object:
    """S3 对象信息。"""
    bucket: str
    key: str
    size: int
    last_modified: datetime
    etag: str
    content_type: Optional[str] = None
    
    @property
    def filename(self) -> str:
        """获取文件名。"""
        return self.key.split("/")[-1] if "/" in self.key else self.key
    
    @property
    def extension(self) -> str:
        """获取文件扩展名。"""
        name = self.filename
        if "." in name:
            return name.split(".")[-1]
        return ""


@dataclass
class S3IngestResult:
    """S3 摄取结果。"""
    bucket: str
    key: str
    success: bool
    local_path: Optional[Path] = None
    size: int = 0
    content_type: Optional[str] = None
    dataset_name: Optional[str] = None
    error: Optional[str] = None
    download_time_ms: int = 0


@dataclass
class S3PrefixIngestResult:
    """S3 前缀批量摄取结果。"""
    bucket: str
    prefix: str
    total_objects: int
    successful: list[S3IngestResult] = field(default_factory=list)
    failed: list[S3IngestResult] = field(default_factory=list)
    
    @property
    def success_count(self) -> int:
        return len(self.successful)
    
    @property
    def failed_count(self) -> int:
        return len(self.failed)
    
    @property
    def total_bytes(self) -> int:
        return sum(r.size for r in self.successful)


class S3Ingester:
    """S3 摄取器。"""
    
    # 支持的文件扩展名
    SUPPORTED_EXTENSIONS = {
        # 文档
        ".txt", ".md", ".markdown", ".rst", ".doc", ".docx", ".pdf",
        # 数据
        ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".xml",
        # 代码
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
        ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala",
        # 网页
        ".html", ".htm", ".css",
        # 配置
        ".toml", ".ini", ".cfg", ".conf",
    }
    
    # 最大文件大小（100MB）
    MAX_FILE_SIZE = 100 * 1024 * 1024
    
    # 流式下载阈值（10MB）
    STREAMING_THRESHOLD = 10 * 1024 * 1024
    
    def __init__(
        self,
        config: Optional[S3Config] = None,
        temp_dir: Optional[Path] = None,
    ):
        self.config = config or S3Config.from_env()
        self.temp_dir = temp_dir or Path(tempfile.gettempdir()) / "aof_s3_ingest"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._s3_client = None
        self._cognee_available = self._check_cognee()
        self._history: list[dict[str, Any]] = []
    
    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        try:
            import cognee
            return True
        except ImportError:
            return False
    
    def _get_s3_client(self):
        """获取 S3 客户端（延迟初始化）。"""
        if self._s3_client is None:
            try:
                import boto3
                from botocore.config import Config
                
                # 配置 boto3
                session_kwargs = {
                    "region_name": self.config.region,
                }
                
                if self.config.access_key and self.config.secret_key:
                    session_kwargs["aws_access_key_id"] = self.config.access_key
                    session_kwargs["aws_secret_access_key"] = self.config.secret_key
                    if self.config.session_token:
                        session_kwargs["aws_session_token"] = self.config.session_token
                
                session = boto3.Session(**session_kwargs)
                
                # 客户端配置
                client_kwargs = {
                    "config": Config(
                        max_pool_connections=25,
                        retries={"max_attempts": 3},
                    ),
                    "verify": self.config.verify_ssl,
                }
                
                # 自定义端点（S3-compatible 服务）
                if self.config.endpoint_url:
                    client_kwargs["endpoint_url"] = self.config.endpoint_url
                    client_kwargs["use_ssl"] = self.config.use_ssl
                
                self._s3_client = session.client("s3", **client_kwargs)
                
            except ImportError:
                raise ImportError(
                    "boto3 is required for S3 ingestion. "
                    "Install with: pip install boto3"
                )
        
        return self._s3_client
    
    def _is_supported_file(self, key: str) -> bool:
        """检查文件类型是否支持。"""
        ext = Path(key).suffix.lower()
        return ext in self.SUPPORTED_EXTENSIONS
    
    def _get_local_path(self, bucket: str, key: str) -> Path:
        """生成本地文件路径。"""
        # 清理 key 中的非法字符
        safe_key = key.replace("/", "_").replace("\\", "_")
        if len(safe_key) > 200:
            safe_key = safe_key[:200]
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{bucket}_{safe_key}_{timestamp}"
        
        return self.temp_dir / filename
    
    async def list_buckets(self) -> list[str]:
        """
        列出所有可访问的 S3 桶。
        
        Returns:
            桶名称列表
        """
        try:
            client = self._get_s3_client()
            response = client.list_buckets()
            return [b["Name"] for b in response.get("Buckets", [])]
        except Exception as e:
            raise Exception(f"Failed to list buckets: {e}")
    
    async def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        pattern: Optional[str] = None,
        max_keys: int = 1000,
    ) -> list[S3Object]:
        """
        列出 S3 对象。
        
        Args:
            bucket: S3 桶名称
            prefix: 前缀过滤
            pattern: 文件名匹配模式（如 "*.json"）
            max_keys: 最大返回数量
            
        Returns:
            S3 对象列表
        """
        try:
            client = self._get_s3_client()
            
            objects = []
            paginator = client.get_paginator("list_objects_v2")
            
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    
                    # 跳过目录
                    if key.endswith("/"):
                        continue
                    
                    # 模式匹配
                    if pattern and not self._match_pattern(key, pattern):
                        continue
                    
                    # 文件类型过滤
                    if not self._is_supported_file(key):
                        continue
                    
                    objects.append(S3Object(
                        bucket=bucket,
                        key=key,
                        size=obj["Size"],
                        last_modified=obj["LastModified"],
                        etag=obj["ETag"].strip('"'),
                        content_type=mimetypes.guess_type(key)[0],
                    ))
                    
                    if len(objects) >= max_keys:
                        break
                
                if len(objects) >= max_keys:
                    break
            
            return objects[:max_keys]
            
        except Exception as e:
            raise Exception(f"Failed to list objects: {e}")
    
    def _match_pattern(self, key: str, pattern: str) -> bool:
        """匹配文件名模式。"""
        import fnmatch
        filename = key.split("/")[-1]
        return fnmatch.fnmatch(filename, pattern)
    
    async def ingest_file(
        self,
        bucket: str,
        key: str,
        dataset_name: Optional[str] = None,
        stream_threshold: int = STREAMING_THRESHOLD,
    ) -> S3IngestResult:
        """
        摄取单个 S3 文件。
        
        Args:
            bucket: S3 桶名称
            key: 文件路径
            dataset_name: 数据集名称
            stream_threshold: 流式下载阈值
            
        Returns:
            摄取结果
        """
        import time
        start_time = time.time()
        
        try:
            client = self._get_s3_client()
            
            # 获取文件信息
            head_response = client.head_object(Bucket=bucket, Key=key)
            file_size = head_response["ContentLength"]
            content_type = head_response.get("ContentType")
            
            # 检查文件大小
            if file_size > self.MAX_FILE_SIZE:
                return S3IngestResult(
                    bucket=bucket,
                    key=key,
                    success=False,
                    size=file_size,
                    content_type=content_type,
                    error=f"File too large: {file_size} bytes (max {self.MAX_FILE_SIZE})",
                )
            
            # 确定本地路径
            local_path = self._get_local_path(bucket, key)
            
            # 下载文件
            if file_size > stream_threshold:
                # 流式下载（大文件）
                await self._download_streaming(client, bucket, key, local_path)
            else:
                # 直接下载（小文件）
                response = client.get_object(Bucket=bucket, Key=key)
                local_path.write_bytes(response["Body"].read())
            
            download_time = int((time.time() - start_time) * 1000)
            
            # 生成数据集名称
            if dataset_name is None:
                dataset_name = f"s3_{bucket}_{key.split('/')[0] if '/' in key else 'root'}"
                dataset_name = "".join(c if c.isalnum() or c == "_" else "_" for c in dataset_name)
            
            # 摄取到 Cognee
            if self._cognee_available:
                import cognee
                await cognee.add(str(local_path), dataset_name=dataset_name)
            
            # 记录历史
            result = S3IngestResult(
                bucket=bucket,
                key=key,
                success=True,
                local_path=local_path,
                size=file_size,
                content_type=content_type,
                dataset_name=dataset_name,
                download_time_ms=download_time,
            )
            self._record_history(result)
            
            return result
            
        except Exception as e:
            return S3IngestResult(
                bucket=bucket,
                key=key,
                success=False,
                error=str(e),
            )
    
    async def _download_streaming(
        self,
        client,
        bucket: str,
        key: str,
        local_path: Path,
        chunk_size: int = 8192,
    ) -> None:
        """流式下载大文件。"""
        response = client.get_object(Bucket=bucket, Key=key)
        
        with open(local_path, "wb") as f:
            for chunk in response["Body"].iter_chunks(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
    
    async def ingest_prefix(
        self,
        bucket: str,
        prefix: str = "",
        pattern: Optional[str] = None,
        dataset_name: Optional[str] = None,
        max_concurrent: int = 5,
        max_files: int = 100,
    ) -> S3PrefixIngestResult:
        """
        批量摄取前缀下的所有文件。
        
        Args:
            bucket: S3 桶名称
            prefix: 前缀路径
            pattern: 文件名匹配模式
            dataset_name: 数据集名称
            max_concurrent: 最大并发数
            max_files: 最大文件数
            
        Returns:
            批量摄取结果
        """
        # 列出对象
        objects = await self.list_objects(bucket, prefix, pattern, max_files)
        
        if not objects:
            return S3PrefixIngestResult(
                bucket=bucket,
                prefix=prefix,
                total_objects=0,
            )
        
        # 并发下载
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def ingest_with_limit(obj: S3Object) -> S3IngestResult:
            async with semaphore:
                return await self.ingest_file(obj.bucket, obj.key, dataset_name)
        
        tasks = [ingest_with_limit(obj) for obj in objects]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
        successful = []
        failed = []
        
        for obj, result in zip(objects, results):
            if isinstance(result, Exception):
                failed.append(S3IngestResult(
                    bucket=obj.bucket,
                    key=obj.key,
                    success=False,
                    error=str(result),
                ))
            elif result.success:
                successful.append(result)
            else:
                failed.append(result)
        
        return S3PrefixIngestResult(
            bucket=bucket,
            prefix=prefix,
            total_objects=len(objects),
            successful=successful,
            failed=failed,
        )
    
    async def sync_prefix(
        self,
        bucket: str,
        prefix: str = "",
        pattern: Optional[str] = None,
        dataset_name: Optional[str] = None,
        incremental: bool = True,
    ) -> S3PrefixIngestResult:
        """
        同步前缀（支持增量同步）。
        
        Args:
            bucket: S3 桶名称
            prefix: 前缀路径
            pattern: 文件名匹配模式
            dataset_name: 数据集名称
            incremental: 是否只同步变化的文件
            
        Returns:
            同步结果
        """
        # 加载上次同步状态
        state = self._load_sync_state(bucket, prefix) if incremental else {}
        
        # 列出当前对象
        objects = await self.list_objects(bucket, prefix, pattern, max_keys=10000)
        
        # 筛选需要同步的文件
        to_sync = []
        for obj in objects:
            key = obj.key
            if key not in state:
                # 新增文件
                to_sync.append(obj)
            elif state[key]["etag"] != obj.etag:
                # 文件已修改
                to_sync.append(obj)
        
        # 检测删除的文件
        current_keys = {obj.key for obj in objects}
        deleted_keys = set(state.keys()) - current_keys
        
        if not to_sync and not deleted_keys:
            return S3PrefixIngestResult(
                bucket=bucket,
                prefix=prefix,
                total_objects=0,
            )
        
        # 同步文件
        results = []
        for obj in to_sync:
            result = await self.ingest_file(obj.bucket, obj.key, dataset_name)
            results.append(result)
            
            # 更新状态
            if result.success:
                state[obj.key] = {
                    "etag": obj.etag,
                    "last_modified": obj.last_modified.isoformat(),
                    "size": obj.size,
                }
        
        # 移除已删除的文件状态
        for key in deleted_keys:
            del state[key]
        
        # 保存状态
        if incremental:
            self._save_sync_state(bucket, prefix, state)
        
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        
        return S3PrefixIngestResult(
            bucket=bucket,
            prefix=prefix,
            total_objects=len(to_sync),
            successful=successful,
            failed=failed,
        )
    
    def _get_state_file(self, bucket: str, prefix: str) -> Path:
        """获取同步状态文件路径。"""
        safe_name = f"{bucket}_{prefix}".replace("/", "_").replace("\\", "_")
        if len(safe_name) > 100:
            import hashlib
            safe_name = hashlib.md5(safe_name.encode()).hexdigest()[:16]
        return self.temp_dir / f"sync_state_{safe_name}.json"
    
    def _load_sync_state(self, bucket: str, prefix: str) -> dict[str, Any]:
        """加载同步状态。"""
        state_file = self._get_state_file(bucket, prefix)
        if not state_file.exists():
            return {}
        try:
            import json
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
    
    def _save_sync_state(self, bucket: str, prefix: str, state: dict[str, Any]) -> None:
        """保存同步状态。"""
        state_file = self._get_state_file(bucket, prefix)
        import json
        state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    
    def _record_history(self, result: S3IngestResult) -> None:
        """记录摄取历史。"""
        self._history.append({
            "timestamp": datetime.now().isoformat(),
            "bucket": result.bucket,
            "key": result.key,
            "success": result.success,
            "size": result.size,
            "dataset_name": result.dataset_name,
            "error": result.error,
        })
        
        if len(self._history) > 1000:
            self._history = self._history[-1000:]
    
    def get_history(
        self,
        limit: int = 50,
        bucket: Optional[str] = None,
        success_only: bool = False,
    ) -> list[dict[str, Any]]:
        """
        获取摄取历史。
        
        Args:
            limit: 返回记录数
            bucket: 按桶筛选
            success_only: 只返回成功的
            
        Returns:
            历史记录
        """
        history = self._history
        
        if bucket:
            history = [h for h in history if h.get("bucket") == bucket]
        
        if success_only:
            history = [h for h in history if h.get("success")]
        
        return history[-limit:]
    
    def get_statistics(self) -> dict[str, Any]:
        """
        获取统计信息。
        
        Returns:
            统计信息
        """
        total = len(self._history)
        successful = len([h for h in self._history if h.get("success")])
        failed = total - successful
        
        total_bytes = sum(h.get("size", 0) for h in self._history if h.get("success"))
        
        # 按桶统计
        by_bucket: dict[str, dict] = {}
        for h in self._history:
            if h.get("success"):
                b = h.get("bucket", "unknown")
                if b not in by_bucket:
                    by_bucket[b] = {"count": 0, "total_size": 0}
                by_bucket[b]["count"] += 1
                by_bucket[b]["total_size"] += h.get("size", 0)
        
        return {
            "total_ingested": total,
            "successful": successful,
            "failed": failed,
            "success_rate": successful / total if total > 0 else 0,
            "total_bytes": total_bytes,
            "total_bytes_human": self._format_bytes(total_bytes),
            "by_bucket": by_bucket,
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
async def ingest_s3_file(
    bucket: str,
    key: str,
    dataset_name: Optional[str] = None,
    config: Optional[S3Config] = None,
) -> S3IngestResult:
    """便捷函数：摄取单个 S3 文件。
    
    Args:
        bucket: S3 桶名称
        key: 文件路径
        dataset_name: 数据集名称
        config: S3 配置
        
    Returns:
        摄取结果
    """
    ingester = S3Ingester(config)
    return await ingester.ingest_file(bucket, key, dataset_name)


async def ingest_s3_prefix(
    bucket: str,
    prefix: str = "",
    pattern: Optional[str] = None,
    dataset_name: Optional[str] = None,
    config: Optional[S3Config] = None,
) -> S3PrefixIngestResult:
    """便捷函数：批量摄取 S3 前缀。
    
    Args:
        bucket: S3 桶名称
        prefix: 前缀路径
        pattern: 文件名匹配模式
        dataset_name: 数据集名称
        config: S3 配置
        
    Returns:
        批量摄取结果
    """
    ingester = S3Ingester(config)
    return await ingester.ingest_prefix(bucket, prefix, pattern, dataset_name)


async def sync_s3_prefix(
    bucket: str,
    prefix: str = "",
    pattern: Optional[str] = None,
    dataset_name: Optional[str] = None,
    config: Optional[S3Config] = None,
) -> S3PrefixIngestResult:
    """便捷函数：同步 S3 前缀（增量）。
    
    Args:
        bucket: S3 桶名称
        prefix: 前缀路径
        pattern: 文件名匹配模式
        dataset_name: 数据集名称
        config: S3 配置
        
    Returns:
        同步结果
    """
    ingester = S3Ingester(config)
    return await ingester.sync_prefix(bucket, prefix, pattern, dataset_name)
