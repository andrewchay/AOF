#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Graph Doctor - AOF 知识图谱健康检查模块.

受 GBrain 的 `gbrain doctor` 启发，本模块提供定期的图谱健康巡检，
帮助企业用户及时发现并修复知识腐烂问题.

检查项：
- 孤立节点 (orphan nodes)
- 缺失嵌入 (missing embeddings)
-  cognify 管道状态
- 数据集空数据
- 碎片化图谱 (过多连通分量)

使用示例:
    doctor = GraphDoctor(dataset_name="main_dataset")
    report = await doctor.full_check()
    print(report.summary)
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class HealthIssue:
    """单个健康问题."""
    severity: str  # "critical" | "warning" | "info"
    category: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class HealthReport:
    """健康检查报告."""
    dataset_name: str
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    score: int = 100  # 0-100
    issues: list[HealthIssue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        critical = sum(1 for i in self.issues if i.severity == "critical")
        warning = sum(1 for i in self.issues if i.severity == "warning")
        info = sum(1 for i in self.issues if i.severity == "info")
        return (
            f"[{self.dataset_name}] 健康评分: {self.score}/100 | "
            f"严重: {critical}, 警告: {warning}, 提示: {info}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "checked_at": self.checked_at.isoformat(),
            "score": self.score,
            "summary": self.summary,
            "stats": self.stats,
            "issues": [i.to_dict() for i in self.issues],
        }


class GraphDoctor:
    """AOF 图谱医生."""

    def __init__(
        self,
        dataset_name: str,
        dataset_id: Optional[str] = None,
        cognee_root: Optional[str] = None,
    ):
        self.dataset_name = dataset_name
        self.dataset_id = dataset_id
        self.cognee_root = cognee_root
        self._cognee_available = importlib.util.find_spec("cognee") is not None

    def _import_cognee(self):
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        if self.cognee_root:
            import sys
            root = str(Path(self.cognee_root).resolve())
            if root not in sys.path:
                sys.path.insert(0, root)
        import cognee  # type: ignore
        return cognee

    def _deduct_score(self, report: HealthReport, points: int) -> None:
        report.score = max(0, report.score - points)

    def _add_issue(
        self,
        report: HealthReport,
        severity: str,
        category: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        report.issues.append(HealthIssue(severity, category, message, details or {}))
        if severity == "critical":
            self._deduct_score(report, 20)
        elif severity == "warning":
            self._deduct_score(report, 10)

    # -----------------------------------------------------------------------
    # Individual checks
    # -----------------------------------------------------------------------
    async def check_dataset_exists(self, report: HealthReport) -> bool:
        """检查数据集是否存在且可访问."""
        try:
            from bridge.dataset_manager import DatasetManager

            manager = DatasetManager()
            datasets = await manager.list_datasets()
            names = [d.name for d in datasets]
            ids = [d.id for d in datasets]

            if self.dataset_name not in names and self.dataset_id not in ids:
                self._add_issue(
                    report, "critical", "dataset",
                    f"数据集不存在: {self.dataset_name}",
                    {"available_datasets": names[:20]},
                )
                return False

            # Resolve dataset_name -> UUID for downstream checks
            for d in datasets:
                if d.name == self.dataset_name:
                    self.dataset_id = d.id
                    break

            return True
        except Exception as e:
            self._add_issue(
                report, "critical", "dataset",
                f"无法列出数据集: {e}",
            )
            return False

    async def check_dataset_has_data(self, report: HealthReport) -> None:
        """检查数据集是否有原始数据."""
        try:
            from bridge.dataset_manager import DatasetManager

            manager = DatasetManager()
            datasets = await manager.list_datasets()
            target_ds = None
            for d in datasets:
                if d.name == self.dataset_name or d.id == self.dataset_id:
                    target_ds = d
                    break

            if target_ds is None:
                self._add_issue(
                    report, "critical", "dataset",
                    f"数据集 {self.dataset_name} 未找到",
                )
                return

            if target_ds.data_count == 0:
                self._add_issue(
                    report, "critical", "dataset",
                    f"数据集 {self.dataset_name} 中没有任何数据",
                    {"suggestion": "请先运行 aof_add.py 导入数据"},
                )
        except Exception as e:
            self._add_issue(
                report, "warning", "dataset",
                f"检查数据集数据存在性失败: {e}",
            )

    async def check_cognify_status(self, report: HealthReport) -> None:
        """检查 cognify 管道状态."""
        try:
            from bridge.dataset_manager import check_dataset_status

            target = self.dataset_id or self.dataset_name
            if not target:
                return

            status = await check_dataset_status(target)
            report.stats["cognify_status"] = status.status
            report.stats["cognify_progress"] = status.progress

            if status.status in ("failed", "error"):
                self._add_issue(
                    report, "critical", "cognify",
                    f"Cognify 管道失败: {status.message or '未知错误'}",
                    {"progress": status.progress},
                )
            elif status.status in ("running", "pending"):
                self._add_issue(
                    report, "info", "cognify",
                    f"Cognify 仍在运行中，当前进度: {status.progress}%",
                )
            elif status.status == "unknown":
                self._add_issue(
                    report, "warning", "cognify",
                    "无法获取 Cognify 状态，可能尚未运行",
                )
        except Exception as e:
            self._add_issue(
                report, "warning", "cognify",
                f"检查 Cognify 状态失败: {e}",
            )

    async def check_orphan_nodes(self, report: HealthReport) -> None:
        """检查孤立节点（度为 0）."""
        try:
            from bridge.graph_analytics import GraphAnalytics

            analyzer = GraphAnalytics(dataset_name=self.dataset_name)
            stats = await analyzer.compute_statistics()
            report.stats["node_count"] = stats.node_count
            report.stats["edge_count"] = stats.edge_count
            report.stats["density"] = stats.density
            report.stats["connected_components"] = stats.connected_components

            if stats.node_count == 0:
                self._add_issue(
                    report, "critical", "graph",
                    "图谱中没有节点",
                    {"suggestion": "确认 cognify 已成功完成"},
                )
                return

            if stats.edge_count == 0 and stats.node_count > 0:
                self._add_issue(
                    report, "critical", "graph",
                    f"图谱中有 {stats.node_count} 个节点，但没有任何边",
                    {"suggestion": "检查 ontology 配置和 cognify 日志"},
                )
                return

            # 估算孤儿节点：如果连通分量数量接近节点数，说明大量孤立
            if stats.node_count > 0:
                orphan_ratio = stats.connected_components / stats.node_count
                if orphan_ratio > 0.5 and stats.node_count > 10:
                    self._add_issue(
                        report, "warning", "graph",
                        f"图谱过于碎片化: {stats.connected_components} 个连通分量 / {stats.node_count} 节点",
                        {"orphan_ratio": round(orphan_ratio, 4)},
                    )

            # 精确查找度为 0 的节点
            try:
                all_nodes = await analyzer.pagerank(top_k=999999)
                orphan_count = sum(1 for n in all_nodes if n.degree == 0)
                report.stats["orphan_count"] = orphan_count

                if orphan_count > 0:
                    severity = "warning" if orphan_count < 50 else "critical"
                    self._add_issue(
                        report, severity, "graph",
                        f"发现 {orphan_count} 个孤立节点（没有任何关系）",
                        {
                            "orphan_count": orphan_count,
                            "total_nodes": stats.node_count,
                            "suggestion": "检查这些节点是否因 cognify 失败或缺少关系抽取规则而产生",
                        },
                    )
            except Exception:
                pass

        except Exception as e:
            self._add_issue(
                report, "warning", "graph",
                f"图谱分析失败: {e}",
            )

    async def check_graph_density(self, report: HealthReport) -> None:
        """检查图谱密度是否在合理范围."""
        density = report.stats.get("density")
        node_count = report.stats.get("node_count", 0)

        if density is None or node_count == 0:
            return

        if density < 0.0001 and node_count > 100:
            self._add_issue(
                report, "warning", "graph",
                f"图谱密度极低 ({density:.6f})，可能存在大量孤立或弱连接节点",
                {"suggestion": "检查数据质量和关系抽取配置"},
            )

    async def check_embedding_coverage(self, report: HealthReport) -> None:
        """检查向量搜索是否可用（作为嵌入覆盖率的代理）."""
        if not self._cognee_available:
            return

        try:
            cognee = self._import_cognee()
            from cognee.modules.search.types import SearchType  # type: ignore

            # Use SUMMARIES search as a proxy for embedding coverage
            # (RAG_COMPLETION may fail with dataset filtering in some Cognee versions)
            results = await cognee.search(
                query_type=SearchType.SUMMARIES,
                query_text="*",
                top_k=1,
            )
            has_results = bool(results and (isinstance(results, list) and len(results) > 0))
            report.stats["embedding_search_works"] = has_results

            if not has_results:
                self._add_issue(
                    report, "warning", "embedding",
                    "向量搜索未返回结果，可能缺少 embeddings",
                    {"suggestion": "重新运行 cognify，确保 vector db 配置正确"},
                )
        except Exception as e:
            self._add_issue(
                report, "warning", "embedding",
                f"向量搜索检查失败: {e}",
            )

    async def check_stale_entities(self, report: HealthReport) -> None:
        """检查长期未更新的实体（需要 Cognee 支持时间戳）."""
        # AOF/Cognee 目前不保证每个节点都有更新时间戳，此检查为启发式
        # 如果未来 Cognee 暴露节点元数据，可以精确实现
        pass

    async def check_broken_references(self, report: HealthReport) -> None:
        """检查是否存在指向不存在的三元组（需要底层图引擎支持）."""
        # 当前 Cognee 后端通常保证引用完整性，此检查为占位
        # 如果切换到 NebulaGraph 等开放后端，可实现 Cypher 查询验证
        pass

    # -----------------------------------------------------------------------
    # Full check
    # -----------------------------------------------------------------------
    async def full_check(self) -> HealthReport:
        """运行完整的健康检查."""
        report = HealthReport(dataset_name=self.dataset_name)

        exists = await self.check_dataset_exists(report)
        if not exists:
            return report

        await self.check_dataset_has_data(report)
        await self.check_cognify_status(report)
        await self.check_orphan_nodes(report)
        await self.check_graph_density(report)
        await self.check_embedding_coverage(report)
        await self.check_stale_entities(report)
        await self.check_broken_references(report)

        return report

    async def check_and_print(self) -> HealthReport:
        """运行检查并打印摘要."""
        report = await self.full_check()
        print(report.summary)
        for issue in report.issues:
            prefix = {"critical": "❌", "warning": "⚠️", "info": "ℹ️"}.get(issue.severity, "•")
            print(f"  {prefix} [{issue.category}] {issue.message}")
        return report


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------
async def run_health_check(
    dataset_name: str,
    dataset_id: Optional[str] = None,
    cognee_root: Optional[str] = None,
) -> HealthReport:
    """便捷函数：运行健康检查."""
    doctor = GraphDoctor(
        dataset_name=dataset_name,
        dataset_id=dataset_id,
        cognee_root=cognee_root,
    )
    return await doctor.check_and_print()
