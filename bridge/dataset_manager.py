#!/usr/bin/env python3
"""数据集管理模块 - 封装 Cognee 的数据集管理能力。

本模块提供：
- 数据集列表查看
- 数据集状态检查
- 数据删除和清空
- 数据存在性检查

使用示例:
    manager = DatasetManager()
    
    # 列出所有数据集
    datasets = await manager.list_datasets()
    
    # 检查数据集状态
    status = await manager.get_dataset_status("dataset_id")
    
    # 删除数据
    await manager.delete_data(dataset_id="...", data_id="...")
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID


@dataclass
class DatasetInfo:
    """数据集信息。"""
    id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    data_count: int = 0
    status: str = "unknown"  # pending, processing, completed, error


@dataclass
class DatasetStatus:
    """数据集处理状态。"""
    dataset_id: str
    pipeline_name: str
    status: str  # pending, running, completed, failed
    progress: float = 0.0  # 0-100
    message: Optional[str] = None
    last_updated: Optional[datetime] = None


@dataclass
class DataItem:
    """数据项信息。"""
    id: str
    name: str
    mime_type: Optional[str] = None
    created_at: Optional[datetime] = None
    raw_data_location: Optional[str] = None


class DatasetManager:
    """Cognee 数据集管理器。"""
    
    def __init__(self):
        self._cognee_available = self._check_cognee()
    
    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        return importlib.util.find_spec("cognee") is not None
    
    async def list_datasets(self, user: Optional[Any] = None) -> list[DatasetInfo]:
        """
        列出所有可访问的数据集。
        
        Args:
            user: 用户对象（可选，默认使用默认用户）
            
        Returns:
            数据集信息列表
        """
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        from cognee.modules.users.methods import get_default_user
        
        if user is None:
            user = await get_default_user()
        
        try:
            # 获取授权的数据集列表
            from cognee.modules.data.methods import get_authorized_existing_datasets
            datasets = await get_authorized_existing_datasets([], "read", user)
            
            result = []
            for dataset in datasets:
                # 获取数据集的数据项数量
                data_count = await self._get_dataset_data_count(dataset.id, user)
                
                result.append(DatasetInfo(
                    id=str(dataset.id),
                    name=dataset.name,
                    description=getattr(dataset, "description", None),
                    created_at=getattr(dataset, "created_at", None),
                    data_count=data_count,
                    status=await self._get_dataset_quick_status(dataset.id),
                ))
            
            return result
            
        except Exception as e:
            raise RuntimeError(f"获取数据集列表失败: {e}") from e
    
    async def _get_dataset_data_count(self, dataset_id: UUID, user: Any) -> int:
        """获取数据集的数据项数量。"""
        try:
            from cognee.modules.data.methods import get_dataset_data
            data_items = await get_dataset_data(dataset_id)
            return len(data_items) if data_items else 0
        except Exception:
            return 0
    
    async def _get_dataset_quick_status(self, dataset_id: UUID) -> str:
        """快速获取数据集状态（不查询详细管道状态）。"""
        try:
            from cognee.modules.data.methods import has_dataset_data
            has_data = await has_dataset_data(dataset_id)
            return "has_data" if has_data else "empty"
        except Exception:
            return "unknown"
    
    async def get_dataset_status(
        self,
        dataset_id: str | UUID,
        pipeline_name: str = "cognify_pipeline",
    ) -> DatasetStatus:
        """
        获取数据集的处理状态。
        
        Args:
            dataset_id: 数据集 ID
            pipeline_name: 管道名称（默认 cognify_pipeline）
            
        Returns:
            数据集状态
        """
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        
        try:
            # 转换 ID
            if isinstance(dataset_id, str):
                dataset_id = UUID(dataset_id)
            
            # 获取管道状态
            from cognee.modules.pipelines.operations.get_pipeline_status import (
                get_pipeline_status,
            )
            status_dict = await get_pipeline_status([dataset_id], pipeline_name=pipeline_name)
            
            # 解析状态
            status_info = status_dict.get(str(dataset_id), {})
            
            return DatasetStatus(
                dataset_id=str(dataset_id),
                pipeline_name=pipeline_name,
                status=status_info.get("status", "unknown"),
                progress=status_info.get("progress", 0.0),
                message=status_info.get("message"),
                last_updated=status_info.get("last_updated"),
            )
            
        except Exception as e:
            raise RuntimeError(f"获取数据集状态失败: {e}") from e
    
    async def list_dataset_data(
        self,
        dataset_id: str | UUID,
        user: Optional[Any] = None,
    ) -> list[DataItem]:
        """
        列出数据集中的所有数据项。
        
        Args:
            dataset_id: 数据集 ID
            user: 用户对象（可选）
            
        Returns:
            数据项列表
        """
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        from cognee.modules.users.methods import get_default_user
        from cognee.modules.data.methods import get_authorized_dataset, get_dataset_data
        
        if user is None:
            user = await get_default_user()
        
        try:
            if isinstance(dataset_id, str):
                dataset_id = UUID(dataset_id)
            
            # 获取授权的数据集
            dataset = await get_authorized_dataset(user, dataset_id)
            
            if not dataset:
                raise PermissionError(f"无权访问数据集 {dataset_id}")
            
            # 获取数据项
            data_items = await get_dataset_data(dataset.id)
            
            return [
                DataItem(
                    id=str(item.id),
                    name=item.name,
                    mime_type=getattr(item, "mime_type", None),
                    created_at=getattr(item, "created_at", None),
                    raw_data_location=getattr(item, "raw_data_location", None),
                )
                for item in (data_items or [])
            ]
            
        except Exception as e:
            raise RuntimeError(f"获取数据项列表失败: {e}") from e
    
    async def delete_data(
        self,
        dataset_id: str | UUID,
        data_id: str | UUID,
        user: Optional[Any] = None,
    ) -> bool:
        """
        删除数据项。
        
        Args:
            dataset_id: 数据集 ID
            data_id: 数据项 ID
            user: 用户对象（可选）
            
        Returns:
            是否成功删除
        """
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        from cognee.modules.users.methods import get_default_user
        from cognee.modules.data.methods import delete_data
        
        if user is None:
            user = await get_default_user()
        
        try:
            if isinstance(dataset_id, str):
                dataset_id = UUID(dataset_id)
            if isinstance(data_id, str):
                data_id = UUID(data_id)
            
            # 检查权限
            from cognee.modules.data.methods import get_authorized_dataset
            dataset = await get_authorized_dataset(user, dataset_id, "delete")
            
            if not dataset:
                raise PermissionError(f"无权删除数据集 {dataset_id} 中的数据")
            
            # 删除数据
            await delete_data(data_id=data_id, dataset_id=dataset_id)
            
            return True
            
        except Exception as e:
            raise RuntimeError(f"删除数据失败: {e}") from e
    
    async def empty_dataset(
        self,
        dataset_id: str | UUID,
        user: Optional[Any] = None,
    ) -> bool:
        """
        清空数据集（删除所有数据）。
        
        Args:
            dataset_id: 数据集 ID
            user: 用户对象（可选）
            
        Returns:
            是否成功清空
        """
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        import cognee
        from cognee.modules.users.methods import get_default_user
        
        if user is None:
            user = await get_default_user()
        
        try:
            if isinstance(dataset_id, str):
                dataset_id = UUID(dataset_id)
            
            # 使用 cognee.datasets.empty_dataset
            await cognee.datasets.empty_dataset(dataset_id, user)
            
            return True
            
        except Exception as e:
            raise RuntimeError(f"清空数据集失败: {e}") from e
    
    async def has_data(
        self,
        dataset_id: str | UUID,
        user: Optional[Any] = None,
    ) -> bool:
        """
        检查数据集是否有数据。
        
        Args:
            dataset_id: 数据集 ID
            user: 用户对象（可选）
            
        Returns:
            是否有数据
        """
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        import cognee
        from cognee.modules.users.methods import get_default_user
        
        if user is None:
            user = await get_default_user()
        
        try:
            if isinstance(dataset_id, str):
                dataset_id = UUID(dataset_id)
            
            return await cognee.datasets.has_data(str(dataset_id), user)
            
        except Exception as e:
            raise RuntimeError(f"检查数据存在性失败: {e}") from e
    
    async def prune_all_data(self, metadata: bool = False) -> bool:
        """
        清空所有数据（危险操作！）。
        
        Args:
            metadata: 是否同时清空元数据
            
        Returns:
            是否成功
        """
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        import cognee
        
        try:
            # 清空数据
            await cognee.prune.prune_data()
            
            # 清空元数据（如果请求）
            if metadata:
                await cognee.prune.prune_system(metadata=True)
            
            return True
            
        except Exception as e:
            raise RuntimeError(f"清空所有数据失败: {e}") from e


# 便捷函数
async def list_all_datasets() -> list[DatasetInfo]:
    """便捷函数：列出所有数据集。"""
    manager = DatasetManager()
    return await manager.list_datasets()


async def check_dataset_status(dataset_id: str) -> DatasetStatus:
    """便捷函数：检查数据集状态。"""
    manager = DatasetManager()
    return await manager.get_dataset_status(dataset_id)


async def delete_dataset_completely(dataset_id: str) -> bool:
    """便捷函数：完全清空数据集。"""
    manager = DatasetManager()
    return await manager.empty_dataset(dataset_id)
