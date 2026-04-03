"""多租户管理模块单元测试

运行:
    cd /path/to/AOF
    python -m pytest tests/test_tenant_manager.py -v
"""

import pytest
from datetime import datetime

from bridge.tenant.manager import (
    TenantManager, Tenant, TenantConfig, TenantStatus
)
from bridge.tenant.context import (
    TenantContext, get_current_tenant_id, set_current_tenant, clear_current_tenant
)


class TestTenantConfig:
    """测试租户配置"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = TenantConfig()
        
        assert config.max_datasets == 100
        assert config.max_storage_gb == 100.0
        assert config.max_concurrent_queries == 10
        assert config.max_graph_nodes == 10_000_000
        assert config.enable_analytics is True
        assert config.enable_ml_features is False
    
    def test_custom_config(self):
        """测试自定义配置"""
        config = TenantConfig(
            max_datasets=50,
            max_graph_nodes=1_000_000,
            enable_ml_features=True
        )
        
        assert config.max_datasets == 50
        assert config.max_graph_nodes == 1_000_000
        assert config.enable_ml_features is True
    
    def test_config_serialization(self):
        """测试配置序列化"""
        config = TenantConfig(max_datasets=200)
        data = config.to_dict()
        
        assert data["max_datasets"] == 200
        assert data["enable_analytics"] is True
        
        # 反序列化
        restored = TenantConfig.from_dict(data)
        assert restored.max_datasets == 200


class TestTenant:
    """测试租户实体"""
    
    def test_tenant_creation(self):
        """测试创建租户实体"""
        tenant = Tenant(
            id="tenant_123",
            name="ACME Corp",
            slug="acme-corp",
            status=TenantStatus.ACTIVE,
            config=TenantConfig(),
            nebula_space="aof_acme-corp"  # 显式设置
        )
        
        assert tenant.id == "tenant_123"
        assert tenant.name == "ACME Corp"
        assert tenant.slug == "acme-corp"
        assert tenant.status == TenantStatus.ACTIVE
        assert tenant.nebula_space == "aof_acme-corp"
    
    def test_tenant_to_dict(self):
        """测试租户序列化"""
        tenant = Tenant(
            id="tenant_123",
            name="Test Tenant",
            slug="test-tenant",
            status=TenantStatus.ACTIVE,
            config=TenantConfig(),
            dataset_count=5,
            total_nodes=10000
        )
        
        data = tenant.to_dict()
        
        assert data["id"] == "tenant_123"
        assert data["name"] == "Test Tenant"
        assert data["status"] == "active"
        assert data["usage"]["dataset_count"] == 5
        assert data["usage"]["total_nodes"] == 10000
    
    def test_tenant_status_enum(self):
        """测试租户状态枚举"""
        assert TenantStatus.ACTIVE.value == "active"
        assert TenantStatus.SUSPENDED.value == "suspended"
        assert TenantStatus.PENDING.value == "pending"
        assert TenantStatus.DELETED.value == "deleted"


class TestTenantManager:
    """测试租户管理器"""
    
    @pytest.fixture
    def manager(self):
        """创建测试管理器"""
        return TenantManager(
            db_session=None,
            rbac_manager=None,
            nebula_pool=None
        )
    
    def test_generate_slug(self, manager):
        """测试 slug 生成"""
        slug = manager._generate_slug("ACME Corporation")
        
        assert slug.startswith("acme-corporation-")
        assert len(slug) <= 64
        assert manager._validate_slug(slug) is True
    
    def test_generate_slug_short_name(self, manager):
        """测试短名称 slug 生成"""
        slug = manager._generate_slug("AB")
        
        assert slug.startswith("tenant-")
        assert len(slug) >= 3
    
    def test_validate_slug_valid(self, manager):
        """测试有效的 slug"""
        valid_slugs = [
            "acme-corp",
            "test123",
            "my-tenant-123",
            "abcd",  # 最小长度4
        ]
        
        for slug in valid_slugs:
            assert manager._validate_slug(slug) is True, f"{slug} should be valid"
    
    def test_validate_slug_invalid(self, manager):
        """测试无效的 slug"""
        invalid_slugs = [
            "",  # 空
            "ab",  # 太短
            "-abc",  # 以 - 开头
            "abc-",  # 以 - 结尾
            "ABC_CORP",  # 大写和下划线
            "a" * 65,  # 太长
        ]
        
        for slug in invalid_slugs:
            assert manager._validate_slug(slug) is False, f"{slug} should be invalid"
    
    @pytest.mark.asyncio
    async def test_check_quota_allowed(self, manager):
        """测试配额检查 - 允许"""
        # 创建模拟租户
        tenant = Tenant(
            id="test_tenant",
            name="Test",
            slug="test",
            status=TenantStatus.ACTIVE,
            config=TenantConfig(max_datasets=10),
            dataset_count=5
        )
        
        # 手动注入到管理器（模拟数据库查询）
        manager._test_tenant = tenant
        
        # Mock get_tenant 方法
        async def mock_get_tenant(tenant_id):
            if tenant_id == "test_tenant":
                return manager._test_tenant
            return None
        
        manager.get_tenant = mock_get_tenant
        
        allowed, info = await manager.check_quota("test_tenant", "dataset", requested_amount=3)
        
        assert allowed is True
        assert info["status"] == "ok"
    
    @pytest.mark.asyncio
    async def test_check_quota_exceeded(self, manager):
        """测试配额检查 - 超出"""
        tenant = Tenant(
            id="test_tenant",
            name="Test",
            slug="test",
            status=TenantStatus.ACTIVE,
            config=TenantConfig(max_datasets=5),
            dataset_count=5
        )
        
        manager._test_tenant = tenant
        
        async def mock_get_tenant(tenant_id):
            if tenant_id == "test_tenant":
                return manager._test_tenant
            return None
        
        manager.get_tenant = mock_get_tenant
        
        allowed, info = await manager.check_quota("test_tenant", "dataset", requested_amount=1)
        
        assert allowed is False
        assert info["resource"] == "dataset"
        assert info["current"] == 5
        assert info["limit"] == 5
    
    @pytest.mark.asyncio
    async def test_check_quota_inactive_tenant(self, manager):
        """测试非活跃租户配额检查"""
        tenant = Tenant(
            id="test_tenant",
            name="Test",
            slug="test",
            status=TenantStatus.SUSPENDED,
            config=TenantConfig()
        )
        
        manager._test_tenant = tenant
        
        async def mock_get_tenant(tenant_id):
            if tenant_id == "test_tenant":
                return manager._test_tenant
            return None
        
        manager.get_tenant = mock_get_tenant
        
        allowed, info = await manager.check_quota("test_tenant", "dataset")
        
        assert allowed is False
        assert "suspended" in info["error"]
    
    def test_get_usage_report(self, manager):
        """测试使用报告生成"""
        tenant = Tenant(
            id="test_tenant",
            name="Test Corp",
            slug="test-corp",
            status=TenantStatus.ACTIVE,
            config=TenantConfig(max_datasets=100, max_graph_nodes=1000000),
            dataset_count=25,
            total_nodes=500000
        )
        
        manager._test_tenant = tenant
        
        # Mock 方法
        async def mock_get_tenant(tenant_id):
            if tenant_id == "test_tenant":
                return manager._test_tenant
            return None
        
        manager.get_tenant = mock_get_tenant
        
        import asyncio
        report = asyncio.run(manager.get_usage_report("test_tenant"))
        
        assert report["tenant_id"] == "test_tenant"
        assert report["tenant_name"] == "Test Corp"
        assert report["datasets"]["used"] == 25
        assert report["datasets"]["limit"] == 100
        assert report["datasets"]["percentage"] == 25.0
        assert report["graph_nodes"]["used"] == 500000
        assert report["graph_nodes"]["limit"] == 1000000
        assert report["graph_nodes"]["percentage"] == 50.0


class TestTenantContext:
    """测试租户上下文"""
    
    def test_context_manager_sync(self):
        """测试同步上下文管理器"""
        # 初始状态
        assert get_current_tenant_id() is None
        
        # 使用上下文
        with TenantContext(tenant_id="acme", tenant_slug="acme-corp"):
            assert get_current_tenant_id() == "acme"
        
        # 退出后恢复
        assert get_current_tenant_id() is None
    
    @pytest.mark.asyncio
    async def test_context_manager_async(self):
        """测试异步上下文管理器"""
        # 初始状态
        assert get_current_tenant_id() is None
        
        # 使用异步上下文
        async with TenantContext(tenant_id="techcorp", tenant_slug="tech-corp"):
            assert get_current_tenant_id() == "techcorp"
        
        # 退出后恢复
        assert get_current_tenant_id() is None
    
    def test_nested_context(self):
        """测试嵌套上下文"""
        with TenantContext(tenant_id="outer"):
            assert get_current_tenant_id() == "outer"
            
            with TenantContext(tenant_id="inner"):
                assert get_current_tenant_id() == "inner"
            
            # 内层退出后恢复外层
            assert get_current_tenant_id() == "outer"
        
        # 全部退出后恢复 None
        assert get_current_tenant_id() is None
    
    def test_manual_set_clear(self):
        """测试手动设置和清除"""
        # 设置
        set_current_tenant(tenant_id="manual", tenant_slug="manual-slug")
        assert get_current_tenant_id() == "manual"
        
        # 清除
        clear_current_tenant()
        assert get_current_tenant_id() is None


class TestTenantAwareRouter:
    """测试租户感知路由工具"""
    
    def test_get_tenant_dataset_name(self):
        """测试生成租户隔离的数据集名称"""
        from bridge.tenant.middleware import TenantAwareRouter
        
        name = TenantAwareRouter.get_tenant_dataset_name("acme", "contracts")
        assert name == "aof_acme_contracts"
    
    def test_parse_dataset_name(self):
        """测试解析数据集名称"""
        from bridge.tenant.middleware import TenantAwareRouter
        
        # 标准格式
        tenant, name = TenantAwareRouter.parse_dataset_name("aof_acme_contracts_v1")
        assert tenant == "acme"
        assert name == "contracts_v1"
        
        # 非标准格式
        tenant, name = TenantAwareRouter.parse_dataset_name("my_dataset")
        assert tenant is None
        assert name == "my_dataset"
    
    def test_get_tenant_ontology_path(self):
        """测试生成租户隔离的本体路径"""
        from bridge.tenant.middleware import TenantAwareRouter
        
        path = TenantAwareRouter.get_tenant_ontology_path("acme", "inventory")
        assert path == "ontologies/acme/inventory.owl"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
