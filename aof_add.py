#!/usr/bin/env python3
"""AOF add runner: add data first, optionally run cognify."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge.cognee_add_runner import run_add_from_spec
from bridge.cognee_runner import run_cognify_from_spec
from bridge.errors import AOFBridgeError
from bridge.path_resolver import resolve_data_path, resolve_result_path
from bridge.preflight import ensure_preflight_for_add, ensure_preflight_for_cognify


def load_spec(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return repr(value)


def save_result_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(_json_safe(payload), f, ensure_ascii=False, indent=2)
    except PermissionError:
        fallback = Path("/tmp") / path.name
        with fallback.open("w", encoding="utf-8") as f:
            json.dump(_json_safe(payload), f, ensure_ascii=False, indent=2)
        print(f"[warn] 无法写入结果文件，已回落到: {fallback}")


def collect_data_args(args: argparse.Namespace, spec: dict) -> tuple[list[str], Any]:
    data_inputs: list[str] = []
    data_paths: list[str] = []

    if args.data:
        data_inputs.extend(args.data)
    if args.data_path:
        # 解析相对路径（支持 knowledge_repo）
        resolved = [str(resolve_data_path(p, spec)) for p in args.data_path]
        data_paths.extend(resolved)

    if not data_inputs and not data_paths:
        raise ValueError("至少提供 --data 或 --data-path")

    combined = data_inputs + data_paths
    payload: Any = combined if len(combined) > 1 else combined[0]
    return data_paths, payload


async def run_pipeline(
    spec: dict,
    payload: Any,
    dry_run: bool,
    run_cognify: bool,
    skip_preflight: bool,
):
    result: dict[str, Any] = {}

    if dry_run:
        result["add"] = {"dry_run": True, "payload_preview": repr(payload)}
        if run_cognify:
            result["cognify"] = {"dry_run": True}
        return result

    add_res = await run_add_from_spec(spec=spec, data=payload)
    result["add"] = add_res

    if run_cognify:
        if not skip_preflight:
            ensure_preflight_for_cognify(spec, require_api_key=True)
        cognify_res = await run_cognify_from_spec(spec)
        result["cognify"] = cognify_res

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="AOF add runner")
    parser.add_argument("--spec", default="aof_spec.example.json")
    parser.add_argument("--data", action="append", help="Inline text data, can repeat")
    parser.add_argument("--data-path", action="append", help="File/dir path, can repeat")
    parser.add_argument("--run-cognify", action="store_true", help="Run cognify after add")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--result-file", default="logs/aof_add_result.json")

    args = parser.parse_args()
    spec_path = Path(args.spec).resolve()
    spec = load_spec(spec_path)

    project_root = spec.get("project_root") or str(Path(__file__).resolve().parent)
    result_path = resolve_result_path(args.result_file, spec)

    knowledge_repo = spec.get("knowledge_repo")
    report: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "spec": str(spec_path),
        "project_root": project_root,
        "knowledge_repo": knowledge_repo,
        "dry_run": args.dry_run,
        "stages": {},
    }

    code = 0
    try:
        data_paths, payload = collect_data_args(args, spec)
        if not args.skip_preflight and not args.dry_run:
            ensure_preflight_for_add(spec, data_paths=data_paths, require_api_key=True)

        stages = asyncio.run(
            run_pipeline(
                spec=spec,
                payload=payload,
                dry_run=args.dry_run,
                run_cognify=args.run_cognify,
                skip_preflight=args.skip_preflight,
            )
        )
        report["stages"] = stages
    except (AOFBridgeError, ValueError) as e:
        report["error"] = {"type": e.__class__.__name__, "message": str(e)}
        print(f"[error] {e}")
        code = 2
    except Exception as e:  # pragma: no cover
        report["error"] = {"type": "UnexpectedError", "message": repr(e)}
        print(f"[unexpected error] {e}")
        code = 99
    finally:
        save_result_log(result_path, report)
        print(f"[result] {result_path}")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
