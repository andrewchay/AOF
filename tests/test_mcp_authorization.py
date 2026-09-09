# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Transport-level authentication, authorization, and identity binding for MCP."""

from __future__ import annotations

import asyncio
import json

import pytest


def _headers(*, role: str, subject: str = "alice", tenant: str = "acme") -> dict[str, str]:
    from bridge.semantic_core.identity import SignedPrincipalVerifier

    return SignedPrincipalVerifier(
        key_id="mcp-test-key", secret=b"mcp-test-secret"
    ).sign_headers(subject=subject, tenant_id=tenant, roles=[role])


@pytest.fixture()
def secured_server(tmp_path, monkeypatch):
    import mcp_server

    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "mcp-test-secret")
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_KEY_ID", "mcp-test-key")
    monkeypatch.setenv("AOF_REVOCATIONS_FILE", str(tmp_path / "revocations.sqlite"))
    return mcp_server.build_server()


def _call(server, name: str, arguments: dict) -> dict:
    return asyncio.run(server._handle_tool_call(name, arguments))


def _error_payload(result: dict) -> dict:
    assert result["isError"] is True
    return json.loads(result["content"][0]["text"])


def test_every_mcp_schema_requires_signed_principal(secured_server):
    for tool in secured_server.tools.values():
        assert "principal_headers" in tool.input_schema["properties"]
        assert "principal_headers" in tool.input_schema["required"]


def test_missing_identity_and_insufficient_role_fail_closed(secured_server):
    missing = _call(secured_server, "aof_health_check", {"dataset_name": "x"})
    denied = _call(
        secured_server,
        "aof_record_decision",
        {"principal_headers": _headers(role="viewer")},
    )

    assert _error_payload(missing)["code"] == "authentication_required"
    assert _error_payload(denied)["code"] == "operation_not_permitted"


def test_unknown_tool_and_invalid_arguments_have_stable_codes(secured_server):
    unknown = _call(secured_server, "aof_not_registered", {})
    invalid = _call(
        secured_server,
        "aof_health_check",
        {"principal_headers": _headers(role="viewer")},
    )

    assert _error_payload(unknown)["code"] == "tool_not_found"
    assert _error_payload(invalid)["code"] == "invalid_arguments"


def test_server_binds_decision_identity_and_rejects_spoofing(secured_server):
    captured: list[dict] = []

    async def capture(arguments: dict) -> dict:
        captured.append(arguments)
        return {"accepted": True}

    secured_server.tools["aof_record_decision"].handler = capture
    signed = _headers(role="operator", subject="real-user", tenant="tenant-a")
    payload = {
        "decision_type": "test",
        "conclusion": "accepted",
        "rationale": "transport identity test",
    }

    spoofed = _call(
        secured_server,
        "aof_record_decision",
        {**payload, "agent_id": "attacker", "principal_headers": signed},
    )
    accepted = _call(
        secured_server,
        "aof_record_decision",
        {**payload, "principal_headers": signed},
    )

    assert _error_payload(spoofed)["code"] == "operation_not_permitted"
    assert accepted["isError"] is False
    assert captured == [{
        **payload,
        "agent_id": "real-user",
        "tenant_id": "tenant-a",
    }]


@pytest.mark.parametrize("kind,identity", [("subject", "alice"), ("tenant", "acme")])
def test_revocation_takes_effect_on_next_mcp_call(secured_server, kind, identity):
    from bridge.access.revocations import RevocationRegistry

    async def healthy(_arguments: dict) -> dict:
        return {"status": "ok"}

    secured_server.tools["aof_health_check"].handler = healthy
    arguments = {
        "dataset_name": "x",
        "principal_headers": _headers(role="viewer"),
    }
    assert _call(secured_server, "aof_health_check", arguments)["isError"] is False

    RevocationRegistry().revoke(kind, identity, reason="security test")
    revoked = _call(secured_server, "aof_health_check", arguments)
    assert _error_payload(revoked)["code"] == "authentication_required"


def test_handler_failure_is_stable_and_does_not_leak_traceback(secured_server):
    async def fail(_arguments: dict) -> dict:
        raise RuntimeError("controlled failure")

    secured_server.tools["aof_health_check"].handler = fail
    result = _call(
        secured_server,
        "aof_health_check",
        {"dataset_name": "x", "principal_headers": _headers(role="viewer")},
    )
    payload = _error_payload(result)
    assert payload == {"code": "tool_execution_failed", "detail": "controlled failure"}
    assert "Traceback" not in result["content"][0]["text"]
