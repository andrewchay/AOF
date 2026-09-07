# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""训练数据生成器集合.

提供三种训练数据生成器:
- SFTGenerator: SFT 微调数据生成
- RAGEvalGenerator: RAG 评估数据生成
- AgentToolGenerator: Agent 工具调用数据生成
"""

from __future__ import annotations

from .base import GeneratorBase
from .sft import SFTGenerator
from .rag_eval import RAGEvalGenerator
from .agent_tool import AgentToolGenerator

__all__ = [
    "GeneratorBase",
    "SFTGenerator",
    "RAGEvalGenerator",
    "AgentToolGenerator",
]
