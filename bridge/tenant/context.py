# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""租户上下文管理

提供租户上下文的获取和设置：
- 上下文变量存储
- 异步上下文管理器
- 租户切换
"""

from __future__ import annotations

from contextvars import ContextVar
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

# 当前租户 ID 的上下文变量
tenant_id_var: ContextVar[Optional[str]] = ContextVar('tenant_id', default=None)
tenant_slug_var: ContextVar[Optional[str]] = ContextVar('tenant_slug', default=None)
tenant_config_var: ContextVar[Optional[Dict[str, Any]]] = ContextVar('tenant_config', default=None)


class TenantContext:
    """租户上下文管理器
    
    使用示例:
        # 方式1：使用 async with
        async with TenantContext(tenant_id="acme"):
            # 在此块内，当前租户是 acme
            await do_something()
        
        # 方式2：手动设置
        set_current_tenant("acme")
        try:
            await do_something()
        finally:
            clear_current_tenant()
    """
    
    def __init__(
        self,
        tenant_id: Optional[str] = None,
        tenant_slug: Optional[str] = None,
        tenant_config: Optional[Dict[str, Any]] = None
    ):
        self.tenant_id = tenant_id
        self.tenant_slug = tenant_slug
        self.tenant_config = tenant_config
        
        # 保存旧的 token
        self._id_token = None
        self._slug_token = None
        self._config_token = None
    
    async def __aenter__(self):
        """进入上下文"""
        self._id_token = tenant_id_var.set(self.tenant_id)
        self._slug_token = tenant_slug_var.set(self.tenant_slug)
        self._config_token = tenant_config_var.set(self.tenant_config)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出上下文"""
        if self._id_token:
            tenant_id_var.reset(self._id_token)
        if self._slug_token:
            tenant_slug_var.reset(self._slug_token)
        if self._config_token:
            tenant_config_var.reset(self._config_token)
    
    def __enter__(self):
        """同步进入（较少用）"""
        self._id_token = tenant_id_var.set(self.tenant_id)
        self._slug_token = tenant_slug_var.set(self.tenant_slug)
        self._config_token = tenant_config_var.set(self.tenant_config)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """同步退出"""
        if self._id_token:
            tenant_id_var.reset(self._id_token)
        if self._slug_token:
            tenant_slug_var.reset(self._slug_token)
        if self._config_token:
            tenant_config_var.reset(self._config_token)


def get_current_tenant_id() -> Optional[str]:
    """获取当前租户 ID"""
    return tenant_id_var.get()


def get_current_tenant_slug() -> Optional[str]:
    """获取当前租户 slug"""
    return tenant_slug_var.get()


def get_current_tenant_config() -> Optional[Dict[str, Any]]:
    """获取当前租户配置"""
    return tenant_config_var.get()


def set_current_tenant(
    tenant_id: Optional[str] = None,
    tenant_slug: Optional[str] = None,
    tenant_config: Optional[Dict[str, Any]] = None
) -> None:
    """设置当前租户（全局，慎用）"""
    tenant_id_var.set(tenant_id)
    tenant_slug_var.set(tenant_slug)
    tenant_config_var.set(tenant_config)


def clear_current_tenant() -> None:
    """清除当前租户上下文"""
    tenant_id_var.set(None)
    tenant_slug_var.set(None)
    tenant_config_var.set(None)


@asynccontextmanager
async def tenant_scope(
    tenant_id: Optional[str] = None,
    tenant_slug: Optional[str] = None,
    tenant_config: Optional[Dict[str, Any]] = None
):
    """租户范围的异步上下文管理器
    
    使用示例:
        async with tenant_scope(tenant_id="acme"):
            result = await process_data()
    """
    async with TenantContext(tenant_id, tenant_slug, tenant_config):
        yield
