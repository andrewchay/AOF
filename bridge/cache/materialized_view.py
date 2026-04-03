"""物化视图模块

预计算热点查询结果，提升查询性能。

使用示例:
    from bridge.cache.materialized_view import MaterializedViewManager
    
    manager = MaterializedViewManager(cache_backend)
    
    # 创建视图
    await manager.create_view(
        "top_pagerank",
        compute_func=compute_pagerank,
        refresh_interval=3600
    )
    
    # 查询视图
    result = await manager.query_view("top_pagerank")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from enum import Enum

logger = logging.getLogger(__name__)


class ViewStatus(str, Enum):
    """视图状态"""
    ACTIVE = "active"
    REFRESHING = "refreshing"
    STALE = "stale"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class ViewDefinition:
    """视图定义"""
    name: str
    compute_func: Callable[[], Any]
    
    # 刷新配置
    refresh_interval: Optional[int] = None  # 秒，None 表示不自动刷新
    refresh_cron: Optional[str] = None      # Cron 表达式
    
    # 依赖
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他视图
    
    # 缓存配置
    ttl: int = 3600  # 结果 TTL
    max_size: int = 10000  # 最大条目数
    
    # 元数据
    description: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    # 状态
    last_refresh: Optional[datetime] = None
    last_error: Optional[str] = None
    refresh_count: int = 0
    avg_refresh_time_ms: float = 0.0
    status: ViewStatus = ViewStatus.ACTIVE
    
    def should_refresh(self) -> bool:
        """检查是否需要刷新"""
        if self.status != ViewStatus.ACTIVE:
            return False
        
        if self.refresh_interval is None:
            return False
        
        if self.last_refresh is None:
            return True
        
        elapsed = (datetime.utcnow() - self.last_refresh).total_seconds()
        return elapsed >= self.refresh_interval


@dataclass
class ViewResult:
    """视图查询结果"""
    data: Any
    created_at: datetime
    is_fresh: bool  # 是否新鲜（非过期）
    refresh_in_progress: bool


class MaterializedViewManager:
    """物化视图管理器
    
    管理预计算视图的创建、刷新和查询。
    
    使用示例:
        manager = MaterializedViewManager(cache_manager)
        
        # 注册视图
        await manager.register_view(ViewDefinition(
            name="pagerank_top_100",
            compute_func=compute_pagerank,
            refresh_interval=3600,
        ))
        
        # 启动自动刷新
        await manager.start()
        
        # 查询
        result = await manager.query("pagerank_top_100")
    """
    
    def __init__(self, cache_manager=None):
        self.cache = cache_manager
        self._views: Dict[str, ViewDefinition] = {}
        self._refresh_tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        
        # 统计
        self._query_count: Dict[str, int] = {}
        self._hit_count: Dict[str, int] = {}
    
    async def start(self) -> None:
        """启动自动刷新调度"""
        if self._running:
            return
        
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Materialized view manager started")
    
    async def stop(self) -> None:
        """停止调度"""
        self._running = False
        
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        
        # 取消正在进行的刷新任务
        for task in self._refresh_tasks.values():
            task.cancel()
        
        logger.info("Materialized view manager stopped")
    
    async def register_view(self, view: ViewDefinition) -> None:
        """注册视图"""
        self._views[view.name] = view
        self._query_count[view.name] = 0
        self._hit_count[view.name] = 0
        
        logger.info(f"Registered materialized view: {view.name}")
        
        # 立即执行首次计算
        await self.refresh_view(view.name, force=True)
    
    async def unregister_view(self, name: str) -> bool:
        """注销视图"""
        if name not in self._views:
            return False
        
        # 取消正在进行的刷新
        if name in self._refresh_tasks:
            self._refresh_tasks[name].cancel()
        
        del self._views[name]
        del self._query_count[name]
        del self._hit_count[name]
        
        # 清除缓存
        if self.cache:
            await self.cache.delete(f"mv:{name}")
        
        logger.info(f"Unregistered materialized view: {name}")
        return True
    
    async def query(self, name: str, allow_stale: bool = True) -> Optional[ViewResult]:
        """查询视图
        
        Args:
            name: 视图名称
            allow_stale: 是否允许返回过期数据
        
        Returns:
            视图结果
        """
        view = self._views.get(name)
        if not view:
            raise ValueError(f"View not found: {name}")
        
        self._query_count[name] += 1
        
        # 检查缓存
        cached = None
        if self.cache:
            cached = await self.cache.get(f"mv:{name}")
        
        if cached:
            self._hit_count[name] += 1
            
            # 检查是否过期
            is_fresh = True
            if view.last_refresh and view.refresh_interval:
                elapsed = (datetime.utcnow() - view.last_refresh).total_seconds()
                is_fresh = elapsed < view.refresh_interval
            
            # 如果过期且不强制刷新，触发后台刷新
            if not is_fresh and view.status == ViewStatus.ACTIVE:
                asyncio.create_task(self.refresh_view(name))
            
            if is_fresh or allow_stale:
                return ViewResult(
                    data=cached,
                    created_at=view.last_refresh or datetime.utcnow(),
                    is_fresh=is_fresh,
                    refresh_in_progress=name in self._refresh_tasks,
                )
        
        # 缓存未命中或不允许过期数据，同步刷新
        if view.status == ViewStatus.ACTIVE:
            await self.refresh_view(name, force=True)
            
            if self.cache:
                cached = await self.cache.get(f"mv:{name}")
                if cached:
                    return ViewResult(
                        data=cached,
                        created_at=view.last_refresh or datetime.utcnow(),
                        is_fresh=True,
                        refresh_in_progress=False,
                    )
        
        return None
    
    async def refresh_view(self, name: str, force: bool = False) -> bool:
        """刷新视图
        
        Args:
            name: 视图名称
            force: 是否强制刷新（忽略状态检查）
        
        Returns:
            是否成功
        """
        view = self._views.get(name)
        if not view:
            return False
        
        # 检查是否已经在刷新中
        if name in self._refresh_tasks and not force:
            return True
        
        # 检查是否需要刷新
        if not force and not view.should_refresh():
            return True
        
        # 启动刷新任务
        task = asyncio.create_task(self._do_refresh(view))
        self._refresh_tasks[name] = task
        
        try:
            await task
            return True
        except Exception as e:
            logger.error(f"Failed to refresh view {name}: {e}")
            return False
        finally:
            if name in self._refresh_tasks:
                del self._refresh_tasks[name]
    
    async def _do_refresh(self, view: ViewDefinition) -> None:
        """执行刷新"""
        start_time = datetime.utcnow()
        view.status = ViewStatus.REFRESHING
        
        try:
            logger.info(f"Refreshing view: {view.name}")
            
            # 先刷新依赖
            for dep_name in view.dependencies:
                await self.refresh_view(dep_name)
            
            # 执行计算
            result = await view.compute_func()
            
            # 存储结果
            if self.cache:
                await self.cache.set(
                    f"mv:{view.name}",
                    result,
                    ttl=view.ttl,
                )
            
            # 更新统计
            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            view.refresh_count += 1
            
            # 移动平均
            if view.avg_refresh_time_ms == 0:
                view.avg_refresh_time_ms = duration_ms
            else:
                view.avg_refresh_time_ms = (
                    view.avg_refresh_time_ms * 0.9 + duration_ms * 0.1
                )
            
            view.last_refresh = datetime.utcnow()
            view.status = ViewStatus.ACTIVE
            view.last_error = None
            
            logger.info(f"View refreshed: {view.name} ({duration_ms:.0f}ms)")
            
        except Exception as e:
            view.status = ViewStatus.ERROR
            view.last_error = str(e)
            logger.error(f"View refresh failed: {view.name}, error={e}")
            raise
    
    async def refresh_all(self) -> Dict[str, bool]:
        """刷新所有视图"""
        results = {}
        for name in self._views:
            results[name] = await self.refresh_view(name, force=True)
        return results
    
    async def _scheduler_loop(self) -> None:
        """调度循环"""
        while self._running:
            try:
                for name, view in self._views.items():
                    if view.should_refresh() and name not in self._refresh_tasks:
                        asyncio.create_task(self.refresh_view(name))
                
                await asyncio.sleep(60)  # 每分钟检查一次
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = {
            "total_views": len(self._views),
            "active_views": sum(1 for v in self._views.values() if v.status == ViewStatus.ACTIVE),
            "views": {},
        }
        
        for name, view in self._views.items():
            query_count = self._query_count.get(name, 0)
            hit_count = self._hit_count.get(name, 0)
            hit_rate = hit_count / query_count if query_count > 0 else 0
            
            stats["views"][name] = {
                "status": view.status.value,
                "last_refresh": view.last_refresh.isoformat() if view.last_refresh else None,
                "refresh_count": view.refresh_count,
                "avg_refresh_time_ms": view.avg_refresh_time_ms,
                "query_count": query_count,
                "hit_count": hit_count,
                "hit_rate": hit_rate,
            }
        
        return stats


# ========== 预定义视图 ==========

async def create_default_views(manager: MaterializedViewManager, backend) -> None:
    """创建默认物化视图"""
    
    # PageRank Top 1000
    async def compute_pagerank():
        return await backend.pagerank(top_k=1000)
    
    await manager.register_view(ViewDefinition(
        name="pagerank_top_1000",
        compute_func=compute_pagerank,
        refresh_interval=3600,  # 1小时
        description="Top 1000 nodes by PageRank",
    ))
    
    # 社区统计
    async def compute_communities():
        return await backend.community_detection()
    
    await manager.register_view(ViewDefinition(
        name="community_summary",
        compute_func=compute_communities,
        refresh_interval=7200,  # 2小时
        description="Community detection summary",
    ))
    
    # 图谱统计
    async def compute_stats():
        return await backend.get_statistics()
    
    await manager.register_view(ViewDefinition(
        name="graph_statistics",
        compute_func=compute_stats,
        refresh_interval=600,  # 10分钟
        description="Graph statistics",
    ))
