# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""解析结果缓存（基于文件内容指纹 Blake2b）。

复用增量加载器的 Blake2b 指纹思路：文件未变则不重复解析，避免大文档反复烧资源。
缓存键 = （文件内容指纹 + 引擎名 + 解析参数），保证引擎升级/参数变更后自动失效。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any


def blake2b_file(path: Path, digest_size: int = 32) -> str:
    """计算文件内容 Blake2b 指纹（分块，适合大文件）。"""
    h = hashlib.blake2b(digest_size=digest_size)
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class ParseCache:
    """基于文件指纹的磁盘缓存（JSON，追加式）。

    设计要点：
    - 目录结构：``<cache_dir>/<sha256(缓存键)>.json``
    - 键 = blake2b(文件) + 引擎 + 语言 + 引擎版本标记
    - 命中时直接返回缓存 ParsedDoc，避免再次调隔离子进程。
    """

    def __init__(
        self, cache_dir: str | os.PathLike | None = None, max_entries: int = 1000
    ):
        self.cache_dir = Path(cache_dir or self._default_dir())
        self.max_entries = max_entries
        self._lock = threading.RLock()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _default_dir() -> Path:
        base = os.environ.get("AOF_CACHE_DIR", "data/parse_cache")
        return Path(base)

    def _cache_file(self, key_hash: str) -> Path:
        return self.cache_dir / f"{key_hash}.json"

    def build_key(
        self, *, file_path: Path, fingerprint: str, engine: str, lang: str
    ) -> str:
        """构建缓存键，返回 sha256 字符串。"""
        raw = json.dumps(
            {
                "file": str(file_path.resolve()),
                "fingerprint": fingerprint,
                "engine": engine,
                "lang": lang,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key_hash: str) -> dict[str, Any] | None:
        """命中返回缓存 dict（含 content/metadata），未命中返回 None。"""
        f = self._cache_file(key_hash)
        try:
            if not f.exists():
                return None
            with self._lock:
                data = json.loads(f.read_text(encoding="utf-8"))
            return data
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key_hash: str, data: dict[str, Any]) -> None:
        """写入缓存。超过 max_entries 时简单清理最旧文件。"""
        with self._lock:
            try:
                (self._cache_file(key_hash)).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError:
                return
            self._maybe_trim()

    def _maybe_trim(self) -> None:
        try:
            files = sorted(
                self.cache_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if len(files) > self.max_entries:
                for old in files[self.max_entries :]:
                    old.unlink(missing_ok=True)
        except OSError:
            pass

    def clear(self) -> int:
        """清空缓存，返回清理条数。"""
        with self._lock:
            removed = 0
            for f in self.cache_dir.glob("*.json"):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
            return removed
