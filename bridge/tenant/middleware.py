"""租户中间件

FastAPI 中间件，自动从请求中提取租户信息并设置上下文。
"""

from __future__ import annotations

from typing import Optional, List
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from .context import set_current_tenant, clear_current_tenant
from .manager import TenantManager, TenantStatus


class TenantMiddleware(BaseHTTPMiddleware):
    """租户识别中间件
    
    从以下位置提取租户信息（按优先级）：
    1. Header: X-Tenant-ID 或 X-Tenant-Slug
    2. JWT Token: tenant_id claim
    3. 子域名: {tenant}.aof.example.com
    4. 路径参数: /api/v1/{tenant}/...
    
    设置：
    - request.state.tenant_id
    - request.state.tenant_slug
    - request.state.tenant_config
    - 上下文变量（通过 ContextVar）
    """
    
    def __init__(
        self,
        app: ASGIApp,
        tenant_manager: Optional[TenantManager] = None,
        header_name: str = "X-Tenant-ID",
        slug_header_name: str = "X-Tenant-Slug",
        subdomain_mode: bool = False,
        exclude_paths: Optional[List[str]] = None,
        require_tenant: bool = True,
    ):
        super().__init__(app)
        self.tenant_manager = tenant_manager
        self.header_name = header_name
        self.slug_header_name = slug_header_name
        self.subdomain_mode = subdomain_mode
        self.exclude_paths = exclude_paths or [
            "/health", "/metrics", "/docs", "/openapi.json",
            "/auth/login", "/auth/register",
        ]
        self.require_tenant = require_tenant
    
    async def dispatch(self, request: Request, call_next):
        """处理请求"""
        path = request.url.path
        
        # 跳过排除的路径
        if any(path.startswith(p) for p in self.exclude_paths):
            return await call_next(request)
        
        try:
            # 提取租户信息
            tenant_id, tenant_slug, tenant_config = await self._extract_tenant(request)
            
            if not tenant_id and self.require_tenant:
                raise HTTPException(
                    status_code=400,
                    detail="Tenant identification required"
                )
            
            # 验证租户状态
            if tenant_id and self.tenant_manager:
                tenant = await self.tenant_manager.get_tenant(tenant_id)
                if not tenant:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Tenant not found: {tenant_id}"
                    )
                if tenant.status == TenantStatus.SUSPENDED:
                    raise HTTPException(
                        status_code=403,
                        detail="Tenant is suspended"
                    )
                if tenant.status == TenantStatus.DELETED:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Tenant not found: {tenant_id}"
                    )
            
            # 设置请求状态
            request.state.tenant_id = tenant_id
            request.state.tenant_slug = tenant_slug
            request.state.tenant_config = tenant_config
            
            # 设置上下文变量
            set_current_tenant(tenant_id, tenant_slug, tenant_config)
            
            # 处理请求
            response = await call_next(request)
            
            # 在响应头中添加租户信息（便于调试）
            if tenant_id:
                response.headers["X-Tenant-ID"] = tenant_id
            if tenant_slug:
                response.headers["X-Tenant-Slug"] = tenant_slug
            
            return response
            
        except HTTPException:
            raise
        except Exception:
            # 清理上下文
            clear_current_tenant()
            raise
        finally:
            # 确保上下文被清理
            clear_current_tenant()
    
    async def _extract_tenant(
        self,
        request: Request
    ) -> tuple[Optional[str], Optional[str], Optional[dict]]:
        """从请求中提取租户信息"""
        tenant_id = None
        tenant_slug = None
        
        # 1. 从 Header 提取
        tenant_id = request.headers.get(self.header_name)
        tenant_slug = request.headers.get(self.slug_header_name)
        
        # 2. 从 JWT Token 提取（如果 Header 没有）
        if not tenant_id:
            tenant_id = await self._extract_from_token(request)
        
        # 3. 从子域名提取
        if not tenant_id and not tenant_slug and self.subdomain_mode:
            tenant_slug = self._extract_from_subdomain(request)
        
        # 4. 从路径参数提取
        if not tenant_id and not tenant_slug:
            tenant_slug = request.path_params.get("tenant_id") or \
                         request.path_params.get("tenant_slug")
        
        # 如果有 slug 但没有 id，查询数据库
        if tenant_slug and not tenant_id and self.tenant_manager:
            tenant = await self.tenant_manager.get_tenant_by_slug(tenant_slug)
            if tenant:
                tenant_id = tenant.id
                tenant_config = tenant.config.to_dict()
                return tenant_id, tenant_slug, tenant_config
        
        # 如果只有 id，获取配置
        tenant_config = None
        if tenant_id and self.tenant_manager:
            tenant = await self.tenant_manager.get_tenant(tenant_id)
            if tenant:
                tenant_config = tenant.config.to_dict()
                tenant_slug = tenant.slug
        
        return tenant_id, tenant_slug, tenant_config
    
    async def _extract_from_token(self, request: Request) -> Optional[str]:
        """从 JWT Token 提取租户 ID"""
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        
        try:
            import jwt
            token = auth_header.replace("Bearer ", "")
            # 不验证签名，只解码
            payload = jwt.decode(token, options={"verify_signature": False})
            return payload.get("tenant_id")
        except Exception:
            return None
    
    def _extract_from_subdomain(self, request: Request) -> Optional[str]:
        """从子域名提取租户 slug"""
        host = request.headers.get("Host", "")
        # 假设格式: {tenant}.aof.example.com
        parts = host.split(".")
        if len(parts) >= 3:
            return parts[0]
        return None


class TenantAwareRouter:
    """租户感知路由工具
    
    自动为路由添加租户前缀或参数
    """
    
    @staticmethod
    def get_tenant_dataset_name(tenant_id: str, dataset_name: str) -> str:
        """生成租户隔离的数据集名称
        
        格式: aof_{tenant_id}_{dataset_name}
        """
        return f"aof_{tenant_id}_{dataset_name}"
    
    @staticmethod
    def parse_dataset_name(full_name: str) -> tuple[Optional[str], str]:
        """解析数据集名称，提取租户和实际名称
        
        输入: aof_acme_contracts_v1
        输出: ("acme", "contracts_v1")
        """
        parts = full_name.split("_", 2)
        if len(parts) >= 3 and parts[0] == "aof":
            # parts[1] 可能是租户 slug，需要进一步验证
            return parts[1], parts[2]
        return None, full_name
    
    @staticmethod
    def get_tenant_ontology_path(tenant_id: str, ontology_name: str) -> str:
        """生成租户隔离的本体路径"""
        return f"ontologies/{tenant_id}/{ontology_name}.owl"


# ========== 依赖注入函数 ==========

async def get_current_tenant(request: Request) -> Optional[dict]:
    """获取当前租户（FastAPI Depends 用）"""
    tenant_id = getattr(request.state, "tenant_id", None)
    tenant_slug = getattr(request.state, "tenant_slug", None)
    tenant_config = getattr(request.state, "tenant_config", None)
    
    if not tenant_id:
        return None
    
    return {
        "id": tenant_id,
        "slug": tenant_slug,
        "config": tenant_config,
    }


async def require_tenant(request: Request) -> dict:
    """要求必须有租户"""
    tenant = await get_current_tenant(request)
    if not tenant:
        raise HTTPException(
            status_code=400,
            detail="Tenant identification required"
        )
    return tenant
