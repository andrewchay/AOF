"""
Procurement Caliber Governance — 闭环验证测试

验证采购支出控制领域的指标口径治理闭环是否满足 P1 验收标准：
1. 完整的审批流（提议 → 审核 → 批准 → 发布）
2. 决策溯源完整性
3. 权限矩阵（Separation of Duties）
4. 争议升级路径
5. 新指标创建
"""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import ResourceKind, SemanticResource
from bridge.semantic_core.compilers import default_compiler_registry
from bridge.semantic_core.governance import (
    SemanticGovernanceError,
    SemanticGovernancePolicy,
    SemanticGovernanceService,
)
from bridge.semantic_core.releases import SqliteReleaseRepository


class ProcurementGovernancePolicy(SemanticGovernancePolicy):
    """采购支出控制领域的 RBAC 策略。

    角色映射：
    - finance_analyst / department_requester：可提议口径变更
    - procurement_manager / internal_audit / finance_controller：可审核
    - cfo / procurement_director：可批准（cfo 为最终口径裁定人）
    - release_bot / admin：可编译与发布
    """

    _ROLES = {
        "create": {
            "finance_analyst", "procurement_manager", "department_requester",
            "it_manager", "rd_manager", "internal_audit", "cfo", "admin",
        },
        "validate": {
            "finance_analyst", "procurement_manager", "internal_audit",
            "it_manager", "rd_manager", "finance_controller", "admin",
        },
        "waive": {"cfo", "risk_owner", "admin"},
        "request_changes": {"procurement_manager", "internal_audit", "admin"},
        "approve": {"cfo", "procurement_director", "admin"},
        "compile": {"release_bot", "admin"},
        "publish": {"release_bot", "admin"},
    }


@pytest.fixture
def workspace():
    """创建临时工作区"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def governance_service(workspace):
    """创建治理服务（含编译器注册表与采购域 RBAC 策略）"""
    decision_store = DecisionProvenanceStore(workspace / "decisions.jsonl")
    release_repo = SqliteReleaseRepository(workspace / "releases.db")
    return SemanticGovernanceService(
        root=workspace,
        decision_store=decision_store,
        release_repository=release_repo,
        compiler_registry=default_compiler_registry(),
        access_policy=ProcurementGovernancePolicy(),
    )


@pytest.fixture
def historical_cases():
    """加载历史案例"""
    cases_file = Path(__file__).parent.parent / "data" / "historical_cases.yaml"
    with open(cases_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def semantic_resources():
    """加载语义资源"""
    resources = []
    for filename in ["semantic_resources.yaml", "objects_and_functions.yaml"]:
        resources_file = Path(__file__).parent.parent / "resources" / filename
        with open(resources_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)  # 单个文档，是列表
        for item in data:
            if item is None:
                continue
            parts = item["resource_id"].split("/")
            domain = parts[3]
            resource = SemanticResource.create(
                resource_id=item["resource_id"],
                kind=ResourceKind(item["kind"]),
                name=item["name"],
                domain=domain,
                owner=item["owner"],
                depends_on=item.get("depends_on", []),
                spec=item.get("spec", {}),
            )
            resources.append(resource)
    return resources


class TestApprovalWorkflow:
    """测试审批工作流完整性"""

    def test_complete_approval_cycle(self, governance_service, semantic_resources):
        """测试完整的审批周期：提议 → 审核 → 批准 → 发布"""
        # 准备资源
        metric = next(
            (r for r in semantic_resources if r.name == "dept_procurement_spend"),
            None
        )
        assert metric is not None, "dept_procurement_spend metric not found"

        # 1. 创建提议
        proposal = governance_service.create_proposal(
            proposal_id="test-prop-001",
            release_id="test-rel-001@v1",
            resources=[metric],
            actor="finance_analyst:test_user",
            rationale="测试口径变更",
        )
        assert proposal["state"] == "proposed"

        # 2. 审核
        review = governance_service.validate(
            "test-prop-001",
            actor="procurement_manager:test_reviewer"
        )
        assert review["state"] in {"review", "conflict_review"}

        # 3. 批准
        approval = governance_service.approve(
            "test-prop-001",
            actor="cfo:test_approver",
            rationale="批准变更"
        )
        assert approval["state"] == "approved"

        # 4. 编译（semantic-json 是默认注册的语义束 target）
        compile_result = governance_service.compile(
            "test-prop-001",
            actor="release_bot:test_compiler",
            targets=["semantic-json"]
        )
        assert compile_result["state"] == "ready"

        # 5. 发布
        publish = governance_service.publish(
            "test-prop-001",
            actor="release_bot:test_publisher"
        )
        assert publish["state"] == "published"

    def test_separation_of_duties(self, governance_service, semantic_resources):
        """测试职责分离：提议人不能批准自己的提议"""
        metric = next(
            (r for r in semantic_resources if r.name == "dept_procurement_spend"),
            None
        )

        # CFO 自己创建提议（cfo 有 create 权限）
        governance_service.create_proposal(
            proposal_id="test-prop-002",
            release_id="test-rel-002@v1",
            resources=[metric],
            actor="cfo:test_user",
            rationale="测试职责分离",
        )

        # 审核
        governance_service.validate(
            "test-prop-002",
            actor="procurement_manager:test_reviewer"
        )

        # 尝试自我批准（应该失败：同一个人即使有权批准，也不能批准自己提的）
        with pytest.raises(SemanticGovernanceError, match="separation of duties"):
            governance_service.approve(
                "test-prop-002",
                actor="cfo:test_user",  # 同一个人
                rationale="尝试自我批准"
            )

    def test_unauthorized_approval(self, governance_service, semantic_resources):
        """测试未授权批准：非 CFO 不能批准"""
        metric = next(
            (r for r in semantic_resources if r.name == "dept_procurement_spend"),
            None
        )

        governance_service.create_proposal(
            proposal_id="test-prop-003",
            release_id="test-rel-003@v1",
            resources=[metric],
            actor="finance_analyst:test_user",
            rationale="测试未授权批准",
        )

        governance_service.validate(
            "test-prop-003",
            actor="procurement_manager:test_reviewer"
        )

        # 尝试用非 CFO 角色批准（应该失败）
        with pytest.raises(SemanticGovernanceError, match="cannot approve"):
            governance_service.approve(
                "test-prop-003",
                actor="procurement_manager:test_manager",  # 不是 CFO
                rationale="尝试未授权批准"
            )


class TestDecisionProvenance:
    """测试决策溯源完整性"""

    def test_decision_trail_recorded(self, governance_service, semantic_resources, workspace):
        """测试决策轨迹被完整记录"""
        metric = next(
            (r for r in semantic_resources if r.name == "dept_procurement_spend"),
            None
        )

        # 执行完整审批流
        governance_service.create_proposal(
            proposal_id="test-prop-004",
            release_id="test-rel-004@v1",
            resources=[metric],
            actor="finance_analyst:test_user",
            rationale="测试决策溯源",
        )
        governance_service.validate("test-prop-004", actor="procurement_manager:test_reviewer")
        governance_service.approve("test-prop-004", actor="cfo:test_approver", rationale="批准")

        # 验证决策轨迹
        decisions_file = workspace / "decisions.jsonl"
        assert decisions_file.exists(), "决策轨迹文件不存在"

        decisions = []
        with open(decisions_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    decisions.append(json.loads(line))

        # 应该至少有 3 个决策：提议、审核、批准
        assert len(decisions) >= 3, f"决策轨迹不完整，只有 {len(decisions)} 条"

        # 验证决策类型
        decision_types = [d["decision"]["decision_type"] for d in decisions]
        assert "semantic_proposal" in decision_types
        assert "semantic_validation" in decision_types
        assert "semantic_approval" in decision_types

    def test_decision_parent_chain(self, governance_service, semantic_resources, workspace):
        """测试决策父链完整性"""
        metric = next(
            (r for r in semantic_resources if r.name == "dept_procurement_spend"),
            None
        )

        governance_service.create_proposal(
            proposal_id="test-prop-005",
            release_id="test-rel-005@v1",
            resources=[metric],
            actor="finance_analyst:test_user",
            rationale="测试决策父链",
        )
        governance_service.validate("test-prop-005", actor="procurement_manager:test_reviewer")
        governance_service.approve("test-prop-005", actor="cfo:test_approver", rationale="批准")

        decisions_file = workspace / "decisions.jsonl"
        decisions = []
        with open(decisions_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    decisions.append(json.loads(line))

        # 验证父链：每个决策都应该有父决策（除了第一个）
        for i, decision in enumerate(decisions):
            parent_ids = decision["decision"].get("parent_decision_ids", [])
            if i == 0:
                # 第一个决策可能没有父决策
                pass
            else:
                assert len(parent_ids) > 0, f"决策 {i} 缺少父决策"


class TestHistoricalCaseReplay:
    """测试历史案例回放"""

    def test_replay_all_cases(self, governance_service, historical_cases, semantic_resources):
        """回放所有历史案例"""
        assert len(historical_cases) == 15, "应该有 15 个历史案例"

        for case in historical_cases:
            # 为每个案例执行审批流
            metric = next(
                (r for r in semantic_resources if r.name == case["metric"]),
                None
            )
            if metric is None:
                # 创建新指标
                metric = SemanticResource.create(
                    resource_id=f"aof://acme/finance/metric/{case['metric']}",
                    kind=ResourceKind.METRIC,
                    name=case["metric"],
                    domain="finance",
                    owner="finance-team",
                    spec={
                        "description": case["title"],
                        "caliber_definition": case["resolution"]["decision"],
                        "version": "1.0.0",
                    }
                )

            proposal_id = f"prop-{case['case_id'].lower()}"
            release_id = f"rel-{case['case_id'].lower()}@v1"

            # 提议
            proposal = governance_service.create_proposal(
                proposal_id=proposal_id,
                release_id=release_id,
                resources=[metric],
                actor=f"{case['roles_involved'][0]}:test_user",
                rationale=case["dispute"],
            )
            assert proposal["state"] == "proposed"

            # 审核
            review = governance_service.validate(
                proposal_id,
                actor=f"{case['roles_involved'][1] if len(case['roles_involved']) > 1 else 'procurement_manager'}:test_reviewer"
            )
            assert review["state"] in {"review", "conflict_review"}

            # 批准
            approval = governance_service.approve(
                proposal_id,
                actor=f"{case['resolution']['decided_by']}:test_approver",
                rationale=case["resolution"]["rationale"],
            )
            assert approval["state"] == "approved"

    def test_case_outcomes_validated(self, historical_cases):
        """验证所有案例都有验证结果"""
        for case in historical_cases:
            assert case["outcome"] == "validated", f"案例 {case['case_id']} 未验证"
            assert "decided_by" in case["resolution"], f"案例 {case['case_id']} 缺少决策者"
            assert "rationale" in case["resolution"], f"案例 {case['case_id']} 缺少决策理由"


class TestPolicyEnforcement:
    """测试政策执行"""

    def test_metric_approval_required(self, semantic_resources):
        """测试需要审批的指标"""
        approval_required_metrics = [
            r for r in semantic_resources
            if r.kind == ResourceKind.METRIC and r.spec.get("approval_required", False)
        ]

        # 关键指标需要审批
        key_metrics = [
            "dept_procurement_spend",
            "framework_commitment",
            "framework_execution",
            "shared_service_allocation",
            "sample_procurement",
            "internal_transfer_spend",
        ]

        for metric_name in key_metrics:
            metric = next(
                (r for r in approval_required_metrics if r.name == metric_name),
                None
            )
            assert metric is not None, f"关键指标 {metric_name} 应该需要审批"

    def test_workflow_dependencies(self, semantic_resources):
        """测试工作流依赖完整性"""
        workflow = next(
            (r for r in semantic_resources if r.kind == ResourceKind.WORKFLOW),
            None
        )
        assert workflow is not None, "工作流资源不存在"

        # 验证工作流节点
        nodes = workflow.spec["nodes"]
        assert len(nodes) == 4, "工作流应该有 4 个节点"

        # 验证依赖关系
        node_ids = [n["node_id"] for n in nodes]
        assert "propose" in node_ids
        assert "review" in node_ids
        assert "approve" in node_ids
        assert "publish" in node_ids

        # 验证依赖链
        propose_node = next(n for n in nodes if n["node_id"] == "propose")
        review_node = next(n for n in nodes if n["node_id"] == "review")
        approve_node = next(n for n in nodes if n["node_id"] == "approve")
        publish_node = next(n for n in nodes if n["node_id"] == "publish")

        assert len(propose_node["depends_on"]) == 0, "propose 应该是起点"
        assert "propose" in review_node["depends_on"], "review 应该依赖 propose"
        assert "review" in approve_node["depends_on"], "approve 应该依赖 review"
        assert "approve" in publish_node["depends_on"], "publish 应该依赖 approve"


class TestMetricsCoverage:
    """测试指标覆盖度"""

    def test_all_dispute_types_covered(self, historical_cases, semantic_resources):
        """测试所有争议类型都有对应的指标"""
        # 从案例中提取所有指标
        case_metrics = {case["metric"] for case in historical_cases}

        # 从资源中提取所有指标
        resource_metrics = {
            r.name for r in semantic_resources
            if r.kind == ResourceKind.METRIC
        }

        # 验证覆盖
        uncovered = case_metrics - resource_metrics
        assert len(uncovered) == 0, f"以下指标未定义: {uncovered}"

    def test_new_metrics_created(self, historical_cases):
        """测试新指标创建"""
        new_metrics = {
            case["resolution"]["new_metric_created"]
            for case in historical_cases
            if "new_metric_created" in case["resolution"]
        }

        # 应该创建了多个新指标
        assert len(new_metrics) >= 5, f"应该创建至少 5 个新指标，实际只有 {len(new_metrics)}"

        # 验证新指标名称
        expected_new_metrics = {
            "admin_travel_spend",
            "prepayment_outflow",
            "framework_commitment",
            "shared_service_allocation",
            "pending_return",
            "fx_variance",
            "sample_procurement",
            "rebate_accrual",
            "internal_transfer_spend",
            "deposit_outflow",
            "commitment_schedule",
            "commitment_vs_execution",
        }

        assert new_metrics.issubset(expected_new_metrics), \
            f"未知的新指标: {new_metrics - expected_new_metrics}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
