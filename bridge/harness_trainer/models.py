"""Agent Harness Trainer - 数据模型.

迭代式 Agent 能力驯化的核心数据模型。
设计决策（已确认）:
- Q1: 资产追踪 = 混合（MVP 隐式推断 → 长期显式声明）
- Q2: 评分维度 = 场景自定义权重
- Q3: 满意标准 = 单轮 overall >= threshold
- Q4: 归因混杂 = 标注置信度（MVP 不做控制实验）
- Q5: 跨问题复用 = 是，按 pattern_type 聚合
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class SessionStatus(str, Enum):
    """驯化会话状态."""
    ACTIVE = "active"
    SATISFIED = "satisfied"
    ABANDONED = "abandoned"


class AssetType(str, Enum):
    """资产类型."""
    ONTOLOGY_CLASS = "ontology_class"
    PLAYBOOK_STEP = "playbook_step"
    KNOWLEDGE_CHUNK = "knowledge_chunk"
    TOOL_SCHEMA = "tool_schema"


class ChangeType(str, Enum):
    """改动类型."""
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"


class UsageContext(str, Enum):
    """资产使用上下文."""
    ANSWER_REFERENCE = "answer_reference"      # 回答中直接引用
    REASONING = "reasoning"                    # 推理过程中使用
    TOOL_ARGUMENT = "tool_argument"            # 工具调用参数


# 默认场景权重配置
DEFAULT_WEIGHTS = {
    "structure": 0.2,
    "accuracy": 0.2,
    "completeness": 0.2,
    "style": 0.2,
    "reasoning": 0.2,
}

# 场景特定权重
SCENARIO_WEIGHTS = {
    "business_analysis": {
        "structure": 0.3,
        "accuracy": 0.2,
        "completeness": 0.2,
        "style": 0.1,
        "reasoning": 0.3,
    },
    "customer_service": {
        "structure": 0.1,
        "accuracy": 0.2,
        "completeness": 0.3,
        "style": 0.3,
        "reasoning": 0.1,
    },
    "legal": {
        "structure": 0.3,
        "accuracy": 0.3,
        "completeness": 0.2,
        "style": 0.1,
        "reasoning": 0.2,
    },
}


@dataclass
class AssetSnapshot:
    """资产快照（某时刻的完整资产状态）."""
    git_commit: str
    ontology_version: str
    playbook_version: str
    knowledge_base_version: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class AssetUsage:
    """资产使用记录（精细归因核心）."""
    asset_type: AssetType
    asset_id: str
    asset_name: str
    asset_version: str
    
    # 使用上下文
    usage_context: UsageContext
    usage_location: str = ""         # 在回答中的位置
    
    # 影响评估
    relevance_score: float = 0.0     # 与问题的相关度（0-1）
    is_critical: bool = False        # 是否是回答的关键支撑
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_type": self.asset_type.value,
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "asset_version": self.asset_version,
            "usage_context": self.usage_context.value,
            "usage_location": self.usage_location,
            "relevance_score": self.relevance_score,
            "is_critical": self.is_critical,
        }


@dataclass
class ExpertScore:
    """专家评分（支持场景自定义权重）."""
    # 各维度评分（1-5）
    structure: float = 0.0
    accuracy: float = 0.0
    completeness: float = 0.0
    style: float = 0.0
    reasoning: float = 0.0
    
    # 权重配置
    weights: dict[str, float] = field(default_factory=lambda: DEFAULT_WEIGHTS.copy())
    
    @property
    def overall(self) -> float:
        """加权总分."""
        total = (
            self.structure * self.weights.get("structure", 0.2) +
            self.accuracy * self.weights.get("accuracy", 0.2) +
            self.completeness * self.weights.get("completeness", 0.2) +
            self.style * self.weights.get("style", 0.2) +
            self.reasoning * self.weights.get("reasoning", 0.2)
        )
        return round(total, 2)
    
    @classmethod
    def for_scenario(cls, scenario: str) -> "ExpertScore":
        """创建场景特定的评分配置."""
        weights = SCENARIO_WEIGHTS.get(scenario, DEFAULT_WEIGHTS)
        return cls(weights=weights)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "structure": self.structure,
            "accuracy": self.accuracy,
            "completeness": self.completeness,
            "style": self.style,
            "reasoning": self.reasoning,
            "weights": self.weights,
            "overall": self.overall,
        }


@dataclass
class AssetChange:
    """资产改动记录."""
    change_type: ChangeType
    asset_type: AssetType
    asset_id: str
    
    before: Optional[str] = None
    after: Optional[str] = None
    diff_summary: str = ""
    affected_queries: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "change_type": self.change_type.value,
            "asset_type": self.asset_type.value,
            "asset_id": self.asset_id,
            "diff_summary": self.diff_summary,
            "affected_queries": self.affected_queries,
        }


@dataclass
class Iteration:
    """单次迭代记录."""
    number: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    # 资产状态
    asset_version: str = ""          # git commit hash
    asset_changes: list[AssetChange] = field(default_factory=list)
    
    # Agent 回答
    agent_response: str = ""
    trace: dict[str, Any] = field(default_factory=dict)  # 完整对话 trace
    
    # 资产使用追踪（精细归因核心）
    assets_used: list[AssetUsage] = field(default_factory=list)
    
    # 评估
    expert_score: ExpertScore = field(default_factory=ExpertScore)
    expert_feedback: str = ""
    is_satisfactory: bool = False
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "timestamp": self.timestamp,
            "asset_version": self.asset_version,
            "asset_changes": [c.to_dict() for c in self.asset_changes],
            "agent_response": self.agent_response,
            "trace": self.trace,
            "assets_used": [u.to_dict() for u in self.assets_used],
            "expert_score": self.expert_score.to_dict(),
            "expert_feedback": self.expert_feedback,
            "is_satisfactory": self.is_satisfactory,
        }


@dataclass
class AttributionDetail:
    """单条归因详情."""
    asset_change: AssetChange
    before_score: float = 0.0
    after_score: float = 0.0
    improvement: float = 0.0
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0          # 归因置信度
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_change": self.asset_change.to_dict(),
            "before_score": self.before_score,
            "after_score": self.after_score,
            "improvement": self.improvement,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }


@dataclass
class AttributionReport:
    """归因报告."""
    # 相关性统计
    change_type_correlation: dict[str, float] = field(default_factory=dict)
    asset_type_correlation: dict[str, float] = field(default_factory=dict)
    
    # 关键洞察
    key_insights: list[str] = field(default_factory=list)
    
    # 具体归因
    attribution_details: list[AttributionDetail] = field(default_factory=list)
    
    # 推荐
    recommended_next_changes: list[AssetChange] = field(default_factory=list)
    overall_confidence: float = 0.0   # 整体置信度
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "change_type_correlation": self.change_type_correlation,
            "asset_type_correlation": self.asset_type_correlation,
            "key_insights": self.key_insights,
            "attribution_details": [d.to_dict() for d in self.attribution_details],
            "overall_confidence": self.overall_confidence,
        }


@dataclass
class HarnessSession:
    """驯化会话（核心实体）."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    problem_statement: str = ""
    pattern_type: str = ""           # 问题模式类型（如"区域库存分析"）
    domain: str = ""                 # 领域标签
    scenario: str = ""               # 场景标识（用于权重配置）
    
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: SessionStatus = SessionStatus.ACTIVE
    satisfaction_threshold: float = 4.0
    
    # 资产快照
    initial_assets: Optional[AssetSnapshot] = None
    current_assets: Optional[AssetSnapshot] = None
    
    # 迭代历史
    iterations: list[Iteration] = field(default_factory=list)
    
    # 产出
    training_dataset_path: Optional[str] = None
    attribution_report: Optional[AttributionReport] = None
    
    @property
    def best_iteration(self) -> Optional[Iteration]:
        """满意度最高的迭代."""
        if not self.iterations:
            return None
        return max(self.iterations, key=lambda i: i.expert_score.overall)
    
    @property
    def satisfactory_iterations(self) -> list[Iteration]:
        """所有满意迭代."""
        return [i for i in self.iterations if i.is_satisfactory]
    
    @property
    def current_score(self) -> float:
        """最新迭代的评分."""
        if not self.iterations:
            return 0.0
        return self.iterations[-1].expert_score.overall
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "problem_statement": self.problem_statement,
            "pattern_type": self.pattern_type,
            "domain": self.domain,
            "scenario": self.scenario,
            "created_at": self.created_at,
            "status": self.status.value,
            "satisfaction_threshold": self.satisfaction_threshold,
            "current_score": self.current_score,
            "iteration_count": len(self.iterations),
            "satisfactory_count": len(self.satisfactory_iterations),
            "iterations": [i.to_dict() for i in self.iterations],
            "attribution_report": self.attribution_report.to_dict() if self.attribution_report else None,
        }
