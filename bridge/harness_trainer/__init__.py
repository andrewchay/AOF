# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Agent Harness Trainer - 迭代式 Agent 能力驯化.

将"测试问题 → 迭代资产 → 记录 trace → 专家评分 → 满意判定 → 训练数据"
模式产品化，支持精细归因和 Agent-Tool + 资产引用训练数据。

核心流程:
    1. 创建 HarnessSession（问题 + 初始资产快照）
    2. 执行 Iteration（Agent 回答 + 资产使用追踪）
    3. 专家评分（多维度 + 场景自定义权重）
    4. 满意判定（单轮 overall >= threshold）
    5. 归因分析（改动 → 效果，含置信度）
    6. 提取训练数据（Agent-Tool 格式，含资产引用）

使用示例:
    from bridge.harness_trainer import HarnessSessionManager, IterationEngine
    
    manager = HarnessSessionManager()
    session = manager.create_session(
        problem_statement="分析 Q3 华东区库存",
        pattern_type="区域库存分析",
        scenario="business_analysis",
    )
    
    engine = IterationEngine()
    iteration = engine.run_iteration(
        problem=session.problem_statement,
        agent_response="...",
        expert_score=ExpertScore(structure=4, accuracy=5, reasoning=4),
    )
    
    session = manager.add_iteration(session.id, iteration)
    if session.status == SessionStatus.SATISFIED:
        samples = TrainingDataExtractor().extract_from_session(session)
"""

from __future__ import annotations

from .models import (
    AssetChange,
    AssetSnapshot,
    AssetType,
    AssetUsage,
    AttributionDetail,
    AttributionReport,
    ChangeType,
    ExpertScore,
    HarnessSession,
    Iteration,
    SessionStatus,
    UsageContext,
    DEFAULT_WEIGHTS,
    SCENARIO_WEIGHTS,
)
from .explicit_asset_tracker import ExplicitAssetTracker
from .iteration_engine import IterationEngine
from .attribution_engine import AttributionEngine
from .training_extractor import TrainingDataExtractor
from .session_manager import HarnessSessionManager

__all__ = [
    # 模型
    "AssetChange",
    "AssetSnapshot",
    "AssetType",
    "AssetUsage",
    "AttributionDetail",
    "AttributionReport",
    "ChangeType",
    "ExpertScore",
    "HarnessSession",
    "Iteration",
    "SessionStatus",
    "UsageContext",
    "DEFAULT_WEIGHTS",
    "SCENARIO_WEIGHTS",
    # 引擎
    "HarnessSessionManager",
    "IterationEngine",
    "ExplicitAssetTracker",
    "AttributionEngine",
    "TrainingDataExtractor",
]
