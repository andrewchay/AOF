# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Fail-closed production configuration and readiness contracts."""

from __future__ import annotations

from bridge.semantic_core.production import ProductionReadiness


def test_production_readiness_rejects_missing_secrets_without_exposing_values() -> None:
    report = ProductionReadiness.evaluate(
        {
            "AOF_RUNTIME_MODE": "production",
            "AOF_SEMANTIC_IDENTITY_SECRET": "identity-secret-value",
        },
        capabilities={"otel_exporter": True},
    )

    assert report.ready is False
    assert {finding.code for finding in report.findings} == {
        "production_identity_key_id_default",
        "production_identity_secret_too_short",
        "production_otel_exporter_missing",
        "production_query_evidence_key_id_default",
        "production_query_evidence_secret_missing",
        "production_release_key_id_default",
        "production_release_secret_missing",
        "production_slo_targets_missing",
    }
    serialized = str(report.to_dict())
    assert "identity-secret-value" not in serialized


def test_production_readiness_rejects_reused_trust_domain_secrets(tmp_path) -> None:
    shared = "s" * 32
    slo = tmp_path / "slo.yaml"
    slo.write_text(
        "slo:\n  availability_error_rate_max: 0.01\n  latency_ms_p95_max: 800\n"
        "trusted_runtime_slo:\n  error_rate_max: 0.005\n  latency_ms_p95_max: 1200\n",
        encoding="utf-8",
    )

    report = ProductionReadiness.evaluate(
        {
            "AOF_RUNTIME_MODE": "production",
            "AOF_SEMANTIC_IDENTITY_SECRET": "i" * 32,
            "AOF_SEMANTIC_IDENTITY_KEY_ID": "identity-2026-08",
            "AOF_QUERY_EVIDENCE_SIGNING_SECRET": shared,
            "AOF_QUERY_EVIDENCE_SIGNING_KEY_ID": "query-2026-08",
            "AOF_RELEASE_SIGNING_SECRET": shared,
            "AOF_RELEASE_SIGNING_KEY_ID": "release-2026-08",
            "AOF_OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel:4318",
            "AOF_SLO_TARGETS_FILE": str(slo),
            "AOF_AGENTIC_RUN_DATABASE": str(tmp_path / "agentic.sqlite3"),
        },
        capabilities={"otel_exporter": True},
    )

    assert report.ready is False
    assert [item.code for item in report.findings] == [
        "production_trust_domain_secrets_reused"
    ]
    assert shared not in str(report.to_dict())


def test_readiness_endpoint_is_fail_closed_only_in_production(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import services.semantic_middle_layer_api.app as api_module

    client = TestClient(api_module.app)
    monkeypatch.setenv("AOF_RUNTIME_MODE", "production")
    blocked = client.get("/v1/ops/readiness")

    assert blocked.status_code == 503
    assert blocked.json()["ready"] is False
    assert blocked.json()["mode"] == "production"
    assert client.get("/healthz").status_code == 200
    protected = client.post(
        "/v1/semantic/evaluate",
        json={"topic": "sales", "candidate": "SELECT 1"},
    )
    assert protected.status_code == 503
    assert protected.json()["code"] == "production_not_ready"

    monkeypatch.setenv("AOF_RUNTIME_MODE", "development")
    development = client.get("/v1/ops/readiness")
    assert development.status_code == 200
    assert development.json()["ready"] is True


def test_explicit_distinct_production_configuration_is_ready(tmp_path) -> None:
    slo = tmp_path / "slo.yaml"
    slo.write_text(
        "slo:\n  availability_error_rate_max: 0.01\n  latency_ms_p95_max: 800\n"
        "trusted_runtime_slo:\n  error_rate_max: 0.005\n  latency_ms_p95_max: 1200\n",
        encoding="utf-8",
    )

    report = ProductionReadiness.evaluate(
        {
            "AOF_RUNTIME_MODE": "production",
            "AOF_SEMANTIC_IDENTITY_SECRET": "i" * 32,
            "AOF_SEMANTIC_IDENTITY_KEY_ID": "identity-2026-08",
            "AOF_QUERY_EVIDENCE_SIGNING_SECRET": "q" * 32,
            "AOF_QUERY_EVIDENCE_SIGNING_KEY_ID": "query-2026-08",
            "AOF_RELEASE_SIGNING_SECRET": "r" * 32,
            "AOF_RELEASE_SIGNING_KEY_ID": "release-2026-08",
            "AOF_OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel:4318",
            "AOF_SLO_TARGETS_FILE": str(slo),
            "AOF_AGENTIC_RUN_DATABASE": str(tmp_path / "agentic.sqlite3"),
        },
        capabilities={"otel_exporter": True},
    )

    assert report.ready is True
    assert report.findings == ()


def test_production_readiness_requires_trusted_runtime_slo(tmp_path) -> None:
    slo = tmp_path / "slo.yaml"
    slo.write_text(
        "slo:\n  availability_error_rate_max: 0.01\n  latency_ms_p95_max: 800\n",
        encoding="utf-8",
    )
    report = ProductionReadiness.evaluate(
        {
            "AOF_RUNTIME_MODE": "production",
            "AOF_SEMANTIC_IDENTITY_SECRET": "i" * 32,
            "AOF_SEMANTIC_IDENTITY_KEY_ID": "identity-2026-08",
            "AOF_QUERY_EVIDENCE_SIGNING_SECRET": "q" * 32,
            "AOF_QUERY_EVIDENCE_SIGNING_KEY_ID": "query-2026-08",
            "AOF_RELEASE_SIGNING_SECRET": "r" * 32,
            "AOF_RELEASE_SIGNING_KEY_ID": "release-2026-08",
            "AOF_OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel:4318",
            "AOF_SLO_TARGETS_FILE": str(slo),
            "AOF_AGENTIC_RUN_DATABASE": str(tmp_path / "agentic.sqlite3"),
        },
        capabilities={"otel_exporter": True},
    )

    assert [item.code for item in report.findings] == [
        "production_trusted_runtime_slo_invalid"
    ]


def test_unknown_runtime_mode_never_falls_back_to_development() -> None:
    report = ProductionReadiness.evaluate({"AOF_RUNTIME_MODE": "prodution"})

    assert report.ready is False
    assert [item.code for item in report.findings] == ["runtime_mode_invalid"]


def test_production_readiness_rejects_relative_agentic_database(tmp_path) -> None:
    slo = tmp_path / "slo.yaml"
    slo.write_text(
        "slo:\n  availability_error_rate_max: 0.01\n  latency_ms_p95_max: 800\n"
        "trusted_runtime_slo:\n  error_rate_max: 0.005\n  latency_ms_p95_max: 1200\n",
        encoding="utf-8",
    )
    report = ProductionReadiness.evaluate(
        {
            "AOF_RUNTIME_MODE": "production",
            "AOF_SEMANTIC_IDENTITY_SECRET": "i" * 32,
            "AOF_SEMANTIC_IDENTITY_KEY_ID": "identity-2026-09",
            "AOF_QUERY_EVIDENCE_SIGNING_SECRET": "q" * 32,
            "AOF_QUERY_EVIDENCE_SIGNING_KEY_ID": "query-2026-09",
            "AOF_RELEASE_SIGNING_SECRET": "r" * 32,
            "AOF_RELEASE_SIGNING_KEY_ID": "release-2026-09",
            "AOF_OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel:4318",
            "AOF_SLO_TARGETS_FILE": str(slo),
            "AOF_AGENTIC_RUN_DATABASE": "state/agentic.sqlite3",
        },
        capabilities={"otel_exporter": True},
    )
    assert [item.code for item in report.findings] == [
        "production_agentic_database_not_absolute"
    ]
