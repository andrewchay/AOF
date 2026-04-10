#!/usr/bin/env python3
"""图谱可视化模块 - 封装 Cognee 的可视化能力。

本模块提供：
- 知识图谱 HTML 可视化生成
- 可视化文件管理
- 交互式图谱查看

使用示例:
    visualizer = GraphVisualizer()
    
    # 生成可视化
    html_path = await visualizer.visualize(
        output_path="visualizations/my_graph.html"
    )
    
    # 为特定主题生成可视化
    html_path = await visualizer.visualize_for_topic(
        topic="my_topic",
        output_dir="visualizations/"
    )
"""

from __future__ import annotations

import importlib.util
import shutil
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class VisualizationResult:
    """可视化结果。"""
    html_path: Path
    topic: Optional[str] = None
    file_size_bytes: int = 0
    preview_url: Optional[str] = None


class GraphVisualizer:
    """Cognee 图谱可视化器。"""
    
    def __init__(self, default_output_dir: Optional[Path] = None):
        self._cognee_available = self._check_cognee()
        self.default_output_dir = default_output_dir or Path("visualizations")
        self.default_output_dir.mkdir(parents=True, exist_ok=True)
    
    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        return importlib.util.find_spec("cognee") is not None
    
    async def visualize(
        self,
        output_path: Optional[Path] = None,
        open_browser: bool = False,
    ) -> VisualizationResult:
        """
        生成知识图谱的 HTML 可视化。
        
        Args:
            output_path: 输出 HTML 文件路径（默认自动生成）
            open_browser: 是否在生成后自动打开浏览器
            
        Returns:
            可视化结果
        """
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        import cognee
        
        # 生成默认路径
        if output_path is None:
            timestamp = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.default_output_dir / f"graph_visualization_{timestamp}.html"
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # 调用 Cognee 生成可视化
            await cognee.visualize_graph(str(output_path))
            
            # 获取文件大小
            file_size = output_path.stat().st_size if output_path.exists() else 0
            
            # 自动打开浏览器
            if open_browser and output_path.exists():
                webbrowser.open(f"file://{output_path.absolute()}")
            
            return VisualizationResult(
                html_path=output_path,
                file_size_bytes=file_size,
                preview_url=f"file://{output_path.absolute()}",
            )
            
        except Exception as e:
            raise RuntimeError(f"生成可视化失败: {e}") from e
    
    async def visualize_for_topic(
        self,
        topic: str,
        output_dir: Optional[Path] = None,
        open_browser: bool = False,
    ) -> VisualizationResult:
        """
        为特定主题生成可视化。
        
        Args:
            topic: 主题名称
            output_dir: 输出目录（默认 visualizations/）
            open_browser: 是否自动打开浏览器
            
        Returns:
            可视化结果
        """
        output_dir = output_dir or self.default_output_dir
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 生成主题特定的文件名
        safe_topic = self._slugify(topic)
        timestamp = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"{safe_topic}_{timestamp}.html"
        
        result = await self.visualize(
            output_path=output_path,
            open_browser=open_browser,
        )
        
        result.topic = topic
        return result

    def create_template_visualization(
        self,
        topic: str,
        template_name: str = "kg_vis_aof_template.html",
        output_dir: Optional[Path] = None,
        open_browser: bool = False,
    ) -> VisualizationResult:
        """
        基于前端模板创建可视化页面（不依赖 Cognee）。

        Args:
            topic: 主题名称
            template_name: 模板文件名（位于 visualization/ 目录）
            output_dir: 输出目录（默认 visualizations/）
            open_browser: 是否自动打开浏览器

        Returns:
            可视化结果
        """
        output_dir = Path(output_dir or self.default_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        safe_topic = self._slugify(topic)
        timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"{safe_topic}_template_{timestamp}.html"

        template_path = Path("visualization") / template_name
        if not template_path.exists():
            raise FileNotFoundError(f"Template not found: {template_path}")

        shutil.copyfile(template_path, output_path)
        file_size = output_path.stat().st_size if output_path.exists() else 0

        if open_browser and output_path.exists():
            webbrowser.open(f"file://{output_path.absolute()}")

        return VisualizationResult(
            html_path=output_path,
            topic=topic,
            file_size_bytes=file_size,
            preview_url=f"file://{output_path.absolute()}",
        )
    
    async def visualize_multi_user(
        self,
        user_dataset_pairs: list[tuple[Any, Any]],
        output_path: Optional[Path] = None,
    ) -> VisualizationResult:
        """
        生成多用户/多数据集的聚合可视化。
        
        Args:
            user_dataset_pairs: [(User, Dataset), ...] 列表
            output_path: 输出路径
            
        Returns:
            可视化结果
        """
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        from cognee.api.v1.visualize.visualize import visualize_multi_user_graph
        
        if output_path is None:
            timestamp = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.default_output_dir / f"multi_user_graph_{timestamp}.html"
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # 调用多用户可视化
            await visualize_multi_user_graph(
                user_dataset_pairs=user_dataset_pairs,
                destination_file_path=str(output_path),
            )
            
            file_size = output_path.stat().st_size if output_path.exists() else 0
            
            return VisualizationResult(
                html_path=output_path,
                file_size_bytes=file_size,
                preview_url=f"file://{output_path.absolute()}",
            )
            
        except Exception as e:
            raise RuntimeError(f"生成多用户可视化失败: {e}") from e
    
    def list_visualizations(self, topic: Optional[str] = None) -> list[Path]:
        """
        列出所有可视化文件。
        
        Args:
            topic: 可选，筛选特定主题
            
        Returns:
            HTML 文件路径列表
        """
        if not self.default_output_dir.exists():
            return []
        
        pattern = "*.html"
        if topic:
            safe_topic = self._slugify(topic)
            pattern = f"{safe_topic}_*.html"
        
        files = sorted(
            self.default_output_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        
        return files
    
    def get_latest_visualization(self, topic: Optional[str] = None) -> Optional[Path]:
        """
        获取最新的可视化文件。
        
        Args:
            topic: 可选，特定主题
            
        Returns:
            最新的 HTML 文件路径，或 None
        """
        files = self.list_visualizations(topic)
        return files[0] if files else None
    
    def open_visualization(self, path: Path) -> bool:
        """
        在浏览器中打开可视化文件。
        
        Args:
            path: HTML 文件路径
            
        Returns:
            是否成功打开
        """
        path = Path(path)
        if not path.exists():
            return False
        
        try:
            webbrowser.open(f"file://{path.absolute()}")
            return True
        except Exception:
            return False
    
    def delete_visualization(self, path: Path) -> bool:
        """
        删除可视化文件。
        
        Args:
            path: HTML 文件路径
            
        Returns:
            是否成功删除
        """
        path = Path(path)
        if not path.exists():
            return False
        
        try:
            path.unlink()
            return True
        except Exception:
            return False
    
    def cleanup_old_visualizations(
        self,
        keep_count: int = 10,
        topic: Optional[str] = None,
    ) -> int:
        """
        清理旧的可视化文件，只保留最新的 N 个。
        
        Args:
            keep_count: 保留的文件数量
            topic: 可选，仅清理特定主题
            
        Returns:
            删除的文件数量
        """
        files = self.list_visualizations(topic)
        
        if len(files) <= keep_count:
            return 0
        
        deleted = 0
        for old_file in files[keep_count:]:
            try:
                old_file.unlink()
                deleted += 1
            except Exception:
                pass
        
        return deleted
    
    @staticmethod
    def _slugify(text: str) -> str:
        """将文本转换为安全文件名。"""
        import re
        text = text.strip().lower()
        text = re.sub(r"[^a-z0-9_\-]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        return text or "untitled"
    
    def get_visualization_info(self, path: Path) -> dict[str, Any]:
        """
        获取可视化文件的详细信息。
        
        Args:
            path: HTML 文件路径
            
        Returns:
            文件信息字典
        """
        path = Path(path)
        
        if not path.exists():
            return {"exists": False}
        
        stat = path.stat()
        
        return {
            "exists": True,
            "path": str(path),
            "filename": path.name,
            "size_bytes": stat.st_size,
            "size_human": self._format_bytes(stat.st_size),
            "created": __import__('datetime').datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "modified": __import__('datetime').datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "url": f"file://{path.absolute()}",
        }
    
    @staticmethod
    def _format_bytes(size_bytes: int) -> str:
        """格式化字节大小为人类可读。"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.2f} TB"


# 便捷函数
async def quick_visualize(
    output_path: Optional[str] = None,
    open_browser: bool = True,
) -> Path:
    """快速生成并打开可视化。
    
    Args:
        output_path: 输出路径（可选）
        open_browser: 是否自动打开浏览器
        
    Returns:
        HTML 文件路径
    """
    visualizer = GraphVisualizer()
    result = await visualizer.visualize(
        output_path=Path(output_path) if output_path else None,
        open_browser=open_browser,
    )
    return result.html_path


async def visualize_topic(
    topic: str,
    output_dir: str = "visualizations",
) -> Path:
    """为主题生成可视化。
    
    Args:
        topic: 主题名称
        output_dir: 输出目录
        
    Returns:
        HTML 文件路径
    """
    visualizer = GraphVisualizer()
    result = await visualizer.visualize_for_topic(
        topic=topic,
        output_dir=Path(output_dir),
    )
    return result.html_path
