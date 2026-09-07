# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Agent Harness Trainer - REST API.

Phase 3: 产品化 API 端点，集成到 AOF Semantic Middle Layer API.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from bridge.harness_trainer import (
    AssetSnapshot,
    ExpertScore,
    HarnessSessionManager,
    IterationEngine,
    AttributionEngine,
    TrainingDataExtractor,
    SessionStatus,
)

router = APIRouter(prefix="/v1/harness", tags=["harness"])

# 全局会话管理器（生产环境应使用依赖注入）
_session_manager: Optional[HarnessSessionManager] = None


def _get_session_manager() -> HarnessSessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = HarnessSessionManager()
    return _session_manager


# ========== 请求/响应模型 ==========

class CreateSessionReq(BaseModel):
    problem_statement: str
    pattern_type: str = ""
    domain: str = ""
    scenario: str = ""
    satisfaction_threshold: float = 4.0
    initial_assets: Optional[dict[str, Any]] = None


class AddIterationReq(BaseModel):
    agent_response: str
    asset_version: str = ""
    asset_changes: list[dict[str, Any]] = Field(default_factory=list)
    expert_score: dict[str, float] = Field(default_factory=dict)
    expert_feedback: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class SessionResp(BaseModel):
    id: str
    problem_statement: str
    pattern_type: str
    domain: str
    scenario: str
    status: str
    current_score: float
    iteration_count: int
    satisfactory_count: int


# ========== API 端点 ==========

@router.post("/sessions", response_model=SessionResp)
async def create_session(req: CreateSessionReq) -> dict[str, Any]:
    """创建新的驯化会话."""
    manager = _get_session_manager()
    
    initial_assets = None
    if req.initial_assets:
        initial_assets = AssetSnapshot(**req.initial_assets)
    
    session = manager.create_session(
        problem_statement=req.problem_statement,
        pattern_type=req.pattern_type,
        domain=req.domain,
        scenario=req.scenario,
        satisfaction_threshold=req.satisfaction_threshold,
        initial_assets=initial_assets,
    )
    
    return {
        "id": session.id,
        "problem_statement": session.problem_statement,
        "pattern_type": session.pattern_type,
        "domain": session.domain,
        "scenario": session.scenario,
        "status": session.status.value,
        "current_score": session.current_score,
        "iteration_count": len(session.iterations),
        "satisfactory_count": len(session.satisfactory_iterations),
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    """获取会话详情."""
    manager = _get_session_manager()
    session = manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    
    return session.to_dict()


@router.post("/sessions/{session_id}/iterations")
async def add_iteration(session_id: str, req: AddIterationReq) -> dict[str, Any]:
    """向会话添加迭代."""
    manager = _get_session_manager()
    session = manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    
    # 构建 ExpertScore
    score = ExpertScore.for_scenario(session.scenario)
    for key in ["structure", "accuracy", "completeness", "style", "reasoning"]:
        if key in req.expert_score:
            setattr(score, key, req.expert_score[key])
    
    # 执行迭代
    engine = IterationEngine(enable_explicit_tracking=True)
    iteration = engine.run_iteration(
        problem=session.problem_statement,
        agent_response=req.agent_response,
        asset_version=req.asset_version,
        expert_score=score,
        expert_feedback=req.expert_feedback,
        tool_calls=req.tool_calls,
    )
    
    session = manager.add_iteration(session_id, iteration)
    
    return {
        "session_id": session.id,
        "iteration_number": iteration.number,
        "is_satisfactory": iteration.is_satisfactory,
        "current_score": session.current_score,
        "status": session.status.value,
    }


@router.get("/sessions/{session_id}/attribution")
async def get_attribution(session_id: str) -> dict[str, Any]:
    """获取会话的归因报告."""
    manager = _get_session_manager()
    session = manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    
    if len(session.iterations) < 2:
        return {"error": "至少需要 2 轮迭代才能生成归因报告"}
    
    engine = AttributionEngine()
    report = engine.analyze(session)
    
    return report.to_dict()


@router.post("/sessions/{session_id}/export")
async def export_training_data(session_id: str) -> dict[str, Any]:
    """导出会话的训练数据."""
    manager = _get_session_manager()
    session = manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    
    extractor = TrainingDataExtractor()
    samples = extractor.extract_from_session(session)
    
    return {
        "session_id": session_id,
        "sample_count": len(samples),
        "samples": [s.to_dict() for s in samples],
    }


@router.get("/sessions")
async def list_sessions(
    pattern_type: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """列会话."""
    manager = _get_session_manager()
    
    status_enum = None
    if status:
        try:
            status_enum = SessionStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    
    sessions = manager.list_sessions(pattern_type=pattern_type, status=status_enum)
    
    return [
        {
            "id": s.id,
            "problem_statement": s.problem_statement,
            "pattern_type": s.pattern_type,
            "status": s.status.value,
            "current_score": s.current_score,
            "iteration_count": len(s.iterations),
        }
        for s in sessions
    ]
