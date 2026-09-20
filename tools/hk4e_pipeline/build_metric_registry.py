#!/usr/bin/env python3
"""步3：playbook 口径.md → kind=Metric 注册表。

确定性解析 20 个口径.md：具名条目（bold/plain `名称：定义`）→ 每条一张 Metric 卡；
无名约束句按 ## 主题归并为约定卡；红线句自动抽取；表引用解析到 ObjectType URI。
全量刷新语义（repo 是唯一事实源）。只进资源索引，不入 TTL/Kuzu。
"""
import hashlib
import json
import re
import time
from pathlib import Path

PLAYBOOKS = Path("/Users/chaihao/LLM/brando-playbook-game-growth/playbooks")
RES = Path(__file__).resolve().parents[2] / "data" / "semantic_assets" / "resources_full.json"
ONTOLOGY_URI = "aof://mihoyo/hk4e/ontology/hk4e-data-ontology"
TBL_RE = re.compile(r"`([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)`")
BARE_TBL_RE = re.compile(r"\b((?:dws|dwb|dwd|ads|dim|biz)_[a-z0-9_]+\.[a-z][a-z0-9_]+)\b")
NAMED_BOLD = re.compile(r"^-\s+\*\*(.+?)\*\*[:：]\s*(.*)$")
NAMED_PLAIN = re.compile(r"^-\s+([^*`。；，、=“”\"']{2,24}?)[:：]\s*(.*)$")
REDLINE_RE = re.compile(r"(禁止|不得|必须|不能|禁用|禁称|只称|只能称|只能走|不算|不授权|不可|须声明|须报告|须过|须用|只能给|先定义|先声明|⚠️|不等于因果|非因果)")

# 场景 → 指标族
FAMILY = {
    "churn-prediction": "活跃与流转族", "return-retention": "活跃与流转族",
    "version-activity-migration": "活跃与流转族", "dau-trend-attribution": "活跃与流转族",
    "cross-game-flow": "活跃与流转族",
    "new-user-profile": "人群画像族", "student-users": "人群画像族",
    "gacha-motivation": "动机与分层族", "user-motivation-tags": "动机与分层族",
    "player-habit-perception": "动机与分层族", "version-rhythm-segmentation": "动机与分层族",
    "map-penetration": "玩法与内容族", "story-progress": "玩法与内容族",
    "character-poi": "玩法与内容族", "content-rhythm": "玩法与内容族",
    "official-content-impact": "内容触达族", "intent-perception-gap": "内容触达族",
    "business-context": "业务先验族", "exploratory-analysis": "方法族",
    "opportunity-sizing": "机会量化族",
}
KEY_RULES = [
    (("dau", "活跃", "活跃天"), "activity"), (("流失", "churn"), "churn"),
    (("回流", "reflux"), "reflux"), (("留存", "retention"), "retention"),
    (("新增", "注册", "进水"), "newuser"), (("付费", "流水", "arpu", "vip_point", "hcoin", "recharge"), "monetization"),
    (("抽卡", "gacha", "祈愿"), "gacha"), (("渗透",), "penetration"), (("版本",), "version"),
    (("参与率", "参率", "时长", "使用率"), "participation"), (("触达", "曝光", "观看"), "reach"),
    (("因果", "净效应", "归因", "准随机", "反事实"), "causal"), (("tgi",), "tgi"),
    (("person_id", "分析单元", "class3", "人级", "账号级"), "unit"), (("风险分层", "分层"), "segment"),
]


def slugify(name: str) -> str:
    s = re.sub(r"[\s（）()：:、，,／/]+", "-", name.strip()).strip("-")
    return s[:60] or hashlib.md5(name.encode()).hexdigest()[:8]


def extract_red_lines(text: str) -> list[str]:
    out, seen = [], set()
    for sent in re.split(r"[。；]", text):
        sent = sent.strip()
        if sent and REDLINE_RE.search(sent) and len(sent) >= 6 and sent not in seen:
            seen.add(sent)
            out.append(sent[:120])
    return out[:8]


def metric_keys(name: str, text: str) -> list[str]:
    hay = (name + " " + text).lower()
    return [key for words, key in KEY_RULES if any(w in hay for w in words)][:6]


BT_RE = re.compile(r"`([a-z][a-z0-9_]{3,70})`")


def resolve_tables(text: str, tbl2uri: dict, bare2uri: dict) -> list[str]:
    """口径文本里的表引用三种形态：`db.table`、裸 db.table、`裸逻辑名`。"""
    uris = set()
    for m in TBL_RE.finditer(text):
        if m.group(1) in tbl2uri:
            uris.add(tbl2uri[m.group(1)])
    for m in BARE_TBL_RE.finditer(text):
        full = m.group(1)
        if full in tbl2uri:
            uris.add(tbl2uri[full])
        elif full.split(".", 1)[1] in bare2uri:
            uris.add(bare2uri[full.split(".", 1)[1]])
    for m in BT_RE.finditer(text):
        tok = m.group(1)
        if tok in bare2uri:
            uris.add(bare2uri[tok])
    return sorted(uris)[:8]


def parse_koujing(path: Path) -> list[dict]:
    """返回条目列表：{topic, name|None, text}。跨行 bullet 聚合成整段。"""
    items, topic = [], "总则"
    buf_name, buf_text = None, []

    def flush():
        nonlocal buf_name, buf_text
        if buf_name is not None or buf_text:
            items.append({"topic": topic, "name": buf_name, "text": " ".join(buf_text).strip()})
        buf_name, buf_text = None, []

    for line in path.read_text().splitlines():
        s = line.rstrip()
        if s.startswith("## "):
            flush()
            topic = s[3:].strip()
            continue
        if s.startswith("# ") or s.strip().startswith("|") or s.strip().startswith(">"):
            continue
        if re.match(r"^-\s+", s):
            flush()
            body = s[2:].strip()
            m = NAMED_BOLD.match(s) or NAMED_PLAIN.match(s)
            if m and not m.group(1).strip().startswith("`"):
                buf_name, buf_text = m.group(1).strip(), [m.group(2).strip()]
            else:
                buf_name, buf_text = None, [body]
        elif s.strip() and buf_text is not None:
            buf_text.append(s.strip())
    flush()
    return items


def main() -> int:
    ts = time.strftime("%Y%m%d-%H%M%S")
    data = json.loads(RES.read_text())
    resources = data["resources"]
    resources[:] = [r for r in resources if r.get("kind") != "Metric"]  # 全量刷新

    tbl2uri, bare2uri = {}, {}
    for r in resources:
        if r.get("kind") == "ObjectType":
            for t in (r.get("spec") or {}).get("source_tables") or []:
                tbl2uri[t] = r["resource_id"]
                bare2uri[t.split(".", 1)[1]] = r["resource_id"]

    scene_titles = {r["name"]: (r.get("spec") or {}).get("title", r["name"])
                    for r in resources if r.get("kind") == "Playbook"}

    added = dup_skipped = 0
    seen_rids: set[str] = set()
    for scene_dir in sorted(p for p in PLAYBOOKS.iterdir() if p.is_dir()):
        kj = scene_dir / "reference" / "口径.md"
        if not kj.exists():
            continue
        scene = scene_dir.name
        scene_title = scene_titles.get(scene, scene)
        base_refs = {"口径": str(kj)}
        for fn, key in [("rules.md", "rules"), ("queries.sql", "queries"), ("gotchas.md", "gotchas")]:
            if (scene_dir / "reference" / fn).exists():
                base_refs[key] = str(scene_dir / "reference" / fn)

        conv: dict[str, list[str]] = {}  # topic → 约束句
        for item in parse_koujing(kj):
            topic, name, text = item["topic"], item["name"], item["text"]
            if not text:
                continue
            if name is None:
                conv.setdefault(topic, []).append(text[:200])
                continue
            uris = resolve_tables(text, tbl2uri, bare2uri)
            tslug = slugify(topic) if topic != "总则" else "zongze"
            rid = f"aof://mihoyo/hk4e/metric/{scene}/{tslug}/{slugify(name)}"
            if rid in seen_rids:
                dup_skipped += 1
                continue
            seen_rids.add(rid)
            resources.append({
                "resource_id": rid, "kind": "Metric", "name": f"{scene}__{slugify(name)}",
                "domain": "hk4e", "owner": "hao.chai",
                "display_name": name,
                "description": f"{scene_title}·{topic}｜{text[:420]}",
                "tags": ["metric", FAMILY.get(scene, "未分类"), scene],
                "depends_on": [ONTOLOGY_URI, f"aof://mihoyo/hk4e/playbook/{scene}"],
                "evidence": [{"source_uri": str(kj), "note": f"{scene_title} / {topic}"}],
                "spec": {
                    "metric": name, "scene": scene, "scene_title": scene_title, "topic": topic,
                    "text": text[:1500], "family": FAMILY.get(scene, "未分类"),
                    "metric_keys": metric_keys(name, text),
                    "red_lines": extract_red_lines(text),
                    "resolved_tables": uris, "refs": base_refs,
                },
                "revision_id": f"sha256:metric-{ts}", "schema_version": "aof.semantic/v1",
            })
            added += 1
        # 约定卡（每 topic 一张）
        for topic, texts in conv.items():
            if not texts:
                continue
            joined = "；".join(texts)
            tslug = slugify(topic) if topic != "总则" else "zongze"
            rid = f"aof://mihoyo/hk4e/metric/{scene}/{tslug}/conventions"
            if rid in seen_rids:
                dup_skipped += 1
                continue
            seen_rids.add(rid)
            resources.append({
                "resource_id": rid, "kind": "Metric", "name": f"{scene}__conv-{tslug}",
                "domain": "hk4e", "owner": "hao.chai",
                "display_name": f"{topic}·约定（{scene_title}）",
                "description": f"{scene_title}·{topic}｜场景级约定与全局默认：{joined[:400]}",
                "tags": ["metric", "convention", FAMILY.get(scene, "未分类"), scene],
                "depends_on": [ONTOLOGY_URI, f"aof://mihoyo/hk4e/playbook/{scene}"],
                "evidence": [{"source_uri": str(kj), "note": f"{scene_title} / {topic} 约定"}],
                "spec": {
                    "metric": f"{topic}·约定", "scene": scene, "scene_title": scene_title,
                    "topic": topic, "text": joined[:1500], "family": FAMILY.get(scene, "未分类"),
                    "metric_keys": metric_keys(topic, joined), "red_lines": extract_red_lines(joined),
                    "resolved_tables": resolve_tables(joined, tbl2uri, bare2uri)[:8], "refs": base_refs,
                    "card_type": "convention",
                },
                "revision_id": f"sha256:metric-{ts}", "schema_version": "aof.semantic/v1",
            })
            added += 1

    RES.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    n_rl = sum(1 for r in resources if r.get("kind") == "Metric" and (r["spec"].get("red_lines")))
    n_tb = sum(len(r["spec"]["resolved_tables"]) for r in resources if r.get("kind") == "Metric")
    print(f"新建 Metric 卡 {added}（含约定卡）；同 rid 重复条目合并 {dup_skipped}；含红线 {n_rl} 张；表互链 {n_tb} 张次")
    print(f"resources 总数: {len(resources)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
