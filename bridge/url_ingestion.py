#!/usr/bin/env python3
"""URL 网络数据摄取模块 - 支持从 URL 下载并摄取内容。

本模块提供：
- URL 内容下载
- 自动 MIME 类型检测
- 内容提取和清洗
- 批量 URL 摄取
- 下载历史管理

使用示例:
    # 摄取单个 URL
    result = await ingest_url("https://example.com/article")
    
    # 批量摄取
    results = await ingest_urls([
        "https://example.com/page1",
        "https://example.com/page2"
    ])
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


@dataclass
class URLIngestionResult:
    """URL 摄取结果。"""
    url: str
    success: bool
    content_type: Optional[str] = None
    content_length: int = 0
    title: Optional[str] = None
    dataset_name: Optional[str] = None
    local_path: Optional[Path] = None
    error: Optional[str] = None
    download_time_ms: int = 0
    processing_time_ms: int = 0


@dataclass
class URLContent:
    """URL 内容信息。"""
    url: str
    content: bytes
    content_type: str
    encoding: str = "utf-8"
    headers: dict[str, str] = field(default_factory=dict)


class URLIngester:
    """URL 数据摄取器。"""
    
    # 支持的 MIME 类型
    SUPPORTED_MIME_TYPES = {
        # 文本
        "text/plain", "text/html", "text/markdown", "text/csv",
        # 文档
        "application/pdf", "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        # 数据
        "application/json", "application/jsonl", "application/yaml",
        "application/xml", "text/xml",
        # 代码
        "text/x-python", "text/javascript", "application/javascript",
    }
    
    # 最大下载大小（50MB）
    MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024
    
    # 请求超时（秒）
    DEFAULT_TIMEOUT = 30
    
    def __init__(self, temp_dir: Optional[Path] = None):
        self._cognee_available = self._check_cognee()
        self.temp_dir = temp_dir or Path(tempfile.gettempdir()) / "aof_url_ingest"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._history: list[dict[str, Any]] = []
    
    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        try:
            import cognee
            return True
        except ImportError:
            return False
    
    async def ingest(
        self,
        url: str,
        dataset_name: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        headers: Optional[dict[str, str]] = None,
    ) -> URLIngestionResult:
        """
        摄取单个 URL。
        
        Args:
            url: 目标 URL
            dataset_name: 数据集名称（默认从 URL 生成）
            timeout: 下载超时（秒）
            headers: 自定义请求头
            
        Returns:
            摄取结果
        """
        import time
        
        start_time = time.time()
        
        if not self._is_valid_url(url):
            return URLIngestionResult(
                url=url,
                success=False,
                error="Invalid URL format"
            )
        
        # 生成数据集名称
        if dataset_name is None:
            dataset_name = self._generate_dataset_name(url)
        
        try:
            # 下载内容
            content = await self._download_url(url, timeout, headers)
            download_time = int((time.time() - start_time) * 1000)
            
            if content is None:
                return URLIngestionResult(
                    url=url,
                    success=False,
                    error="Failed to download content"
                )
            
            # 检查 MIME 类型
            if not self._is_supported_content(content.content_type):
                return URLIngestionResult(
                    url=url,
                    success=False,
                    content_type=content.content_type,
                    error=f"Unsupported content type: {content.content_type}"
                )
            
            # 保存到临时文件
            local_path = self._save_to_temp(content, url)
            
            # 摄取到 Cognee
            if self._cognee_available:
                await self._ingest_to_cognee(local_path, dataset_name)
            
            processing_time = int((time.time() - start_time) * 1000)
            
            # 记录历史
            result = URLIngestionResult(
                url=url,
                success=True,
                content_type=content.content_type,
                content_length=len(content.content),
                title=self._extract_title(content),
                dataset_name=dataset_name,
                local_path=local_path,
                download_time_ms=download_time,
                processing_time_ms=processing_time,
            )
            
            self._record_history(result)
            return result
            
        except Exception as e:
            return URLIngestionResult(
                url=url,
                success=False,
                error=str(e)
            )
    
    async def ingest_batch(
        self,
        urls: list[str],
        dataset_name: Optional[str] = None,
        max_concurrent: int = 5,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> list[URLIngestionResult]:
        """
        批量摄取多个 URL。
        
        Args:
            urls: URL 列表
            dataset_name: 数据集名称（所有 URL 共享）
            max_concurrent: 最大并发数
            timeout: 每个 URL 的超时时间
            
        Returns:
            摄取结果列表
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def ingest_with_limit(url: str) -> URLIngestionResult:
            async with semaphore:
                return await self.ingest(url, dataset_name, timeout)
        
        tasks = [ingest_with_limit(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理异常
        final_results = []
        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                final_results.append(URLIngestionResult(
                    url=url,
                    success=False,
                    error=str(result)
                ))
            else:
                final_results.append(result)
        
        return final_results
    
    async def _download_url(
        self,
        url: str,
        timeout: int,
        custom_headers: Optional[dict[str, str]] = None,
    ) -> Optional[URLContent]:
        """下载 URL 内容。"""
        try:
            import aiohttp
            
            headers = {
                "User-Agent": "AOF-URL-Ingester/1.0",
                "Accept": "*/*",
            }
            
            if custom_headers:
                headers.update(custom_headers)
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                    if response.status != 200:
                        raise Exception(f"HTTP {response.status}")
                    
                    # 检查内容大小
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self.MAX_DOWNLOAD_SIZE:
                        raise Exception(f"Content too large: {content_length} bytes")
                    
                    # 读取内容
                    content = await response.read()
                    
                    if len(content) > self.MAX_DOWNLOAD_SIZE:
                        raise Exception(f"Content too large: {len(content)} bytes")
                    
                    # 获取内容类型
                    content_type = response.headers.get("Content-Type", "application/octet-stream")
                    # 移除 charset 等后缀
                    content_type = content_type.split(";")[0].strip()
                    
                    # 检测编码
                    encoding = "utf-8"
                    if "charset=" in response.headers.get("Content-Type", ""):
                        encoding = response.headers.get("Content-Type").split("charset=")[-1].strip()
                    
                    return URLContent(
                        url=url,
                        content=content,
                        content_type=content_type,
                        encoding=encoding,
                        headers=dict(response.headers),
                    )
                    
        except Exception as e:
            raise Exception(f"Download failed: {e}")
    
    def _is_valid_url(self, url: str) -> bool:
        """验证 URL 格式。"""
        try:
            result = urlparse(url)
            return all([result.scheme in ("http", "https"), result.netloc])
        except Exception:
            return False
    
    def _is_supported_content(self, content_type: str) -> bool:
        """检查是否支持的内容类型。"""
        # 检查精确匹配
        if content_type in self.SUPPORTED_MIME_TYPES:
            return True
        
        # 检查通配符 (如 text/*)
        main_type = content_type.split("/")[0] if "/" in content_type else ""
        if f"{main_type}/*" in self.SUPPORTED_MIME_TYPES:
            return True
        
        # 一些常见扩展
        if content_type in ("text/x-markdown", "text/md"):
            return True
        
        return False
    
    def _generate_dataset_name(self, url: str) -> str:
        """从 URL 生成数据集名称。"""
        parsed = urlparse(url)
        
        # 使用域名 + 路径哈希
        domain = parsed.netloc.replace(".", "_")
        path_hash = hashlib.md5(parsed.path.encode()).hexdigest()[:8]
        
        timestamp = datetime.now().strftime("%Y%m%d")
        
        return f"{domain}_{path_hash}_{timestamp}"
    
    def _save_to_temp(self, content: URLContent, original_url: str) -> Path:
        """保存内容到临时文件。"""
        # 生成文件名
        url_hash = hashlib.md5(original_url.encode()).hexdigest()[:12]
        
        # 确定扩展名
        ext = self._get_extension(content.content_type, original_url)
        filename = f"url_{url_hash}{ext}"
        
        file_path = self.temp_dir / filename
        
        # 写入文件
        file_path.write_bytes(content.content)
        
        return file_path
    
    def _get_extension(self, content_type: str, url: str) -> str:
        """根据内容类型获取文件扩展名。"""
        # 尝试从 URL 获取
        parsed = urlparse(url)
        path = parsed.path
        if "." in path:
            ext = path.split(".")[-1]
            if ext and len(ext) < 10:
                return f".{ext}"
        
        # 从 MIME 类型映射
        ext = mimetypes.guess_extension(content_type)
        if ext:
            return ext
        
        # 默认
        return ".txt"
    
    async def _ingest_to_cognee(self, file_path: Path, dataset_name: str) -> None:
        """摄取到 Cognee。"""
        if not self._cognee_available:
            return
        
        import cognee
        
        await cognee.add(str(file_path), dataset_name=dataset_name)
    
    def _extract_title(self, content: URLContent) -> Optional[str]:
        """从内容中提取标题。"""
        if content.content_type in ("text/html", "text/xhtml"):
            try:
                content_str = content.content.decode(content.encoding, errors="ignore")
                
                # 简单的标题提取
                import re
                title_match = re.search(r"<title[^>]*>(.*?)</title>", content_str, re.IGNORECASE | re.DOTALL)
                if title_match:
                    return title_match.group(1).strip()
                
                # 尝试 h1
                h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", content_str, re.IGNORECASE | re.DOTALL)
                if h1_match:
                    return h1_match.group(1).strip()
                    
            except Exception:
                pass
        
        return None
    
    def _record_history(self, result: URLIngestionResult) -> None:
        """记录摄取历史。"""
        self._history.append({
            "timestamp": datetime.now().isoformat(),
            "url": result.url,
            "success": result.success,
            "dataset_name": result.dataset_name,
            "content_type": result.content_type,
            "content_length": result.content_length,
            "error": result.error,
        })
        
        # 只保留最近 1000 条
        if len(self._history) > 1000:
            self._history = self._history[-1000:]
    
    def get_history(
        self,
        limit: int = 50,
        success_only: bool = False,
    ) -> list[dict[str, Any]]:
        """
        获取摄取历史。
        
        Args:
            limit: 返回记录数量
            success_only: 只返回成功的记录
            
        Returns:
            历史记录列表
        """
        history = self._history
        
        if success_only:
            history = [h for h in history if h.get("success")]
        
        return history[-limit:]
    
    def get_statistics(self) -> dict[str, Any]:
        """
        获取摄取统计信息。
        
        Returns:
            统计信息
        """
        total = len(self._history)
        successful = len([h for h in self._history if h.get("success")])
        failed = total - successful
        
        # 按内容类型统计
        by_type: dict[str, int] = {}
        for h in self._history:
            if h.get("success"):
                content_type = h.get("content_type", "unknown")
                by_type[content_type] = by_type.get(content_type, 0) + 1
        
        return {
            "total_ingested": total,
            "successful": successful,
            "failed": failed,
            "success_rate": successful / total if total > 0 else 0,
            "by_content_type": by_type,
        }
    
    def cleanup_temp_files(self, max_age_hours: int = 24) -> int:
        """
        清理临时文件。
        
        Args:
            max_age_hours: 最大保留时间（小时）
            
        Returns:
            删除的文件数量
        """
        import time
        
        cutoff = time.time() - (max_age_hours * 3600)
        deleted = 0
        
        for file_path in self.temp_dir.glob("url_*"):
            try:
                if file_path.stat().st_mtime < cutoff:
                    file_path.unlink()
                    deleted += 1
            except Exception:
                pass
        
        return deleted


# 便捷函数
async def ingest_url(
    url: str,
    dataset_name: Optional[str] = None,
    timeout: int = 30,
) -> URLIngestionResult:
    """便捷函数：摄取单个 URL。
    
    Args:
        url: 目标 URL
        dataset_name: 数据集名称
        timeout: 超时时间（秒）
        
    Returns:
        摄取结果
    """
    ingester = URLIngester()
    return await ingester.ingest(url, dataset_name, timeout)


async def ingest_urls(
    urls: list[str],
    dataset_name: Optional[str] = None,
    max_concurrent: int = 5,
) -> list[URLIngestionResult]:
    """便捷函数：批量摄取多个 URL。
    
    Args:
        urls: URL 列表
        dataset_name: 数据集名称
        max_concurrent: 最大并发数
        
    Returns:
        摄取结果列表
    """
    ingester = URLIngester()
    return await ingester.ingest_batch(urls, dataset_name, max_concurrent)


def validate_url(url: str) -> bool:
    """便捷函数：验证 URL 格式。
    
    Args:
        url: URL 字符串
        
    Returns:
        是否有效
    """
    ingester = URLIngester()
    return ingester._is_valid_url(url)
