"""RBAC 权限系统单元测试

运行:
    cd /path/to/AOF
    python -m pytest tests/test_auth_rbac.py -v
"""

import pytest
from datetime import datetime, timedelta

# 导入被测试模块
from bridge.auth.models import (
    User, Permission, Resource, UserRoleAssignment,
    RoleType, ResourceType, Action,
    create_system_role,
)
from bridge.auth.rbac import RBACManager


class TestModels:
    """测试数据模型"""
    
    def test_permission_matching(self):
        """测试权限匹配逻辑"""
        # 通配符权限
        wildcard = Permission(ResourceType.DATASET, Action.READ)
        
        # 特定资源权限
        specific = Permission(ResourceType.DATASET, Action.READ, "dataset_123")
        
        # 通配符应该匹配特定资源
        assert wildcard.matches(specific)
        
        # 特定资源不应匹配其他资源
        other = Permission(ResourceType.DATASET, Action.READ, "dataset_456")
        assert not specific.matches(other)
    
    def test_role_type_levels(self):
        """测试角色级别排序"""
        assert RoleType.ADMIN.level > RoleType.OWNER.level
        assert RoleType.OWNER.level > RoleType.EDITOR.level
        assert RoleType.EDITOR.level > RoleType.VIEWER.level
    
    def test_user_permission_check(self):
        """测试用户权限检查"""
        # 创建角色
        admin_role = create_system_role(RoleType.ADMIN)
        
        # 创建用户并分配角色
        user = User(
            id="user_1",
            username="test_user",
            roles=[admin_role]
        )
        
        # 检查权限
        perm = Permission(ResourceType.DATASET, Action.READ, "ds_123")
        assert user.has_permission(perm)
        
        # 超级管理员
        superuser = User(
            id="user_2",
            username="super",
            is_superuser=True,
            roles=[]
        )
        assert superuser.has_permission(perm)
    
    def test_resource_to_permission(self):
        """测试资源生成权限"""
        resource = Resource(
            type=ResourceType.DATASET,
            id="dataset_abc",
            name="Test Dataset"
        )
        
        perm = resource.to_permission(Action.UPDATE)
        assert perm.resource_type == ResourceType.DATASET
        assert perm.resource_id == "dataset_abc"
        assert perm.action == Action.UPDATE


class TestRolePermissions:
    """测试系统预定义角色的权限"""
    
    def test_admin_role_permissions(self):
        """测试 ADMIN 角色拥有所有权限"""
        admin = create_system_role(RoleType.ADMIN)
        
        # 应该拥有所有类型的权限
        for resource_type in ResourceType:
            for action in Action:
                perm = Permission(resource_type, action)
                assert admin.has_permission(perm), f"Admin should have {perm}"
    
    def test_viewer_role_permissions(self):
        """测试 VIEWER 角色只有读权限"""
        viewer = create_system_role(RoleType.VIEWER)
        
        # 应该拥有读权限
        read_perm = Permission(ResourceType.DATASET, Action.READ)
        assert viewer.has_permission(read_perm)
        
        # 不应该拥有写权限
        write_perm = Permission(ResourceType.DATASET, Action.UPDATE)
        assert not viewer.has_permission(write_perm)
    
    def test_editor_role_permissions(self):
        """测试 EDITOR 角色有读写但无删除权限"""
        editor = create_system_role(RoleType.EDITOR)
        
        # 有读写权限
        assert editor.has_permission(Permission(ResourceType.DATASET, Action.READ))
        assert editor.has_permission(Permission(ResourceType.DATASET, Action.CREATE))
        assert editor.has_permission(Permission(ResourceType.DATASET, Action.UPDATE))
        
        # 无删除权限
        assert not editor.has_permission(Permission(ResourceType.DATASET, Action.DELETE))
        
        # 无管理权限
        assert not editor.has_permission(Permission(ResourceType.DATASET, Action.ADMIN))


class TestUserRoleAssignment:
    """测试用户角色分配"""
    
    def test_assignment_expiration(self):
        """测试角色分配过期"""
        # 未过期的分配
        valid = UserRoleAssignment(
            user_id="user_1",
            role_id="role_1",
            resource_type=ResourceType.DATASET,
            expires_at=None
        )
        assert not valid.is_expired()
        
        # 已过期的分配
        expired = UserRoleAssignment(
            user_id="user_1",
            role_id="role_1",
            resource_type=ResourceType.DATASET,
            expires_at=datetime.utcnow() - timedelta(days=1)
        )
        assert expired.is_expired()
        
        # 未来过期的分配
        future = UserRoleAssignment(
            user_id="user_1",
            role_id="role_1",
            resource_type=ResourceType.DATASET,
            expires_at=datetime.utcnow() + timedelta(days=7)
        )
        assert not future.is_expired()


class TestRBACManager:
    """测试 RBAC 管理器（需要 mock 数据库）"""
    
    @pytest.fixture
    def rbac(self):
        """创建测试用 RBACManager"""
        return RBACManager(db_session=None)
    
    @pytest.mark.asyncio
    async def test_create_user(self, rbac):
        """测试创建用户"""
        user = await rbac.create_user(
            username="test_user",
            email="test@example.com",
            tenant_id="tenant_1"
        )
        
        assert user.username == "test_user"
        assert user.email == "test@example.com"
        assert user.tenant_id == "tenant_1"
        assert user.is_active is True
        assert len(user.id) > 0  # 生成了 UUID
    
    @pytest.mark.asyncio
    async def test_check_permission_with_superuser(self, rbac):
        """测试超级管理员绕过权限检查"""
        # 创建超级管理员并手动添加到缓存
        admin = await rbac.create_user(
            username="admin",
            is_superuser=True
        )
        
        # 手动添加到缓存（因为 mock 数据库不会持久化）
        rbac._user_cache[admin.id] = admin
        
        # 超级管理员应该有所有权限
        has_perm = await rbac.check_permission(
            admin.id,
            ResourceType.DATASET,
            Action.DELETE,
            "any_dataset"
        )
        assert has_perm
    
    @pytest.mark.asyncio
    async def test_tenant_roles_creation(self, rbac):
        """测试为租户创建角色"""
        tenant_id = "acme_corp"
        roles = await rbac.create_tenant_roles(tenant_id)
        
        # 应该创建 4 个角色
        assert len(roles) == 4
        assert RoleType.ADMIN in roles
        assert RoleType.VIEWER in roles
        
        # 角色 ID 应该包含租户 ID
        admin_role = roles[RoleType.ADMIN]
        assert tenant_id in admin_role.id


class TestPermissionDecorator:
    """测试权限装饰器"""
    
    def test_require_permission_decorator(self):
        """测试权限装饰器创建"""
        from bridge.auth import require_permission
        
        # 创建装饰器
        decorator = require_permission(
            ResourceType.DATASET,
            Action.READ,
            resource_id_param="dataset_id"
        )
        
        assert callable(decorator)


class TestNebulaIntegration:
    """测试 NebulaGraph 集成相关功能"""
    
    def test_temp_password_generation(self):
        """测试临时密码生成"""
        rbac = RBACManager(db_session=None)
        
        # 相同 user_id 应该生成相同密码
        pwd1 = rbac._generate_temp_password("user_123")
        pwd2 = rbac._generate_temp_password("user_123")
        assert pwd1 == pwd2
        
        # 不同 user_id 应该生成不同密码
        pwd3 = rbac._generate_temp_password("user_456")
        assert pwd1 != pwd3
        
        # 密码长度应该固定
        assert len(pwd1) == 16


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
