# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with the
# Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""AOF 语义资产结构化查询层（数据资产路径）。

面向 hk4e 权威直连资产（资源索引 + 资源卡 JSON）的只读查询：检索、取卡、问题路由。
与 release-pinned 治理栈（mcp_server.py 的 ``aof_semantic_query``，查询编译器 release 产物）
并行不悖：本模块不经过身份/发布治理，面向 playbook 分析流的接地计划（grounding plan）。

三层路由模型（步1-步3 建成）：
- ``Metric``  口径卡：定义全文 + 红线 + 指标族 + 表互链（284 张）
- ``Playbook`` 场景卡：触发问题 + 表清单 + 能力状态 + 合同文件路径（21 张）
- ``ObjectType`` 表卡：中文名/逻辑名/来源表/分区/FK/防呆注记（2,388 张）
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.build_resource_index import search as _index_search  # noqa: E402

DEFAULT_INDEX_DB = REPO_ROOT / "data/aof_resource_index/resource_index.sqlite3"
DEFAULT_RESOURCES_JSON = REPO_ROOT / "data" / "semantic_assets" / "resources_full.json"
KUZU_DIR = REPO_ROOT / "data/hk4e_ontology"

GUIDANCE_SCENE = (
    "命中场景卡：进 spec.repo_path 把合同文件一次读全（spec.refs 指向 口径/表/gotchas/queries/输出/rules），"
    "交付格式跟 reference/输出.md；spec.capabilities 里 blocked 的能力如实声明边界。"
)
GUIDANCE_METRIC = (
    "命中口径卡：定义+红线直接可答；口径细节以卡片 refs.口径 指向的原文为准；"
    "红线（禁止/不得/必须句）与表.md 负引用（已下线表）必须遵守。"
)
GUIDANCE_TABLE = (
    "命中表卡：照常做最小分区 preflight（aof-semantic 不提供数据行，取数走 andrew-mcp query_data）；"
    "注意卡片 notes 防呆注记与 family: 版本快照族标签。"
)
GUIDANCE_MISS = (
    "0 命中：换同义词/英文逻辑名/拆词再试（aof_search）；仍无命中则如实告知，"
    "并把缺口追加到 workspace .context/hk4e_meta/ontology_gaps.md。"
)

# 反向词表匹配时排除的泛化词（出现在问题里不代表指向某张卡）
_GENERIC_TERMS = {
    "口径", "约定", "总则", "场景卡", "指标", "表", "定义", "红线", "怎么算", "是什么",
    "哪些", "多少", "为什么", "如何", "分析", "查询", "场景", "用户", "人群", "窗口",
    "行为", "特征", "标签", "清单", "基线", "分母", "样本", "总体", "结果", "输入",
}

_PAREN_ALIAS_RE = re.compile(r"[（(]([^（）()]{2,24})[)）]")


class SemanticQuery:
    """资源索引 + 资源卡的结构化只读查询（零 LLM，离线可用）。"""

    def __init__(
        self,
        index_db: str | Path = DEFAULT_INDEX_DB,
        resources_json: str | Path = DEFAULT_RESOURCES_JSON,
    ) -> None:
        self.index_db = str(index_db)
        self.resources_json = Path(resources_json)
        self._by_id: dict[str, dict[str, Any]] | None = None
        self._vocab_cache: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    # ------------------------------------------------------------------ 检索
    def search(self, query: str, kind: str | None = None, k: int = 8) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("query must not be empty")
        results, elapsed_ms, partial = _index_search(self.index_db, query, k, kind)
        return {
            "query": query,
            "kind": kind,
            "elapsed_ms": round(elapsed_ms, 1),
            "partial_or_fallback": partial,
            "results": results,
        }

    # ------------------------------------------------------------------ 取卡
    def _cards(self) -> dict[str, dict[str, Any]]:
        if self._by_id is None:
            data = json.loads(self.resources_json.read_text(encoding="utf-8"))
            self._by_id = {r["resource_id"]: r for r in data["resources"]}
        return self._by_id

    def get(self, resource_id: str) -> dict[str, Any]:
        card = self._cards().get(resource_id)
        if card is None:
            raise KeyError(resource_id)
        return card

    # ------------------------------------------------------- 反向词表匹配
    def _vocab_items(self, kind: str) -> list[tuple[str, dict[str, Any]]]:
        """某 kind 全部卡的检索词表：(term, card)。泛化词与超长名剔除。"""
        if kind in self._vocab_cache:
            return self._vocab_cache[kind]
        items: list[tuple[str, dict[str, Any]]] = []
        for card in self._cards().values():
            if card.get("kind") != kind:
                continue
            spec = card.get("spec") or {}
            candidates = {card.get("display_name"), card.get("name")}
            if kind == "Metric":
                candidates.add(spec.get("metric"))
            elif kind == "Playbook":
                candidates.update({spec.get("title"), spec.get("scene_slug")})
            elif kind == "ObjectType":
                candidates.update({spec.get("entity"), spec.get("cn")})
                for t in spec.get("source_tables") or []:
                    candidates.update({t, t.split(".", 1)[1] if "." in t else None})
            # 括号别名（如「人流失 vs 账号流失（伪流失）」→「伪流失」）
            for src in list(candidates):
                if src:
                    for m in _PAREN_ALIAS_RE.finditer(str(src)):
                        candidates.add(m.group(1).strip())
            for c in candidates:
                if c and len(c) >= 2 and c not in _GENERIC_TERMS and not str(c).startswith("aof:"):
                    items.append((str(c), card))
        self._vocab_cache[kind] = items
        return items

    def _vocab_match(self, question: str, kind: str, k: int) -> list[dict[str, Any]]:
        """词表路由：正向（词出现在问题里，强匹配）+ 反向（问题的 3 字 CJK 片段
        出现在长词里，弱匹配，如问句「学生用户…」→ 场景标题「学生用户生活状态…」）。"""
        windows = {
            question[i : i + 3]
            for i in range(len(question) - 2)
            if re.fullmatch(r"[\u4e00-\u9fff]{3}", question[i : i + 3])
        }
        best: dict[str, tuple[float, int, str, dict[str, Any]]] = {}  # rid → (score, term_len, term, card)

        def consider(card: dict[str, Any], term: str, score: float) -> None:
            rid = card["resource_id"]
            cur = best.get(rid)
            if cur is None or (score, len(term)) > (cur[0], cur[1]):
                best[rid] = (score, len(term), term, card)

        for term, card in self._vocab_items(kind):
            if term in question:
                consider(card, term, 1.0)
            elif len(term) >= 4 and any(w in term for w in windows):
                consider(card, term, 0.6)

        ranked = sorted(best.values(), key=lambda t: (t[0], t[1]), reverse=True)[:k]
        return [
            {
                "resource_id": card["resource_id"],
                "kind": kind,
                "name": card.get("name"),
                "display_name": card.get("display_name"),
                "description": card.get("description"),
                "matched_term": term,
                "score": score,
            }
            for score, _l, term, card in ranked
        ]

    # ------------------------------------------------------------------ 路由
    def route(
        self, question: str, k_metric: int = 3, k_scene: int = 2, k_table: int = 5
    ) -> dict[str, Any]:
        if not question.strip():
            raise ValueError("question must not be empty")
        metrics = self._vocab_match(question, "Metric", k_metric)
        scenes = self._vocab_match(question, "Playbook", k_scene)
        tables = self._vocab_match(question, "ObjectType", k_table)

        # 口径命中推导其所属场景（metric.spec.scene → playbook 卡）
        seen_scene_ids = {s["resource_id"] for s in scenes}
        for m in metrics:
            card = self._cards().get(m["resource_id"], {})
            scene_slug = (card.get("spec") or {}).get("scene")
            rid = f"aof://mihoyo/hk4e/playbook/{scene_slug}" if scene_slug else None
            if rid and rid not in seen_scene_ids and rid in self._cards():
                scene_card = self._cards()[rid]
                scenes.append({
                    "resource_id": rid, "kind": "Playbook", "name": scene_card.get("name"),
                    "display_name": scene_card.get("display_name"),
                    "description": scene_card.get("description"),
                    "matched_via": m["resource_id"], "score": 0.8,
                })
                seen_scene_ids.add(rid)
        scenes = scenes[:k_scene]

        fallback_used = False
        if not (metrics or scenes or tables):
            # 词表未命中 → 原始搜索兜底（全 kind），按 kind 归桶
            raw, _, partial = _index_search(self.index_db, question, 10, None)
            if raw:
                fallback_used = True
                buckets: dict[str, list[dict[str, Any]]] = {"Metric": [], "Playbook": [], "ObjectType": []}
                for r in raw:
                    if r["kind"] in buckets and len(buckets[r["kind"]]) < 3:
                        buckets[r["kind"]].append(r)
                metrics, scenes, tables = buckets["Metric"], buckets["Playbook"], buckets["ObjectType"]

        guidance: list[str] = []
        if scenes:
            guidance.append(GUIDANCE_SCENE)
        if metrics:
            guidance.append(GUIDANCE_METRIC)
        if tables:
            guidance.append(GUIDANCE_TABLE)
        if not guidance:
            guidance.append(GUIDANCE_MISS)
        return {
            "question": question,
            "route_mode": "fallback_search" if fallback_used else "vocab_match",
            "metrics": metrics,
            "scenes": scenes,
            "tables": tables,
            "guidance": guidance,
        }

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "index_db": self.index_db,
            "index_exists": Path(self.index_db).exists(),
            "resources_json": str(self.resources_json),
            "resources_json_exists": self.resources_json.exists(),
            "kuzu_dir": str(KUZU_DIR),
            "kuzu_exists": KUZU_DIR.exists(),
        }
        if out["index_exists"]:
            with sqlite3.connect(self.index_db) as conn:
                kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM resources GROUP BY kind"))
            out["index_kind_counts"] = kinds
            out["index_total"] = sum(kinds.values())
        if out["resources_json_exists"]:
            out["resources_json_total"] = len(self._cards())
        return out

    def families(self) -> dict[str, int]:
        counts = Counter(
            str(card.get("spec", {}).get("family", "未分类"))
            for card in self._cards().values()
            if card.get("kind") == "Metric"
        )
        return dict(counts)
