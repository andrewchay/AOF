"""Low-cardinality trusted-runtime metrics, trace correlation, and SLO checks."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any


_CORRELATION_FIELDS = {
    "channel",
    "compilation_run_id",
    "error_type",
    "plan_digest",
    "query_run_id",
    "release_digest",
    "release_id",
    "tenant_id",
}


class TrustedRuntimeTelemetry:
    """Process-local telemetry with bounded samples and no raw query or secret labels."""

    def __init__(
        self,
        *,
        trace_sink: Callable[[Mapping[str, str]], None] | None = None,
        sample_limit: int = 5_000,
    ) -> None:
        self._lock = threading.Lock()
        self._trace_sink = trace_sink
        self._counts: dict[tuple[str, str], int] = defaultdict(int)
        self._latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=sample_limit)
        )
        self._last_correlation: dict[str, str] = {}

    def set_trace_sink(
        self, trace_sink: Callable[[Mapping[str, str]], None] | None
    ) -> None:
        self._trace_sink = trace_sink

    def record(
        self,
        operation: str,
        *,
        status: str,
        latency_ms: float,
        correlation: Mapping[str, Any] | None = None,
    ) -> None:
        if status not in {"succeeded", "failed"}:
            raise ValueError("trusted operation status must be succeeded or failed")
        if not operation.strip() or latency_ms < 0:
            raise ValueError("operation and non-negative latency_ms are required")
        safe = {
            key: str(value)
            for key, value in (correlation or {}).items()
            if key in _CORRELATION_FIELDS and value is not None
        }
        with self._lock:
            self._counts[(operation, status)] += 1
            self._latencies[operation].append(float(latency_ms))
            self._last_correlation = safe
        if self._trace_sink is not None:
            try:
                self._trace_sink(safe)
            except Exception:
                pass

    @contextmanager
    def operation(
        self, name: str, correlation: Mapping[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        current = dict(correlation or {})
        try:
            yield current
        except Exception as exc:
            current["error_type"] = type(exc).__name__
            self.record(
                name,
                status="failed",
                latency_ms=(time.perf_counter() - started) * 1_000,
                correlation=current,
            )
            raise
        else:
            self.record(
                name,
                status="succeeded",
                latency_ms=(time.perf_counter() - started) * 1_000,
                correlation=current,
            )

    def snapshot(self, targets: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            counts = dict(self._counts)
            latencies = {key: list(value) for key, value in self._latencies.items()}
            last_correlation = dict(self._last_correlation)
        succeeded = sum(value for (__, status), value in counts.items() if status == "succeeded")
        failed = sum(value for (__, status), value in counts.items() if status == "failed")
        operations = {}
        for operation in sorted({name for name, __ in counts} | set(latencies)):
            samples = sorted(latencies.get(operation, ()))
            operations[operation] = {
                "succeeded": counts.get((operation, "succeeded"), 0),
                "failed": counts.get((operation, "failed"), 0),
                "latency_ms_p95": round(self._percentile(samples, 0.95), 3),
                "latency_ms_max": round(max(samples, default=0.0), 3),
            }
        total = succeeded + failed
        error_rate = failed / total if total else 0.0
        all_samples = sorted(value for values in latencies.values() for value in values)
        p95 = self._percentile(all_samples, 0.95)
        normalized_targets = dict(targets or {})
        alerts = []
        max_error = float(normalized_targets.get("error_rate_max", 1.0))
        max_p95 = float(normalized_targets.get("latency_ms_p95_max", float("inf")))
        if error_rate > max_error:
            alerts.append(
                {
                    "code": "trusted_runtime_error_rate_breached",
                    "severity": "critical",
                    "current": round(error_rate, 6),
                    "target": max_error,
                }
            )
        if p95 > max_p95:
            alerts.append(
                {
                    "code": "trusted_runtime_latency_p95_breached",
                    "severity": "warning",
                    "current": round(p95, 3),
                    "target": max_p95,
                }
            )
        return {
            "status": "breached" if alerts else "ok",
            "counters": {"failed": failed, "succeeded": succeeded, "total": total},
            "error_rate": round(error_rate, 6),
            "latency_ms_p95": round(p95, 3),
            "operations": operations,
            "targets": normalized_targets,
            "alerts": alerts,
            "last_correlation": last_correlation,
        }

    def prometheus(self, targets: Mapping[str, Any] | None = None) -> str:
        snapshot = self.snapshot(targets)
        lines = [
            "# HELP aof_trusted_operations_total Trusted runtime operations.",
            "# TYPE aof_trusted_operations_total counter",
        ]
        for operation, values in snapshot["operations"].items():
            for status in ("succeeded", "failed"):
                lines.append(
                    'aof_trusted_operations_total'
                    f'{{operation="{operation}",status="{status}"}} {values[status]}'
                )
            lines.append(
                'aof_trusted_operation_latency_ms_p95'
                f'{{operation="{operation}"}} {values["latency_ms_p95"]:.3f}'
            )
        lines.extend(
            [
                "# TYPE aof_trusted_runtime_error_rate_ratio gauge",
                f'aof_trusted_runtime_error_rate_ratio {snapshot["error_rate"]:.6f}',
                "# TYPE aof_trusted_runtime_slo_breach gauge",
                f'aof_trusted_runtime_slo_breach {1 if snapshot["alerts"] else 0}',
            ]
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        index = int(round((len(values) - 1) * percentile))
        return values[max(0, min(index, len(values) - 1))]


_TRUSTED_RUNTIME_TELEMETRY = TrustedRuntimeTelemetry()


def trusted_runtime_telemetry() -> TrustedRuntimeTelemetry:
    return _TRUSTED_RUNTIME_TELEMETRY
