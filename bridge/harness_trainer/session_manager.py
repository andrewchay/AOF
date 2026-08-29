"""Agent Harness Trainer - 会话管理器.

管理驯化会话的生命周期：创建、迭代、评估、完成。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .models import (
    AssetSnapshot,
    HarnessSession,
    Iteration,
    SessionStatus,
)

logger = logging.getLogger(__name__)


class HarnessSessionManager:
    """驯化会话管理器."""
    
    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = Path(storage_path) if storage_path else Path("data/harness_sessions")
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, HarnessSession] = {}
    
    def create_session(
        self,
        problem_statement: str,
        pattern_type: str = "",
        domain: str = "",
        scenario: str = "",
        satisfaction_threshold: float = 4.0,
        initial_assets: Optional[AssetSnapshot] = None,
    ) -> HarnessSession:
        """创建新的驯化会话."""
        session = HarnessSession(
            problem_statement=problem_statement,
            pattern_type=pattern_type,
            domain=domain,
            scenario=scenario,
            satisfaction_threshold=satisfaction_threshold,
            initial_assets=initial_assets,
            current_assets=initial_assets,
        )
        self._sessions[session.id] = session
        self._persist(session)
        logger.info(f"[Harness] Created session {session.id} for: {problem_statement[:50]}")
        return session
    
    def get_session(self, session_id: str) -> Optional[HarnessSession]:
        """获取会话."""
        if session_id in self._sessions:
            return self._sessions[session_id]
        # 尝试从磁盘加载
        return self._load(session_id)
    
    def add_iteration(
        self,
        session_id: str,
        iteration: Iteration,
    ) -> HarnessSession:
        """向会话添加迭代，并评估是否满意."""
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        
        # 自动编号
        iteration.number = len(session.iterations) + 1
        
        # 评估满意度（单轮达标）
        iteration.is_satisfactory = iteration.expert_score.overall >= session.satisfaction_threshold
        
        session.iterations.append(iteration)
        
        # 更新会话状态
        if iteration.is_satisfactory:
            session.status = SessionStatus.SATISFIED
            logger.info(f"[Harness] Session {session_id} SATISFIED at iteration {iteration.number} "
                       f"(score: {iteration.expert_score.overall})")
        
        self._persist(session)
        return session
    
    def mark_abandoned(self, session_id: str) -> HarnessSession:
        """标记会话为放弃."""
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        session.status = SessionStatus.ABANDONED
        self._persist(session)
        return session
    
    def list_sessions(
        self,
        pattern_type: Optional[str] = None,
        status: Optional[SessionStatus] = None,
    ) -> list[HarnessSession]:
        """列会话，支持过滤."""
        sessions = list(self._sessions.values())
        if pattern_type:
            sessions = [s for s in sessions if s.pattern_type == pattern_type]
        if status:
            sessions = [s for s in sessions if s.status == status]
        return sessions
    
    def _persist(self, session: HarnessSession) -> None:
        """持久化会话到磁盘."""
        path = self.storage_path / f"{session.id}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
    
    def _load(self, session_id: str) -> Optional[HarnessSession]:
        """从磁盘加载会话."""
        path = self.storage_path / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                _ = json.load(f)
            # TODO: 从 dict 反序列化为 HarnessSession
            return None
        except Exception as e:
            logger.warning(f"[Harness] Failed to load session {session_id}: {e}")
            return None
