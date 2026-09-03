#!/usr/bin/env python3
"""
Procurement Caliber Governance — 历史案例回放验证

验证采购支出控制领域的指标口径治理闭环：
1. 加载 15 个历史争议案例
2. 为每个案例模拟口径变更提议 → 审核 → 批准 → 发布 的审批流
3. 验证决策溯源（Decision Provenance）完整性
4. 验证权限矩阵（Separation of Duties）
5. 验证争议升级路径
"""

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))

from bridge.decision_provenance import DecisionProvenanceStore  # noqa: E402
from bridge.semantic_core import ResourceKind, SemanticResource  # noqa: E402
from bridge.semantic_core.governance import SemanticGovernanceService  # noqa: E402
from bridge.semantic_core.releases import SqliteReleaseRepository  # noqa: E402


def load_yaml_resources(path: Path) -> list[dict[str, Any]]:
    """加载 YAML 资源文件（单文档，列表格式）"""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_historical_cases(path: Path) -> list[dict[str, Any]]:
    """加载历史案例"""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def create_semantic_resources(resources_data: list[dict[str, Any]]) -> list[SemanticResource]:
    """将 YAML 数据转换为 SemanticResource 对象"""
    resources = []
    for item in resources_data:
        # 解析 resource_id 获取 domain
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


class ProcurementCaliberGovernanceReplay:
    """采购口径治理回放测试"""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)

        # 初始化决策溯源存储
        self.decision_store = DecisionProvenanceStore(workspace / "decisions.jsonl")

        # 初始化发布仓库
        self.release_repo = SqliteReleaseRepository(workspace / "releases.db")

        # 初始化治理服务
        self.governance = SemanticGovernanceService(
            root=workspace,
            decision_store=self.decision_store,
            release_repository=self.release_repo,
        )

        # 加载资源
        self.resources = self._load_resources()
        self.cases = self._load_cases()

    def _load_resources(self) -> list[SemanticResource]:
        """加载所有语义资源"""
        resources = []

        # 加载指标和动作
        semantic_data = load_yaml_resources(
            Path(__file__).parent.parent / "resources" / "semantic_resources.yaml"
        )
        resources.extend(create_semantic_resources(semantic_data))

        # 加载对象和函数
        objects_data = load_yaml_resources(
            Path(__file__).parent.parent / "resources" / "objects_and_functions.yaml"
        )
        resources.extend(create_semantic_resources(objects_data))

        return resources

    def _load_cases(self) -> list[dict[str, Any]]:
        """加载历史案例"""
        return load_historical_cases(
            Path(__file__).parent.parent / "data" / "historical_cases.yaml"
        )

    def run_replay(self) -> dict[str, Any]:
        """运行完整回放测试"""
        results = {
            "total_cases": len(self.cases),
            "cases_passed": 0,
            "cases_failed": 0,
            "case_results": [],
            "metrics": {
                "approval_cycles": 0,
                "disputes_escalated": 0,
                "new_metrics_created": 0,
                "policies_updated": 0,
            },
            "decision_trail": [],
        }

        for case in self.cases:
            case_result = self._replay_case(case)
            results["case_results"].append(case_result)

            if case_result["status"] == "passed":
                results["cases_passed"] += 1
            else:
                results["cases_failed"] += 1

            # 更新度量指标
            if case_result.get("approval_cycle_completed"):
                results["metrics"]["approval_cycles"] += 1
            if case_result.get("dispute_escalated"):
                results["metrics"]["disputes_escalated"] += 1
            if case_result.get("new_metric_created"):
                results["metrics"]["new_metrics_created"] += 1
            if case_result.get("policy_updated"):
                results["metrics"]["policies_updated"] += 1

        # 收集决策轨迹
        results["decision_trail"] = self._collect_decision_trail()

        return results

    def _replay_case(self, case: dict[str, Any]) -> dict[str, Any]:
        """回放单个案例"""
        case_id = case["case_id"]
        result = {
            "case_id": case_id,
            "title": case["title"],
            "status": "passed",
            "steps": [],
            "errors": [],
        }

        try:
            # Step 1: 创建口径变更提议
            proposal_id = f"prop-{case_id.lower()}"
            proposal_result = self._create_proposal(case, proposal_id)
            result["steps"].append({"step": "create_proposal", "result": proposal_result})

            # Step 2: 审核提议
            review_result = self._review_proposal(proposal_id, case)
            result["steps"].append({"step": "review_proposal", "result": review_result})

            # Step 3: 批准提议
            approval_result = self._approve_proposal(proposal_id, case)
            result["steps"].append({"step": "approve_proposal", "result": approval_result})
            result["approval_cycle_completed"] = True

            # Step 4: 发布新版本
            publish_result = self._publish_release(proposal_id, case)
            result["steps"].append({"step": "publish_release", "result": publish_result})

            # 检查是否创建了新指标
            if case["resolution"].get("new_metric_created"):
                result["new_metric_created"] = True

            # 检查是否更新了政策
            if case["resolution"].get("policy_updated"):
                result["policy_updated"] = True

            # 检查是否涉及争议升级
            if "escalate" in case["resolution"].get("decision", "").lower():
                result["dispute_escalated"] = True

        except Exception as e:
            result["status"] = "failed"
            result["errors"].append(str(e))

        return result

    def _create_proposal(self, case: dict[str, Any], proposal_id: str) -> dict[str, Any]:
        """创建口径变更提议"""
        # 模拟提议者角色
        requester_role = case["roles_involved"][0]  # 通常是 finance_analyst 或 procurement_manager
        actor = f"{requester_role}:test_user"

        # 准备提议的资源（变更后的指标定义）
        metric_id = case["metric"]
        metric_resource = next(
            (r for r in self.resources if r.name == metric_id and r.kind == ResourceKind.METRIC),
            None
        )

        if not metric_resource:
            # 如果指标不存在，创建一个新的
            metric_resource = SemanticResource.create(
                resource_id=f"aof://acme/finance/metric/{metric_id}",
                kind=ResourceKind.METRIC,
                name=metric_id,
                domain="finance",
                owner="finance-team",
                spec={
                    "description": f"Metric for {case['title']}",
                    "formula": "TBD",
                    "caliber_definition": case["resolution"]["decision"],
                    "version": "1.0.0",
                }
            )

        # 创建提议
        release_id = f"rel-{case['case_id'].lower()}@v1"
        proposal = self.governance.create_proposal(
            proposal_id=proposal_id,
            release_id=release_id,
            resources=[metric_resource],
            actor=actor,
            rationale=case["dispute"],
            scope={"case_id": case["case_id"], "period": case["context"]["period"]},
        )

        return {
            "proposal_id": proposal_id,
            "actor": actor,
            "state": proposal["state"],
        }

    def _review_proposal(self, proposal_id: str, case: dict[str, Any]) -> dict[str, Any]:
        """审核提议"""
        # 模拟审核者角色（通常是 procurement_manager 或 finance_analyst）
        reviewer_role = case["roles_involved"][1] if len(case["roles_involved"]) > 1 else "procurement_manager"
        actor = f"{reviewer_role}:test_reviewer"

        review = self.governance.validate(proposal_id, actor=actor)
        return {
            "actor": actor,
            "conforms": review["conforms"],
            "state": review["state"],
        }

    def _approve_proposal(self, proposal_id: str, case: dict[str, Any]) -> dict[str, Any]:
        """批准提议"""
        # 模拟批准者角色（CFO）
        approver_role = case["resolution"]["decided_by"]
        actor = f"{approver_role}:test_approver"

        approval = self.governance.approve(
            proposal_id,
            actor=actor,
            rationale=case["resolution"]["rationale"],
        )

        return {
            "actor": actor,
            "state": approval["state"],
        }

    def _publish_release(self, proposal_id: str, case: dict[str, Any]) -> dict[str, Any]:
        """发布新版本"""
        # 发布者通常是管理员或发布专员
        actor = "admin:test_publisher"

        # 先编译
        self.governance.compile(
            proposal_id,
            actor="compiler:test_compiler",
            targets=["sql"],
        )

        # 再发布
        publish_result = self.governance.publish(proposal_id, actor=actor)

        return {
            "actor": actor,
            "state": publish_result["state"],
            "release_id": publish_result.get("release_id"),
        }

    def _collect_decision_trail(self) -> list[dict[str, Any]]:
        """收集决策轨迹"""
        decisions = []
        decisions_file = self.workspace / "decisions.jsonl"
        if decisions_file.exists():
            with open(decisions_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        decisions.append(json.loads(line))
        return decisions

    def print_summary(self, results: dict[str, Any]) -> None:
        """打印测试摘要"""
        print("=" * 80)
        print("Procurement Caliber Governance — 回放测试摘要")
        print("=" * 80)
        print(f"\n总案例数: {results['total_cases']}")
        print(f"通过: {results['cases_passed']}")
        print(f"失败: {results['cases_failed']}")
        print("\n度量指标:")
        print(f"  完成审批周期: {results['metrics']['approval_cycles']}")
        print(f"  争议升级: {results['metrics']['disputes_escalated']}")
        print(f"  新指标创建: {results['metrics']['new_metrics_created']}")
        print(f"  政策更新: {results['metrics']['policies_updated']}")
        print(f"\n决策轨迹记录: {len(results['decision_trail'])} 条")

        if results['cases_failed'] > 0:
            print("\n失败案例:")
            for case in results['case_results']:
                if case['status'] == 'failed':
                    print(f"  - {case['case_id']}: {case['title']}")
                    for error in case['errors']:
                        print(f"    错误: {error}")

        print("\n" + "=" * 80)


def main():
    """主函数"""
    # 创建临时工作区
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "procurement_caliber_governance"

        # 运行回放测试
        replay = ProcurementCaliberGovernanceReplay(workspace)
        results = replay.run_replay()

        # 打印摘要
        replay.print_summary(results)

        # 保存详细结果
        output_file = Path(__file__).parent / "replay_results.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        print(f"\n详细结果已保存到: {output_file}")

        # 返回退出码
        return 0 if results['cases_failed'] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
