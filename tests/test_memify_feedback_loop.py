# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""Cognee 1.5.4 Memify feedback adapter behavior."""

from __future__ import annotations

import hashlib
import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock

import pytest

from bridge.memify_feedback_loop import MemifyFeedbackLoop


@pytest.mark.asyncio
async def test_feedback_uses_current_memify_api_and_opaque_dataset_scope(monkeypatch):
    memify = AsyncMock()
    module = ModuleType("cognee.modules.memify")
    module.memify = memify
    monkeypatch.setitem(sys.modules, "cognee.modules.memify", module)

    feedback = {"session_id": "tenant/a/../../other", "feedback_text": "有用"}
    instance = MemifyFeedbackLoop.__new__(MemifyFeedbackLoop)
    await instance._save_to_cognee_memify(feedback)

    expected = hashlib.sha256(feedback["session_id"].encode()).hexdigest()[:24]
    memify.assert_awaited_once()
    call = memify.await_args.kwargs
    assert call["dataset"] == f"aof-feedback-{expected}"
    assert json.loads(call["data"][0]) == feedback
    assert "tenant/a" not in call["dataset"]
