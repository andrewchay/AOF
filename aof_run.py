#!/usr/bin/env python3
"""AOF minimal runner.

Default behavior is dry-run style unless stages are explicitly enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge.cognee_runner import run_cognify_from_spec
from bridge.errors import AOFBridgeError
from bridge.preflight import ensure_preflight_for_cognify
from bridge.quality_gate import quality_gate_commands


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


def run_quality(project_root: str, dry_run: bool) -> list[str]:
    cmds = quality_gate_commands(project_root)
    for cmd in cmds:
        print(f"[quality] {cmd}")
        if not dry_run:
            subprocess.run(cmd, shell=True, check=True)
    return cmds


async def run_cognify(spec: dict, dry_run: bool, skip_preflight: bool):
    print("[cognify] invoke cognee.cognify(...) from mapped spec")
    if dry_run:
        return {"dry_run": True}

    if not skip_preflight:
        ensure_preflight_for_cognify(spec, require_api_key=True)

    result = await run_cognify_from_spec(spec)
    print("[cognify] result:", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="AOF minimal runner")
    parser.add_argument(
        "--spec",
        default="aof_spec.example.json",
        help="Path to AOF JSON spec file",
    )
    parser.add_argument(
        "--run-quality",
        action="store_true",
        help="Run copied quality-gate lint scripts",
    )
    parser.add_argument(
        "--run-cognify",
        action="store_true",
        help="Run cognee.cognify with mapped spec",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions only, do not execute",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip preflight checks before real cognify execution",
    )
    parser.add_argument(
        "--result-file",
        default="logs/aof_run_result.json",
        help="Write run result as JSON to this path",
    )

    args = parser.parse_args()
    spec_path = Path(args.spec).resolve()
    spec = load_spec(spec_path)

    project_root = spec.get("project_root") or str(Path(__file__).resolve().parent)
    result_path = Path(args.result_file)
    if not result_path.is_absolute():
        result_path = Path(project_root) / result_path

    if not args.run_quality and not args.run_cognify:
        print("No stage selected. Use --run-quality and/or --run-cognify.")
        return 0

    report: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "spec": str(spec_path),
        "project_root": project_root,
        "dry_run": args.dry_run,
        "stages": {},
    }

    code = 0
    try:
        if args.run_quality:
            cmds = run_quality(project_root=project_root, dry_run=args.dry_run)
            report["stages"]["quality"] = {
                "ok": True,
                "commands": cmds,
            }

        if args.run_cognify:
            cognify_result = asyncio.run(
                run_cognify(spec=spec, dry_run=args.dry_run, skip_preflight=args.skip_preflight)
            )
            report["stages"]["cognify"] = {
                "ok": True,
                "result": cognify_result,
            }
    except AOFBridgeError as e:
        print(f"[AOF bridge error] {e}")
        report["error"] = {"type": "AOFBridgeError", "message": str(e)}
        code = 2
    except subprocess.CalledProcessError as e:
        print(f"[subprocess error] {e}")
        report["error"] = {"type": "SubprocessError", "message": str(e)}
        code = 3
    except Exception as e:  # pragma: no cover
        print(f"[unexpected error] {e}")
        report["error"] = {"type": "UnexpectedError", "message": repr(e)}
        code = 99
    finally:
        save_result_log(result_path, report)
        print(f"[result] {result_path}")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
