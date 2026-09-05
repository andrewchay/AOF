"""Fail-closed production configuration and readiness evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .canonical import content_digest


@dataclass(frozen=True)
class ReadinessFinding:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ReadinessReport:
    mode: str
    ready: bool
    findings: tuple[ReadinessFinding, ...]
    report_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.production-readiness/v1",
            "mode": self.mode,
            "ready": self.ready,
            "findings": [item.to_dict() for item in self.findings],
            "report_digest": self.report_digest,
        }


class ProductionReadiness:
    """Evaluate deploy-time safety without serializing any secret material."""

    _KEYS = (
        (
            "identity",
            "AOF_SEMANTIC_IDENTITY_SECRET",
            "AOF_SEMANTIC_IDENTITY_KEY_ID",
            "identity-key-default",
        ),
        (
            "query_evidence",
            "AOF_QUERY_EVIDENCE_SIGNING_SECRET",
            "AOF_QUERY_EVIDENCE_SIGNING_KEY_ID",
            "query-evidence-key-default",
        ),
        (
            "release",
            "AOF_RELEASE_SIGNING_SECRET",
            "AOF_RELEASE_SIGNING_KEY_ID",
            "release-key-default",
        ),
    )

    @classmethod
    def evaluate(
        cls,
        environment: Mapping[str, str],
        *,
        capabilities: Mapping[str, bool] | None = None,
    ) -> ReadinessReport:
        mode = str(environment.get("AOF_RUNTIME_MODE", "development")).strip().lower()
        runtime_capabilities = dict(capabilities or cls._runtime_capabilities())
        findings: list[ReadinessFinding] = []
        if mode not in {"development", "test", "production"}:
            findings.append(
                ReadinessFinding(
                    "runtime_mode_invalid",
                    "AOF_RUNTIME_MODE must be development, test, or production",
                )
            )
        if mode == "production":
            configured_secrets = []
            for name, secret_name, key_name, default_key_id in cls._KEYS:
                secret = str(environment.get(secret_name, ""))
                key_id = str(environment.get(key_name, default_key_id)).strip()
                if not secret:
                    findings.append(
                        ReadinessFinding(
                            f"production_{name}_secret_missing",
                            f"{secret_name} is required in production",
                        )
                    )
                elif len(secret.encode("utf-8")) < 32:
                    findings.append(
                        ReadinessFinding(
                            f"production_{name}_secret_too_short",
                            f"{secret_name} must contain at least 32 bytes",
                        )
                    )
                else:
                    configured_secrets.append(secret)
                if not key_id or key_id == default_key_id:
                    findings.append(
                        ReadinessFinding(
                            f"production_{name}_key_id_default",
                            f"{key_name} must be explicitly versioned in production",
                        )
                    )
            if len(configured_secrets) != len(set(configured_secrets)):
                findings.append(
                    ReadinessFinding(
                        "production_trust_domain_secrets_reused",
                        "identity, release, and query evidence secrets must be distinct",
                    )
                )
            otel_endpoint = str(
                environment.get("AOF_OTEL_EXPORTER_OTLP_ENDPOINT", "")
            ).strip()
            if not otel_endpoint:
                findings.append(
                    ReadinessFinding(
                        "production_otel_exporter_missing",
                        "AOF_OTEL_EXPORTER_OTLP_ENDPOINT is required in production",
                    )
                )
            elif not runtime_capabilities.get("otel_exporter", False):
                findings.append(
                    ReadinessFinding(
                        "production_otel_exporter_unavailable",
                        "the configured OpenTelemetry exporter is not installed",
                    )
                )
            slo_path = str(environment.get("AOF_SLO_TARGETS_FILE", "")).strip()
            if not slo_path:
                findings.append(
                    ReadinessFinding(
                        "production_slo_targets_missing",
                        "AOF_SLO_TARGETS_FILE must be explicit in production",
                    )
                )
            else:
                cls._validate_slo_targets(Path(slo_path), findings)
            agentic_database = str(
                environment.get("AOF_AGENTIC_RUN_DATABASE", "")
            ).strip()
            if not agentic_database and environment.get('AOF_AGENTIC_ENABLED', 'false').lower() == 'true':
                findings.append(
                    ReadinessFinding(
                        "production_agentic_database_missing",
                        "AOF_AGENTIC_RUN_DATABASE must be explicit in production",
                    )
                )
            elif agentic_database and not Path(agentic_database).is_absolute():
                findings.append(
                    ReadinessFinding(
                        "production_agentic_database_not_absolute",
                        "AOF_AGENTIC_RUN_DATABASE must be an absolute durable path",
                    )
                )
        findings.sort(key=lambda item: item.code)
        payload = {
            "api_version": "aof.production-readiness/v1",
            "mode": mode,
            "ready": not findings,
            "findings": [item.to_dict() for item in findings],
        }
        return ReadinessReport(
            mode=mode,
            ready=not findings,
            findings=tuple(findings),
            report_digest=content_digest(payload),
        )

    @staticmethod
    def _runtime_capabilities() -> dict[str, bool]:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
                OTLPSpanExporter,
            )

            available = OTLPSpanExporter is not None
        except Exception:
            available = False
        return {"otel_exporter": available}

    @staticmethod
    def _validate_slo_targets(
        path: Path, findings: list[ReadinessFinding]
    ) -> None:
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            findings.append(
                ReadinessFinding(
                    "production_slo_targets_unreadable",
                    "AOF_SLO_TARGETS_FILE must be readable valid YAML",
                )
            )
            return
        slo = value.get("slo") if isinstance(value, Mapping) else None
        required = {"availability_error_rate_max", "latency_ms_p95_max"}
        if not isinstance(slo, Mapping) or not required.issubset(slo):
            findings.append(
                ReadinessFinding(
                    "production_slo_targets_invalid",
                    "production SLO targets must define availability and p95 latency",
                )
            )
        trusted = value.get("trusted_runtime_slo") if isinstance(value, Mapping) else None
        trusted_required = {"error_rate_max", "latency_ms_p95_max"}
        if not isinstance(trusted, Mapping) or not trusted_required.issubset(trusted):
            findings.append(
                ReadinessFinding(
                    "production_trusted_runtime_slo_invalid",
                    "production SLO targets must define trusted runtime error rate and p95 latency",
                )
            )
