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
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Callable, List
from functools import wraps

from fastapi import Request, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
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
JWT_SECRET_KEY = "your-secret-key-change-in-production"  # 生产环境应从环境变量读取
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
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            
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
        
        # 这里应该查询数据库验证 API Key 的有效性
        # 简化示例：假设 API Key 有效
        parts = api_key.split("_")
        if len(parts) >= 3:
            tenant_id = parts[1]
            # 从数据库查询 API Key 对应的用户
            if self.rbac:
                # TODO: 实现 API Key 到用户的映射查询
                user = await self.rbac.get_user_by_username(f"api_{tenant_id}")
                if user:
                    return {
                        "user_id": user.id,
                        "username": user.username,
                        "tenant_id": tenant_id,
                        "auth_method": "api_key",
                    }
        
        raise InvalidAPIKeyError("Invalid API key")
    
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
        
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    
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
        
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    
    @staticmethod
    def decode_token(token: str) -> Dict[str, Any]:
        """解码 Token（不验证签名）"""
        return jwt.decode(token, options={"verify_signature": False})


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
                from fastapi import Request
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
            request_time = datetime.fromtimestamp(int(timestamp))
            if datetime.utcnow() - request_time > timedelta(seconds=max_age_seconds):
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

from fastapi import APIRouter
from pydantic import BaseModel

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
async def login(request: LoginRequest, rbac: RBACManager = Depends(lambda: None)):
    """用户登录（示例实现）"""
    # TODO: 验证用户名密码
    # 这里简化处理，实际应从数据库验证
    
    # 假设验证通过
    user_id = "user_123"
    tenant_id = "tenant_456"
    
    # 生成 Token
    access_token = TokenManager.create_access_token(
        user_id=user_id,
        username=request.username,
        tenant_id=tenant_id,
    )
    refresh_token = TokenManager.create_refresh_token(user_id=user_id)
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str):
    """刷新访问 Token"""
    try:
        payload = jwt.decode(refresh_token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        user_id = payload.get("sub")
        
        # 生成新的访问 Token
        access_token = TokenManager.create_access_token(
            user_id=user_id,
            username="",  # 应从数据库查询
        )
        new_refresh_token = TokenManager.create_refresh_token(user_id=user_id)
        
        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


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
