#!/usr/bin/env python3
"""步2：playbook 20 场景 → kind=Playbook SemanticResource 注册进 resources_full.json。

确定性解析 playbook.md（frontmatter/场景/能力状态）+ reference/表.md + 口径.md，0 LLM。
幂等：resource_id 已存在则跳过。场景卡只进资源索引，不入 TTL/Kuzu（流程知识≠游戏域本体）。
"""
import json
import re
import time
from pathlib import Path

PLAYBOOKS = Path("/Users/chaihao/LLM/brando-playbook-game-growth/playbooks")
RES = Path(__file__).resolve().parents[2] / "data" / "semantic_assets" / "resources_full.json"
ONTOLOGY_URI = "aof://mihoyo/hk4e/ontology/hk4e-data-ontology"


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    fm: dict = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip("[]").replace(" ", "")
    return fm


def section(text: str, name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


TBL_RE = re.compile(r"`([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)`")


def parse_tables(ref_dir: Path) -> list[dict]:
    """兼容两种表格布局：A `|别名|表|粒度|用途|状态|` 与 B `|表|粒度|用途|约束|`。
    逐单元格扫描反引号 db.table，位置派生 alias/粒度/用途/约束。"""
    p = ref_dir / "表.md"
    if not p.exists():
        return []
    tables, seen = [], set()
    for line in p.read_text().splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        idx = next((i for i, c in enumerate(cells) if TBL_RE.search(c)), None)
        if idx is None:
            continue
        m_full = TBL_RE.search(cells[idx])
        assert m_full is not None
        full = m_full.group(1)
        if full in seen:
            continue
        seen.add(full)
        tables.append({
            "alias": cells[0].strip("` ") if idx > 0 else "",
            "full_name": full,
            "granularity": cells[idx + 1] if len(cells) > idx + 1 else "",
            "purpose": cells[idx + 2] if len(cells) > idx + 2 else "",
            "status": cells[idx + 3] if len(cells) > idx + 3 else "",
        })
    return tables


def parse_metrics(ref_dir: Path) -> list[str]:
    p = ref_dir / "口径.md"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        m = re.match(r"^#{2,4}\s+(.+?)\s*$", line)
        if m and m.group(1) != "口径":
            out.append(m.group(1))
    return out[:40]


def parse_capabilities(text: str) -> dict:
    caps = {}
    for line in section(text, "能力状态").splitlines():
        m = re.match(r"^[-*]\s+(.+?)[:：]\s*(ready|blocked|conditional|partial)", line)
        if m:
            caps[m.group(1).strip()] = m.group(2)
    return caps


def trigger_questions(scene_text: str) -> list[str]:
    qs, seen, out = re.findall(r"[「“]([^」”]{4,120})[」”]", scene_text), set(), []
    for q in qs:
        q = q.strip()
        if len(q) >= 8 and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:6]


def contract_refs(repo: Path) -> dict:
    refs = {}
    for key, fn in [("口径", "口径.md"), ("表", "表.md"), ("gotchas", "gotchas.md"),
                    ("queries", "queries.sql"), ("输出", "输出.md"), ("rules", "rules.md")]:
        p = repo / "reference" / fn
        if p.exists():
            refs[key] = str(p)
    return refs


def main() -> int:
    ts = time.strftime("%Y%m%d-%H%M%S")
    data = json.loads(RES.read_text())
    resources = data["resources"]
    # 全量刷新：旧场景卡全部移除后按 repo 现状重建（与 playbook 仓保持同步）
    resources[:] = [r for r in resources if r.get("kind") != "Playbook"]
    existing_ids = {r["resource_id"] for r in resources}

    # 表名 → ObjectType 资源 URI 映射（场景卡 ↔ 表卡互链）
    tbl2uri: dict[str, str] = {}
    for r in resources:
        if r.get("kind") == "ObjectType":
            for t in (r.get("spec") or {}).get("source_tables") or []:
                tbl2uri[t] = r["resource_id"]

    scenes, added, skipped = [], 0, 0
    for repo in sorted(p for p in PLAYBOOKS.iterdir() if p.is_dir()):
        pb = repo / "playbook.md"
        if not pb.exists():
            continue
        slug = repo.name
        rid = f"aof://mihoyo/hk4e/playbook/{slug}"
        text = pb.read_text()
        fm = parse_frontmatter(text)
        title = fm.get("title", slug)
        desc = fm.get("description", "")
        scene_para = re.sub(r"\s+", " ", section(text, "场景"))
        tq = trigger_questions(section(text, "场景"))
        caps = parse_capabilities(text)
        tables = parse_tables(repo / "reference")
        metrics = parse_metrics(repo / "reference")
        refs = contract_refs(repo)
        resolved = sorted({tbl2uri[t["full_name"]] for t in tables if t["full_name"] in tbl2uri})

        status = fm.get("status", "active")
        n_ready = sum(1 for v in caps.values() if v == "ready")
        card = {
            "resource_id": rid,
            "kind": "Playbook",
            "name": slug,
            "domain": "hk4e",
            "owner": "hao.chai",
            "display_name": f"{title}（场景卡）",
            "description": (
                f"{desc}｜场景问题: {(tq[0] if tq else scene_para)[:200]}"
                f"｜{len(tables)} 张表 / {len(metrics)} 个口径主题｜能力 {n_ready}/{len(caps)} ready"
                f"｜repo: {repo}"
            ),
            "tags": ["playbook", "scene", status, *([d for d in fm.get("domain", "").split(",") if d])],
            "depends_on": [ONTOLOGY_URI],
            "evidence": [{"source_uri": str(pb), "note": f"playbook frontmatter updated={fm.get('updated', '')}"}],
            "spec": {
                "title": title, "scene_slug": slug, "status": status, "repo_path": str(repo),
                "trigger_questions": tq, "tables": tables, "metrics": metrics,
                "capabilities": caps, "refs": refs, "resolved_tables": resolved,
            },
            "revision_id": f"sha256:pb3-{ts}",
            "schema_version": "aof.semantic/v1",
        }
        resources.append(card)
        existing_ids.add(rid)
        scenes.append({"slug": slug, "title": title})
        added += 1

    # 总览卡（幂等更新：删旧再建）
    idx_id = "aof://mihoyo/hk4e/playbook/index"
    resources[:] = [r for r in resources if r["resource_id"] != idx_id]
    resources.append({
        "resource_id": idx_id,
        "kind": "Playbook",
        "name": "playbook-index",
        "domain": "hk4e",
        "owner": "hao.chai",
        "display_name": "Playbook 场景总览（game-growth）",
        "description": "brando game-growth playbook 全部 " + str(len(scenes))
                       + " 个场景索引：" + "；".join(f"{s['slug']}={s['title']}" for s in scenes)
                       + f"｜repo: {PLAYBOOKS}",
        "tags": ["playbook", "index", "scene"],
        "depends_on": [ONTOLOGY_URI],
        "evidence": [{"source_uri": str(PLAYBOOKS / "index.md")}],
        "spec": {"title": "Playbook 场景总览", "scenes": scenes, "repo_path": str(PLAYBOOKS)},
        "revision_id": f"sha256:pb3-{ts}",
        "schema_version": "aof.semantic/v1",
    })

    RES.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    n_res = sum(len((r.get("spec") or {}).get("tables") or []) for r in resources
                if r.get("kind") == "Playbook" and r["resource_id"] != idx_id)
    n_ok = sum(len((r.get("spec") or {}).get("resolved_tables") or []) for r in resources
               if r.get("kind") == "Playbook" and r["resource_id"] != idx_id)
    print(f"新建场景卡 {added}，跳过(已存在) {skipped}，总览卡 1")
    print(f"场景卡引用物理表合计 {n_res} 张次，其中解析到表卡 URI {n_ok} 张次")
    print(f"resources 总数: {len(resources)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
