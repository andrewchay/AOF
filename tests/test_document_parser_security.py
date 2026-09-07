# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""document_parser 路径限域安全测试（防任意文件读取）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bridge.document_parser.security import (
    PathNotAllowedError,
    allowed_roots,
    validate_parse_path,
)

pytestmark = [pytest.mark.unit]


class TestValidateParsePath:
    def test_allows_data_dir(self, monkeypatch):
        # data/ 默认允许
        p = validate_parse_path("data/poc")
        assert p.is_relative_to(Path("data").resolve())

    def test_rejects_system_sensitive(self, monkeypatch):
        for bad in ("/etc/passwd", "/etc/hosts", "/var/log/system.log"):
            with pytest.raises(PathNotAllowedError):
                validate_parse_path(bad)

    def test_rejects_user_secrets_dir(self, monkeypatch):
        ssh = Path.home() / ".ssh"
        if ssh.exists():
            with pytest.raises(PathNotAllowedError):
                validate_parse_path(str(ssh / "id_rsa"))
        # 至少 SSH 目录本身应被拒（不在白名单且属系统敏感）
        with pytest.raises(PathNotAllowedError):
            validate_parse_path(str(ssh))

    def test_rejects_non_whitelisted_project_file(self, monkeypatch):
        # 项目根内的代码/dotfile 不在白名单（只 data/samples/tools 等）
        with pytest.raises(PathNotAllowedError):
            validate_parse_path("mcp_server.py")
        with pytest.raises(PathNotAllowedError):
            validate_parse_path(".env")

    def test_env_external_root_added(self, tmp_path, monkeypatch):
        ext = tmp_path / "extdocs"
        ext.mkdir()
        f = ext / "a.txt"
        f.write_text("x", encoding="utf-8")
        monkeypatch.setenv("AOF_PARSER_ALLOW_ROOTS", str(ext))
        p = validate_parse_path(str(f))
        assert p == f.resolve()

    def test_env_external_root_allows_subtree(self, tmp_path, monkeypatch):
        base = tmp_path / "root"
        sub = base / "nested" / "deep"
        sub.mkdir(parents=True)
        f = sub / "doc.md"
        f.write_text("# t", encoding="utf-8")
        monkeypatch.setenv("AOF_PARSER_ALLOW_ROOTS", str(base))
        assert validate_parse_path(str(f)) == f.resolve()


class TestAllowedRoots:
    def test_includes_default_data(self):
        roots = [str(r) for r in allowed_roots()]
        assert any(r.endswith("/data") for r in roots)

    def test_includes_env_roots(self, tmp_path, monkeypatch):
        extra = tmp_path / "extra"
        extra.mkdir()
        monkeypatch.setenv("AOF_PARSER_ALLOW_ROOTS", str(extra))
        roots = [str(r) for r in allowed_roots()]
        assert str(extra.resolve()) in roots
