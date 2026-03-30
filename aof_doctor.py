#!/usr/bin/env python3
"""AOF environment doctor: quick health checks for runtime prerequisites."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def load_spec(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def check_python_version(issues: list[str], infos: list[str]) -> None:
    v = sys.version_info
    infos.append(f"python={v.major}.{v.minor}.{v.micro}")
    if (v.major, v.minor) < (3, 10):
        issues.append("Python 版本过低，要求 >=3.10")
    if (v.major, v.minor) >= (3, 14):
        issues.append("Python 版本过高，cognee 当前要求 <3.14（建议 3.13）")


def check_llm_api_key(require_api_key: bool, issues: list[str], infos: list[str]) -> None:
    has_key = bool(os.environ.get("LLM_API_KEY"))
    infos.append(f"LLM_API_KEY={'set' if has_key else 'missing'}")
    if require_api_key and not has_key:
        issues.append("缺少环境变量 LLM_API_KEY")


def check_llm_provider_model(issues: list[str], infos: list[str]) -> None:
    provider = (os.environ.get("LLM_PROVIDER") or "").strip()
    model = (os.environ.get("LLM_MODEL") or "").strip()
    endpoint = (os.environ.get("LLM_ENDPOINT") or "").strip()

    infos.append(f"LLM_PROVIDER={provider or 'unset'}")
    infos.append(f"LLM_MODEL={model or 'unset'}")
    infos.append(f"LLM_ENDPOINT={'set' if endpoint else 'unset'}")

    if provider == "custom" and model and "/" not in model:
        issues.append(
            "LLM_PROVIDER=custom 时建议使用带 provider 前缀的模型名（如 deepseek/deepseek-chat）"
        )


def check_paths(spec: dict[str, Any], issues: list[str], infos: list[str]) -> str | None:
    cognee_root = (spec.get("cognee") or {}).get("root")
    if cognee_root:
        p = Path(cognee_root)
        infos.append(f"cognee.root={p}")
        if not p.exists():
            issues.append(f"cognee.root 不存在: {p}")
            return None
        if not p.is_dir():
            issues.append(f"cognee.root 不是目录: {p}")
            return None
    else:
        infos.append("cognee.root=<unset>")
    return cognee_root


def check_ontology(spec: dict[str, Any], issues: list[str], infos: list[str]) -> None:
    ontology_file = (spec.get("ontology") or {}).get("file")
    if not ontology_file:
        infos.append("ontology.file=<unset>")
        return

    p = Path(ontology_file)
    infos.append(f"ontology.file={p}")
    if not p.exists():
        issues.append(f"ontology.file 不存在: {p}")


def check_cognee_import(cognee_root: str | None, issues: list[str], infos: list[str]) -> None:
    added = False
    if cognee_root and cognee_root not in sys.path:
        sys.path.insert(0, cognee_root)
        added = True

    try:
        import cognee  # type: ignore  # noqa: F401
        infos.append("cognee.import=ok")
    except ModuleNotFoundError as e:
        issues.append(f"cognee 依赖缺失: {e.name}")
    except Exception as e:
        issues.append(f"cognee 导入失败: {e.__class__.__name__}: {e}")
    finally:
        if added:
            try:
                sys.path.remove(cognee_root)
            except ValueError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="AOF environment doctor")
    parser.add_argument("--spec", default="aof_spec.example.json")
    parser.add_argument(
        "--require-api-key",
        action="store_true",
        help="Fail when LLM_API_KEY is missing",
    )

    args = parser.parse_args()
    spec_path = Path(args.spec).resolve()
    if not spec_path.exists():
        print(f"[error] spec 不存在: {spec_path}")
        return 2

    spec = load_spec(spec_path)
    issues: list[str] = []
    infos: list[str] = [f"spec={spec_path}"]

    check_python_version(issues, infos)
    check_llm_api_key(args.require_api_key, issues, infos)
    check_llm_provider_model(issues, infos)
    cognee_root = check_paths(spec, issues, infos)
    check_ontology(spec, issues, infos)
    check_cognee_import(cognee_root, issues, infos)

    print("[doctor] 环境检查结果")
    for info in infos:
        print(f"- {info}")

    if issues:
        print("[doctor] FAIL")
        for issue in issues:
            print(f"- {issue}")
        return 2

    print("[doctor] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
