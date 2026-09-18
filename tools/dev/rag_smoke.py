#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""RAG 检索冒烟脚本：通过 TestClient 直接驱动 API，验证多路召回与参数校验。

用法（在仓库根目录执行）：

    python tools/dev/rag_smoke.py --dataset genshin_ultimate_kg

退出码始终为 0：本脚本用于人工观察检索行为，不作为 CI 门禁。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERVICE_DIR = ROOT / "services" / "semantic_middle_layer_api"


def show(label: str, data: dict) -> None:
    print(f"\n===== {label} =====")
    print("count:", data.get("count"))
    print("route_counts:", data.get("route_counts"))
    print("execution_time_ms:", data.get("execution_time_ms"))
    print("error:", data.get("error"))
    for i, r in enumerate(data.get("results", [])):
        print(f'  [{i}] score={r.get("score")} type={r.get("type")} src={r.get("source")}')
        print(f'       text: {(r.get("text") or "")[:60]}')
        prov = r.get("provenance") or {}
        print(f"       provenance: {json.dumps(prov, ensure_ascii=False)[:180]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="genshin_ultimate_kg", help="检索目标数据集名")
    parser.add_argument(
        "--spec",
        default=str(ROOT / "aof_spec.example.json"),
        help="AOF_SPEC_PATH 配置文件",
    )
    parser.add_argument(
        "--service-dir",
        default=str(DEFAULT_SERVICE_DIR),
        help="semantic_middle_layer_api 服务代码目录",
    )
    args = parser.parse_args()

    import os

    os.environ["AOF_SERVE_WEB"] = "0"
    os.environ["AOF_SPEC_PATH"] = args.spec
    sys.path.insert(0, args.service_dir)
    sys.path.insert(0, str(ROOT))

    from fastapi.testclient import TestClient

    import app as app_mod

    cases = [
        ("用例1: 魔女会", {"query": "魔女会", "limit": 5}),
        ("用例2: 八重神子 御影炉心", {"query": "八重神子 御影炉心", "limit": 5}),
        ("用例3: 莱茵多特(无图谱路)", {"query": "莱茵多特", "limit": 3, "include_graph": False}),
    ]

    t0 = time.time()
    with TestClient(app_mod.app) as client:
        for label, payload in cases:
            payload["dataset_name"] = args.dataset
            response = client.post("/v1/rag/retrieve", json=payload)
            show(label, response.json())

        # 参数校验：空 query 应 422
        response = client.post(
            "/v1/rag/retrieve", json={"query": "", "dataset_name": args.dataset}
        )
        print("\n===== 用例4: 空 query 参数校验 =====")
        print("status:", response.status_code, "(期望 422)")

    print(f"\n总耗时: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
