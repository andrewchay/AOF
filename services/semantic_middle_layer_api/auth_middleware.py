# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""API 鉴权中间件

提供 FastAPI 的认证和授权中间件：
1. JWT Token 认证
2. API Key 认证
3. 请求签名验证
4. 权限检查

与 RBAC 系统集成，实现统一的访问控制。
"""

from __future__ import annotations

import jwt
import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Callable, List
from functools import wraps

from fastapi import Request, HTTPException, Depends, APIRouter
from fastapi.security import HTTPBearer, APIKeyHeader
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# 导入 RBAC
from bridge.auth import (
    RBACManager,
    ResourceType,
    Action,
    PermissionDenied,
)

logger = logging.getLogger(__name__)

# ========== 配置 ==========

# JWT 配置
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 30
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7

# API Key 配置
API_KEY_HEADER = "X-API-Key"
API_KEY_PREFIX = "aof_"

# 安全 Schema
security_bearer = HTTPBearer(auto_error=False)
security_api_key = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


class AuthError(Exception):
    """认证错误基类"""
    pass


class InvalidTokenError(AuthError):
    """无效 Token"""
    pass


class ExpiredTokenError(AuthError):
    """Token 过期"""
    pass


class InvalidAPIKeyError(AuthError):
    """无效 API Key"""
    pass


def _jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET_KEY", "")
    if len(secret) < 32:
        raise AuthError("legacy JWT signing is disabled: JWT_SECRET_KEY must be at least 32 characters")
    return secret


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """认证中间件
    
    自动解析请求中的认证信息并设置 request.state.user
    """
    
    def __init__(
        self,
        app: ASGIApp,
        rbac_manager: Optional[RBACManager] = None,
        exclude_paths: Optional[List[str]] = None,
    ):
        super().__init__(app)
        self.rbac = rbac_manager
        self.exclude_paths = exclude_paths or [
            "/docs", "/openapi.json", "/redoc",
            "/health", "/metrics", "/v1/ops/health",
        ]
    
    async def dispatch(self, request: Request, call_next):
        """处理每个请求"""
        
        # 跳过排除的路径
        path = request.url.path
        if any(path.startswith(p) for p in self.exclude_paths):
            return await call_next(request)
        
        # 尝试认证
        try:
            user_context = await self._authenticate_request(request)
            request.state.user = user_context
            request.state.user_id = user_context.get("user_id") if user_context else None
            request.state.tenant_id = user_context.get("tenant_id") if user_context else None
        except AuthError as e:
            # 认证失败但继续（让端点自行决定是否要求认证）
            request.state.user = None
            request.state.user_id = None
            request.state.tenant_id = None
            request.state.auth_error = str(e)
        
        response = await call_next(request)
        return response
    
    async def _authenticate_request(self, request: Request) -> Dict[str, Any]:
        """解析请求中的认证信息"""
        
        # 1. 尝试 JWT Token
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")
            return self._verify_jwt_token(token)
        
        # 2. 尝试 API Key
        api_key = request.headers.get(API_KEY_HEADER)
        if api_key:
            return await self._verify_api_key(api_key)
        
        # 3. 尝试 Session Cookie（如果有）
        session_token = request.cookies.get("session")
        if session_token:
            return self._verify_session_token(session_token)
        
        raise AuthError("No valid authentication credentials found")
    
    def _verify_jwt_token(self, token: str) -> Dict[str, Any]:
        """验证 JWT Token"""
        try:
            payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
            
            # 检查过期时间
            exp = payload.get("exp")
            if exp and datetime.utcnow().timestamp() > exp:
                raise ExpiredTokenError("Token has expired")
            
            return {
                "user_id": payload.get("sub"),
                "username": payload.get("username"),
                "tenant_id": payload.get("tenant_id"),
                "auth_method": "jwt",
                "scopes": payload.get("scopes", []),
            }
            
        except jwt.ExpiredSignatureError:
            raise ExpiredTokenError("Token has expired")
        except jwt.InvalidTokenError as e:
            raise InvalidTokenError(f"Invalid token: {e}")
    
    async def _verify_api_key(self, api_key: str) -> Dict[str, Any]:
        """验证 API Key"""
        # API Key 格式: aof_{tenant_id}_{hash}
        if not api_key.startswith(API_KEY_PREFIX):
            raise InvalidAPIKeyError("Invalid API key format")
        
        # A tenant name embedded in a key is not proof of identity. This
        # legacy middleware has no credential repository, so it must reject
        # every key instead of treating a syntactically valid string as valid.
        raise InvalidAPIKeyError("API key authentication requires a configured credential store")
    
    def _verify_session_token(self, session_token: str) -> Dict[str, Any]:
        """验证 Session Token"""
        # 这里应该查询 Redis/数据库验证 Session
        # 简化示例
        raise AuthError("Session authentication not implemented")


# ========== Token 生成与管理 ==========

class TokenManager:
    """JWT Token 管理"""
    
    @staticmethod
    def create_access_token(
        user_id: str,
        username: str,
        tenant_id: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        expires_delta: Optional[timedelta] = None
    ) -> str:
        """创建访问 Token"""
        if expires_delta is None:
            expires_delta = timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
        
        expire = datetime.utcnow() + expires_delta
        
        payload = {
            "sub": user_id,  # subject (user_id)
            "username": username,
            "tenant_id": tenant_id,
            "scopes": scopes or [],
            "exp": expire,
            "iat": datetime.utcnow(),  # issued at
            "type": "access",
        }
        
        return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)
    
    @staticmethod
    def create_refresh_token(user_id: str, expires_delta: Optional[timedelta] = None) -> str:
        """创建刷新 Token"""
        if expires_delta is None:
            expires_delta = timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        
        expire = datetime.utcnow() + expires_delta
        
        payload = {
            "sub": user_id,
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "refresh",
        }
        
        return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)
    
    @staticmethod
    def decode_token(token: str) -> Dict[str, Any]:
        """Decode and verify a legacy token."""
        return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])


# ========== 依赖注入函数 ==========

async def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """获取当前用户（FastAPI Depends 用）"""
    user = getattr(request.state, "user", None)
    return user


async def require_auth(request: Request) -> Dict[str, Any]:
    """要求必须认证"""
    user = await get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user_id(request: Request) -> Optional[str]:
    """获取当前用户 ID"""
    return getattr(request.state, "user_id", None)


async def get_current_tenant_id(request: Request) -> Optional[str]:
    """获取当前租户 ID"""
    return getattr(request.state, "tenant_id", None)


# ========== 权限检查依赖 ==========

class PermissionChecker:
    """权限检查器"""
    
    def __init__(self, rbac_manager: RBACManager):
        self.rbac = rbac_manager
    
    def __call__(
        self,
        resource_type: ResourceType,
        action: Action,
        resource_id_param: Optional[str] = None
    ) -> Callable:
        """创建权限检查依赖"""
        
        async def check_permission(request: Request) -> bool:
            user = await require_auth(request)
            user_id = user.get("user_id")
            
            # 获取资源 ID
            resource_id = None
            if resource_id_param:
                # 从路径参数或查询参数获取
                resource_id = request.path_params.get(resource_id_param) or \
                             request.query_params.get(resource_id_param)
            
            # 检查权限
            try:
                await self.rbac.require_permission(
                    user_id=user_id,
                    resource_type=resource_type,
                    action=action,
                    resource_id=resource_id
                )
                return True
            except PermissionDenied as e:
                raise HTTPException(
                    status_code=403,
                    detail=f"Permission denied: {e}"
                )
        
        return check_permission


# ========== 便捷装饰器 ==========

def require_permission(
    resource_type: ResourceType,
    action: Action,
    resource_id_param: Optional[str] = None
):
    """端点权限装饰器
    
    使用示例:
        @app.get("/datasets/{dataset_id}")
        @require_permission(ResourceType.DATASET, Action.READ, "dataset_id")
        async def get_dataset(dataset_id: str, user: dict = Depends(require_auth)):
            pass
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 获取 request 对象
            request = kwargs.get('request')
            if not request and args:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break
            
            if not request:
                raise HTTPException(status_code=500, detail="Request object not found")
            
            # 获取 user_id
            user = await require_auth(request)
            user_id = user.get("user_id")
            
            # 获取 rbac_manager（从 app state）
            rbac = getattr(request.app.state, "rbac_manager", None)
            if not rbac:
                raise HTTPException(status_code=500, detail="RBAC not initialized")
            
            # 获取资源 ID
            resource_id = None
            if resource_id_param:
                resource_id = kwargs.get(resource_id_param) or \
                             request.path_params.get(resource_id_param)
            
            # 检查权限
            try:
                await rbac.require_permission(
                    user_id=user_id,
                    resource_type=resource_type,
                    action=action,
                    resource_id=resource_id
                )
            except PermissionDenied as e:
                raise HTTPException(status_code=403, detail=str(e))
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


# ========== 请求签名验证 ==========

class RequestSigner:
    """请求签名验证（用于服务间调用）"""
    
    @staticmethod
    def generate_signature(
        method: str,
        path: str,
        timestamp: str,
        body: Optional[str],
        secret: str
    ) -> str:
        """生成请求签名"""
        # 拼接签名字符串
        sign_string = f"{method}\n{path}\n{timestamp}\n{body or ''}"
        
        # HMAC-SHA256
        signature = hmac.new(
            secret.encode(),
            sign_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return signature
    
    @staticmethod
    def verify_signature(
        method: str,
        path: str,
        timestamp: str,
        body: Optional[str],
        signature: str,
        secret: str,
        max_age_seconds: int = 300
    ) -> bool:
        """验证请求签名"""
        # 检查时间戳
        try:
            request_time = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            age = abs(datetime.now(timezone.utc) - request_time)
            if age > timedelta(seconds=max_age_seconds):
                return False
        except (ValueError, TypeError):
            return False
        
        # 验证签名
        expected = RequestSigner.generate_signature(method, path, timestamp, body, secret)
        return hmac.compare_digest(signature, expected)


# ========== 初始化函数 ==========

def setup_auth(app: ASGIApp, rbac_manager: Optional[RBACManager] = None) -> None:
    """为 FastAPI 应用设置认证"""
    # 添加中间件
    app.add_middleware(
        AuthenticationMiddleware,
        rbac_manager=rbac_manager,
    )
    
    # 存储 rbac_manager 到 app state
    if hasattr(app, 'state'):
        app.state.rbac_manager = rbac_manager


# ========== 认证端点（示例）==========

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserInfoResponse(BaseModel):
    user_id: str
    username: str
    email: Optional[str]
    tenant_id: Optional[str]
    roles: List[str]


@auth_router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """Retired local-password endpoint; interactive users authenticate at the IdP."""
    del request
    raise HTTPException(
        status_code=410,
        detail="Local password login is retired; use OIDC authorization code with PKCE",
    )


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str):
    """Retired local refresh endpoint; token lifecycle belongs to the IdP."""
    del refresh_token
    raise HTTPException(
        status_code=410,
        detail="Local refresh tokens are retired; use the configured OIDC provider",
    )


@auth_router.get("/me", response_model=UserInfoResponse)
async def get_current_user_info(user: dict = Depends(require_auth)):
    """获取当前用户信息"""
    return UserInfoResponse(
        user_id=user.get("user_id"),
        username=user.get("username"),
        email=user.get("email"),
        tenant_id=user.get("tenant_id"),
        roles=user.get("scopes", []),
    )
