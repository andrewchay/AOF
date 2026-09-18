#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""Generate/check the repository capability inventory and README summary."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ROOT / "config" / "capabilities" / "operations.json"
MANIFEST = ROOT / "config" / "capabilities" / "capability-manifest.json"
README = ROOT / "README.md"
UI_ROUTER = ROOT / "web" / "src" / "router" / "index.ts"
INTEGRATION_PROFILES = ROOT / "config" / "capabilities" / "integration-profiles.json"
START = "<!-- capability-inventory:start -->"
END = "<!-- capability-inventory:end -->"


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _dependency_profiles() -> list[dict[str, Any]]:
    paths = {
        *ROOT.glob("requirements*.txt"),
        *(ROOT / "requirements").glob("*.in"),
        *(ROOT / "services").glob("**/requirements*.txt"),
        ROOT / "web" / "package.json",
        ROOT / "web" / "pnpm-lock.yaml",
    }
    profiles = []
    for path in sorted(item for item in paths if item.is_file()):
        relative = path.relative_to(ROOT).as_posix()
        if path.name == "package.json":
            package = json.loads(path.read_text(encoding="utf-8"))
            declared = sorted({
                *package.get("dependencies", {}),
                *package.get("devDependencies", {}),
            })
            ecosystem = "node"
        elif path.name.endswith("lock.yaml"):
            lock = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            declared = sorted((lock.get("importers", {}).get(".", {}).get("dependencies", {}) or {}))
            ecosystem = "node-lock"
        else:
            declared = sorted(
                line.split(";", 1)[0].split("==", 1)[0].split(">=", 1)[0].strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
            ecosystem = "python"
        profiles.append({
            "path": relative,
            "ecosystem": ecosystem,
            "source_digest": _digest(path),
            "declared_dependencies": declared,
            "dependency_count": len(declared),
        })
    return profiles


def _deployment_profiles() -> list[dict[str, Any]]:
    compose_paths = {
        *(ROOT / "deploy").glob("docker-compose*.yml"),
        *(ROOT / "services").glob("**/docker-compose*.yml"),
    }
    profiles = []
    for path in sorted(compose_paths):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        profiles.append({
            "profile_id": path.relative_to(ROOT).as_posix(),
            "kind": "docker-compose",
            "source": path.relative_to(ROOT).as_posix(),
            "source_digest": _digest(path),
            "services": sorted((document.get("services") or {}).keys()),
        })
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    workflow_document = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
    profiles.append({
        "profile_id": "ci-required-jobs",
        "kind": "ci-workflow",
        "source": workflow.relative_to(ROOT).as_posix(),
        "source_digest": _digest(workflow),
        "jobs": sorted((workflow_document.get("jobs") or {}).keys()),
    })
    dockerfile = ROOT / "services" / "semantic_middle_layer_api" / "Dockerfile"
    profiles.append({
        "profile_id": "production-image",
        "kind": "container-image",
        "source": dockerfile.relative_to(ROOT).as_posix(),
        "source_digest": _digest(dockerfile),
    })
    return profiles


def _domain(operation: dict[str, Any]) -> str:
    if operation["method"] == "TOOL":
        return "mcp-tools"
    if operation["classification"] == "static-hosting":
        return "web-shell"
    path = operation["path"]
    parts = [part for part in path.split("/") if part]
    return parts[1] if parts and parts[0] == "v1" and len(parts) > 1 else (parts[0] if parts else "root")


def build_inventory() -> dict[str, Any]:
    operations = json.loads(OPERATIONS.read_text(encoding="utf-8"))["operations"]
    previous = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    metadata = {
        item["domain"]: item
        for item in previous.get("capabilities", [])
        if isinstance(item, dict) and "domain" in item
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for operation in operations:
        grouped[_domain(operation)].append(operation)

    capabilities = []
    for domain, domain_operations in sorted(grouped.items()):
        old = metadata.get(domain, {})
        classifications = sorted({item["classification"] for item in domain_operations})
        if classifications == ["governed"]:
            profile = "governed"
        elif classifications == ["legacy"]:
            profile = "legacy"
        elif classifications == ["public-diagnostic"]:
            profile = "diagnostic"
        elif classifications == ["static-hosting"]:
            profile = "static-hosting"
        else:
            profile = "mixed"
        capabilities.append({
            "capability_id": f"cap:{domain}",
            "domain": domain,
            "entry_points": len(domain_operations),
            "classifications": classifications,
            "data_ownership": old.get("data_ownership", "tbd"),
            "owner_role": old.get("owner_role", "工程负责人"),
            "owner_person": old.get("owner_person"),
            "related_findings": old.get("related_findings", "tbd"),
            "profile": profile,
            "migration_path": old.get("migration_path", "classify before production enablement"),
            "test_coverage": old.get("test_coverage", "operation registry validation"),
        })

    rest = [item for item in operations if item["method"] != "TOOL"]
    mcp = [item for item in operations if item["method"] == "TOOL"]
    cli = sorted(path.name for path in ROOT.glob("aof_*.py"))
    route_source = UI_ROUTER.read_text(encoding="utf-8")
    ui_routes = re.findall(r"^\s*path:\s*'([^']*)'", route_source, flags=re.MULTILINE)
    dependencies = _dependency_profiles()
    deployments = _deployment_profiles()
    integration_document = json.loads(INTEGRATION_PROFILES.read_text(encoding="utf-8"))
    integration_profiles = [
        {
            "profile_id": profile_id,
            "capabilities": [
                {
                    "id": item["id"],
                    "required": bool(item.get("required", False)),
                    "enabled_env": item.get("enabled_env"),
                    "default_enabled": bool(item.get("default_enabled", False)),
                    "probe_type": item.get("probe", {}).get("type"),
                    "distribution": item.get("probe", {}).get("distribution"),
                    "version": item.get("probe", {}).get("version"),
                    "reject_editable": bool(item.get("probe", {}).get("reject_editable", False)),
                }
                for item in definition.get("capabilities", [])
            ],
        }
        for profile_id, definition in sorted(integration_document["profiles"].items())
    ]
    return {
        "schema_version": "aof.capability-manifest/v3",
        "generated_from": [
            "config/capabilities/operations.json",
            "aof_*.py",
            "web/src/router/index.ts",
            ".github/workflows/ci.yml",
            "requirements*.txt and requirements/*.in",
            "services/**/requirements*.txt",
            "web/package.json and web/pnpm-lock.yaml",
            "deploy/docker-compose*.yml and services/**/docker-compose*.yml",
            "services/semantic_middle_layer_api/Dockerfile",
            "config/capabilities/integration-profiles.json",
        ],
        "summary": {
            "http_operations": len(rest),
            "http_paths": len({item["path"] for item in rest}),
            "mcp_tools": len(mcp),
            "cli_entry_points": len(cli),
            "ui_routes": len(ui_routes),
            "dependency_manifests": len(dependencies),
            "deployment_profiles": len(deployments),
            "integration_profiles": len(integration_profiles),
        },
        "surfaces": {
            "cli": cli,
            "ui_routes": ui_routes,
            "dependency_profiles": dependencies,
            "deployment_profiles": deployments,
            "integration_profiles": integration_profiles,
        },
        "capabilities": capabilities,
    }


def readme_block(inventory: dict[str, Any]) -> str:
    summary = inventory["summary"]
    return (
        f"{START}\n"
        "当前交付面由能力清单自动生成："
        f"**{summary['http_operations']} 个 HTTP 操作 / "
        f"{summary['http_paths']} 个 HTTP 路径 / "
        f"{summary['mcp_tools']} 个 MCP 工具 / "
        f"{summary['cli_entry_points']} 个 CLI 入口 / "
        f"{summary['ui_routes']} 个 UI 路由**。"
        "详见 [`config/capabilities/capability-manifest.json`](config/capabilities/capability-manifest.json)。\n"
        f"{END}"
    )


def update_readme(content: str, block: str, inventory: dict[str, Any]) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if pattern.search(content):
        content = pattern.sub(block, content)
    else:
        marker = "## 历史功能与兼容能力"
        content = content.replace(marker, block + "\n\n" + marker, 1)
    content = re.sub(r"API 层 \(\d+ 端点\)", "API 层（见自动清单）", content)
    content = re.sub(
        r"\*\*当前测试状态\*\*: \d+ tests ✅ 全部通过",
        "**当前测试状态**：以 CI 全量与企业零跳过门禁为准",
        content,
    )
    content = re.sub(
        r"\| API (?:端点|操作) \| [^|]+ \|",
        f"| API 操作 | {inventory['summary']['http_operations']}（自动生成） |",
        content,
    )
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    inventory = build_inventory()
    manifest_text = json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    readme_text = update_readme(
        README.read_text(encoding="utf-8"), readme_block(inventory), inventory
    )
    if args.check:
        stale = []
        if MANIFEST.read_text(encoding="utf-8") != manifest_text:
            stale.append(str(MANIFEST.relative_to(ROOT)))
        if README.read_text(encoding="utf-8") != readme_text:
            stale.append(str(README.relative_to(ROOT)))
        if stale:
            raise SystemExit(f"capability inventory is stale: {stale}")
        print("Capability inventory and README summary are current")
        return 0
    MANIFEST.write_text(manifest_text, encoding="utf-8")
    README.write_text(readme_text, encoding="utf-8")
    print(json.dumps(inventory["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
