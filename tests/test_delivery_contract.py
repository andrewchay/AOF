# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_image_builds_the_browser_and_runs_as_non_root():
    dockerfile = (ROOT / "services/semantic_middle_layer_api/Dockerfile").read_text()
    assert "FROM node:22-alpine AS web-builder" in dockerfile
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "COPY --from=web-builder" in dockerfile
    assert "USER aof" in dockerfile
    assert "/healthz" in dockerfile
    assert "apt-get" not in dockerfile
    assert "urllib.request" in dockerfile


def test_production_compose_requires_every_readiness_secret():
    compose = (ROOT / "services/semantic_middle_layer_api/docker-compose.prod.yml").read_text()
    for variable in (
        "AOF_SEMANTIC_IDENTITY_SECRET",
        "AOF_SEMANTIC_IDENTITY_KEY_ID",
        "AOF_QUERY_EVIDENCE_SIGNING_SECRET",
        "AOF_QUERY_EVIDENCE_SIGNING_KEY_ID",
        "AOF_RELEASE_SIGNING_SECRET",
        "AOF_RELEASE_SIGNING_KEY_ID",
        "AOF_OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        assert f"${{{variable}:?" in compose
    assert "AOF_RUNTIME_MODE=production" in compose
    assert "UVICORN_WORKERS=1" in compose


def test_checked_in_slo_targets_cover_the_production_readiness_contract():
    targets = yaml.safe_load(
        (ROOT / "config/observability/slo_targets.yaml").read_text()
    )
    assert set(targets["slo"]) >= {
        "availability_error_rate_max",
        "latency_ms_p95_max",
    }
    assert set(targets["trusted_runtime_slo"]) >= {
        "error_rate_max",
        "latency_ms_p95_max",
    }


def test_browser_shell_is_public_so_oidc_can_start_in_strict_mode():
    app_source = (
        ROOT / "services/semantic_middle_layer_api/app.py"
    ).read_text()
    assert "_WEB_PUBLIC_PATHS" in app_source
    assert "path.startswith('/assets/')" in app_source
