"""解析路径安全校验（防任意文件读取）。

document_parser 的对外能力（API /v1/documents/parse、MCP aof_document_parse）
接收用户提供的文件路径。为避免任意文件读取漏洞（读取 /etc/passwd、.env、.ssh 等），
对路径做**根目录白名单限域**：

- 允许：位于已授权的白名单根目录内的文件。默认含 AOF 的采集/存储目录
  （data/、tools/retrieval_gain/dataset/、samples/ 等），可用环境变量
  ``AOF_PARSER_ALLOW_ROOTS``（冒号分隔绝对路径）扩展。
- 拒绝：路径 resolve 后不在任何允许根内，或落在系统敏感目录
  （/etc、/var、~/.ssh、/usr 等）。

用法：调用 ``validate_parse_path(path)``，通过返回 resolve 后的 Path，失败抛
``PathNotAllowedError``。
"""

from __future__ import annotations

import os
from pathlib import Path

# 系统敏感根目录（即使假设兜底也不允许）
_SENSITIVE_ROOTS = {
    "/etc",
    "/var",
    "/usr",
    "/bin",
    "/sbin",
    "/opt",
    "/dev",
    "/proc",
    "/sys",
    "/Library",
}


class PathNotAllowedError(PermissionError):
    """路径不在允许解析的白名单根目录内。"""


def allowed_roots() -> list[Path]:
    """计算允许解析的根目录列表（默认 + 环境变量扩展）。"""
    roots: list[Path] = []
    # 默认：AOF 项目内的采集/存储/样例目录
    project = _project_root()
    for sub in ("data", "samples", "test_data", "tools", "workspace-files"):
        roots.append(project / sub)
    # 环境变量扩展（绝对路径列表，逗号/冒号分隔）
    env = os.environ.get("AOF_PARSER_ALLOW_ROOTS", "")
    for part in env.replace(",", ":").split(":"):
        part = part.strip()
        if part:
            roots.append(Path(part).resolve())
    # 去重 + 保留存在的
    seen: set[Path] = set()
    cleaned: list[Path] = []
    for r in roots:
        rr = r.resolve()
        if rr not in seen:
            seen.add(rr)
            cleaned.append(rr)
    return cleaned


def _project_root() -> Path:
    # AOF 项目根：bridge/document_parser/security.py → parents[2]
    return Path(__file__).resolve().parents[2]


def validate_parse_path(path: str | Path) -> Path:
    """校验解析路径是否被允许。通过返回 resolve 后的路径，失败抛 PathNotAllowedError。"""
    p = Path(path).expanduser().resolve()

    # 硬拒：系统敏感根
    for sr in _SENSITIVE_ROOTS:
        if p.is_relative_to(Path(sr)) or str(p).startswith(sr + "/"):
            raise PathNotAllowedError(f"禁止解析系统敏感路径: {p}")

    # 白名单根目录检查
    for root in allowed_roots():
        try:
            if p.is_relative_to(root):
                return p
        except (ValueError, OSError):
            continue

    raise PathNotAllowedError(f"路径不在允许解析的根目录内: {p}")
