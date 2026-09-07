# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Metrics, correlation, SLO, and alerts for trusted runtime operations."""

from bridge.semantic_core.observability import TrustedRuntimeTelemetry


def test_trusted_runtime_telemetry_correlates_evidence_and_evaluates_slo() -> None:
    traced = []
    telemetry = TrustedRuntimeTelemetry(trace_sink=traced.append)
    telemetry.record(
        "query.execute",
        status="succeeded",
        latency_ms=25,
        correlation={
            "query_run_id": "query-001",
            "compilation_run_id": "compile-002",
            "release_id": "sales@1",
            "plan_digest": "sha256:plan",
        },
    )
    telemetry.record(
        "query.execute",
        status="failed",
        latency_ms=125,
        correlation={"query_run_id": "query-002", "error_type": "Timeout"},
    )

    snapshot = telemetry.snapshot(
        {"error_rate_max": 0.1, "latency_ms_p95_max": 100}
    )

    assert snapshot["counters"] == {"failed": 1, "succeeded": 1, "total": 2}
    assert snapshot["operations"]["query.execute"]["latency_ms_p95"] == 125
    assert {alert["code"] for alert in snapshot["alerts"]} == {
        "trusted_runtime_error_rate_breached",
        "trusted_runtime_latency_p95_breached",
    }
    assert traced[0]["query_run_id"] == "query-001"
    metrics = telemetry.prometheus()
    assert (
        'aof_trusted_operations_total{operation="query.execute",status="failed"} 1'
        in metrics
    )
    assert "aof_trusted_runtime_slo_breach 1" in telemetry.prometheus(
        {"error_rate_max": 0.1, "latency_ms_p95_max": 100}
    )


def test_trusted_runtime_telemetry_rejects_unknown_correlation_fields() -> None:
    telemetry = TrustedRuntimeTelemetry()
    telemetry.record(
        "release.promote",
        status="succeeded",
        latency_ms=1,
        correlation={"release_id": "sales@1", "secret": "must-not-leak"},
    )

    assert telemetry.snapshot()["last_correlation"] == {"release_id": "sales@1"}


def test_ops_endpoints_expose_trusted_runtime_slo_and_prometheus_alert(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import services.semantic_middle_layer_api.app as api_module

    telemetry = TrustedRuntimeTelemetry()
    telemetry.record(
        "query.execute",
        status="failed",
        latency_ms=50,
        correlation={"query_run_id": "query-failed"},
    )
    monkeypatch.setattr(api_module, "TRUSTED_RUNTIME_TELEMETRY", telemetry)
    monkeypatch.setattr(
        api_module,
        "SLO_TARGETS_CACHE",
        {"trusted_runtime_slo": {"error_rate_max": 0.01, "latency_ms_p95_max": 25}},
    )
    client = TestClient(api_module.app)

    snapshot = client.get("/v1/ops/trusted-runtime")
    metrics = client.get("/metrics")

    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "breached"
    assert len(snapshot.json()["alerts"]) == 2
    assert "aof_trusted_runtime_slo_breach 1" in metrics.text
