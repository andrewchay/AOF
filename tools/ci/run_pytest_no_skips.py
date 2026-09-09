#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""Run a pytest profile and fail when any selected test is skipped."""

from __future__ import annotations

import sys
from typing import Any

import pytest


class NoSkipPlugin:
    def __init__(self) -> None:
        self.skipped: list[str] = []

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.skipped:
            self.skipped.append(report.nodeid)

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        if not self.skipped:
            return
        terminal = session.config.pluginmanager.get_plugin("terminalreporter")
        if terminal is not None:
            terminal.write_sep("=", "enterprise profile rejected skipped tests", red=True)
            for nodeid in self.skipped:
                terminal.write_line(f"SKIPPED {nodeid}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def main() -> int:
    plugin = NoSkipPlugin()
    return int(pytest.main(sys.argv[1:], plugins=[plugin]))


if __name__ == "__main__":
    sys.exit(main())
