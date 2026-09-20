#!/usr/bin/env python3
"""playbook 需求驱动的公共层本体增补（步1）。

输入：
  - .context/hk4e_meta/catalog.json                    （6,620 张表完整 schema）
  - brando-playbook-game-growth/playbooks/*/reference/表.md（需求清单 + 场景归属）
  - aof/hk4e-complete.ttl + aof/resources_full.json     （权威资产，增量合并）

产出：24 张缺失表 → pub# 本体段（TTL 增量）+ SemanticResource 增量；先备份原文件。
约定：完全对齐 biz 层序列化（kind/layer/logical_name/tables/notes 注释、<E>_<col>_rel 断言边）。
"""
import json
import re
import shutil
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / "data" / "semantic_assets"
AOF_DIR = BASE
PB = Path("/Users/chaihao/LLM/brando-playbook-game-growth/playbooks")
TTL = AOF_DIR / "hk4e-complete.ttl"
RES = AOF_DIR / "resources_full.json"
PUB = "http://hk4e.mihoyo.com/ontology/pub#"

SQL2XSD = {"int": "integer", "bigint": "long", "smallint": "short", "string": "string",
           "double": "double", "float": "float", "decimal": "decimal", "boolean": "boolean",
           "date": "date", "timestamp": "dateTime"}

# ---- 24 张精修表：full_name → (EN, cn, kind, domain_key, note) ----
CURATED = {
    "biz_hk4e.activity_info_v2": ("ActivityInfoV2", "活动信息V2", "Dimension", "GameEntity", "列注释缺失，结构待精修"),
    "biz_hk4e.aq_quest_list_v2": ("AqQuestListV2", "AQ任务清单V2", "Dimension", "GameEntity", "quest_id 为主键"),
    "biz_hk4e.fay_20250603_aq_quest_req_match": ("AqQuestReqMatchFay20250603", "AQ任务需求匹配(fay-20250603)", "Fact", "Player", "分析师快照表（fay 代号），uid×quest_id"),
    "biz_hk4e.gene_20250603_quest_req": ("QuestReqGene20250603", "任务需求表(gene-20250603)", "Fact", "Player", "分析师快照表（gene 代号），uid×quest_id"),
    "dim_hk4e.dim_hk4e_game_version_gacha_public_df": ("GameVersionGachaPublic", "游戏版本卡池排期", "Dimension", "Time", "版本×卡池排期（公开 DF），PK=version_num+gacha_half"),
    "dwd_hk4e.dwd_inr_pqt_comp_hk4e_hourly_gacha": ("HourlyGacha", "祈愿小时明细", "Fact", "Commerce", "uid×小时事件流"),
    "dwd_hk4e.dwd_inr_pqt_comp_hk4e_hourly_ley_line_challenge": ("HourlyLeyLineChallenge", "地脉挑战小时明细", "Fact", "Combat", "uid×小时事件流"),
    "dwd_hk4e.dwd_inr_pqt_comp_hk4e_hourly_mission_finish": ("HourlyMissionFinish", "任务完成小时明细", "Fact", "GameEntity", "uid×小时事件流"),
    "dwd_hk4e.dwd_inr_pqt_comp_hk4e_hourly_player_add_coin": ("HourlyPlayerAddCoin", "玩家货币获得小时明细", "Fact", "Commerce", "uid×小时事件流"),
    "dwd_hk4e.dwd_inr_pqt_comp_hk4e_hourly_weekly_active": ("HourlyWeeklyActive", "周活跃行为小时明细", "Fact", "Player", "uid×小时事件流（砺行相关，历史上需特定连接器）"),
    "dwd_hk4e.dwd_inr_pqt_comp_hk4e_user_recharge": ("UserRecharge", "用户充值日明细", "Fact", "Commerce", "含 Beyond 混线字段（byd_*），实际充值以 real_recharge_cps 为准"),
    "dws_hk4e.dws_hk4e_all_user_growth_wide_info_td": ("AllUserGrowthWideInfoTd", "全量用户成长宽表(TD快照)", "Fact", "Player", "129 列宽表；单日版本末快照，tag<>3；禁跨快照数活跃天（playbook 约束）"),
    "dws_hk4e.dws_hk4e_avatar_portrait_info_td": ("AvatarPortraitInfoTd", "用户角色画像TD快照", "Fact", "Player", "角色获取/养成/TopN 等级画像"),
    "dws_hk4e.dws_hk4e_daily_all_user_parent_quest": ("DailyAllUserParentQuest", "全量用户父任务日表", "Fact", "GameEntity", "uid×父任务×日，含接受/完成时间"),
    "dws_hk4e.dws_hk4e_quest_map_info_full_1d": ("QuestMapInfoFull", "任务地图映射全表", "Dimension", "GameEntity", "父任务→子任务→地图映射，无 uid"),
    "dws_hk4e.dws_hk4e_reflux_user_tag_1d": ("RefluxUserTag", "回流用户标签日表", "Fact", "Player", "42日/7日回流标签，注册/行为平台"),
    "dws_hk4e.dws_hk4e_task_daily": ("TaskDaily", "用户任务完成日表", "Fact", "Player", "uid×日任务完成数量"),
    "dws_hk4e.dws_hk4e_tower_avatar_list_type_detail_1d": ("TowerAvatarListTypeDetail", "深渊角色名单类型明细日表", "Fact", "Combat", "uid×schedule_id×日"),
    "dws_hk4e.dws_hk4e_user_tower_avatar_info_1d": ("UserTowerAvatarInfo", "用户深渊角色信息日表", "Fact", "Combat", "uid×avatar_id×schedule_id（深渊周期）×日"),
    "dws_hk4e.dws_inr_pqt_comp_hk4e_daily_all_user_avatar": ("DailyAllUserAvatar", "全量用户角色日表", "Fact", "GameEntity", "uid×avatar_id×日"),
    "dws_hk4e.dws_inr_pqt_comp_hk4e_daily_all_user_quest": ("DailyAllUserQuest", "全量用户任务日表", "Fact", "GameEntity", "uid×quest_id×日，含完成进度"),
    "dws_hk4e.dws_inr_pqt_comp_hk4e_daily_all_user_reliquary": ("DailyAllUserReliquary", "全量用户圣遗物日表", "Fact", "GameEntity", "圣遗物持有/锁定/词条状态"),
    "dws_hk4e.dws_inr_pqt_comp_hk4e_daily_tower_settle": ("DailyTowerSettle", "深渊每日结算日表", "Fact", "Combat", "uid×schedule_id×结算×日；DAU 归因场景深渊参与主表"),
    "dws_hk4e.dws_inr_pqt_comp_hk4e_user_daily_info": ("UserDailyInfo", "用户日信息表", "Fact", "Player", "DAU/版本 UV/活跃天核心主表（playbook 11 个场景引用）；必须带日期与区服分区"),
}
WILDCARD = "dwd_hk4e.dwd_inr_pqt_comp_hk4e_hourly_"  # 族级引用，不建实体，记缺口


def ttl_classes(ttl_text: str):
    """label(zh)/label(en) → subject IRI；以及全部 class subject 集合。"""
    zh2iri: dict[str, str] = {}
    iri_set: set[str] = set()
    for m in re.finditer(r"<([^>]+)> a owl:Class ;", ttl_text):
        iri_set.add(m.group(1))
    for m in re.finditer(r"<([^>]+)> a owl:Class ;[^.]*?rdfs:label \"([^\"]+)\"@en,\s*\"([^\"]+)\"@zh", ttl_text, re.S):
        iri, en, zh = m.groups()
        zh2iri.setdefault(zh, iri)
    return zh2iri, iri_set


def main() -> int:
    ts = time.strftime("%Y%m%d-%H%M%S")
    cat = json.loads((BASE / "catalog.json").read_text())
    by_full = {t["full_name"]: t for t in cat["tables"]}
    res_data = json.loads(RES.read_text())
    resources = res_data["resources"]
    ttl_text = TTL.read_text()

    # 备份
    for p in (TTL, RES):
        shutil.copy2(p, p.with_suffix(p.suffix + f".bak-{ts}"))

    zh2iri, existing_classes = ttl_classes(ttl_text)
    existing_prop_locals = set(re.findall(r"/ontology/(?:biz|pub)#([A-Za-z0-9_]+)> a owl:(?:Object|Datatype)Property", ttl_text))
    existing_res_ids = {r["resource_id"] for r in resources}
    existing_obj_names = {r["name"].lower() for r in resources if r["kind"] == "ObjectType"}

    # 场景归属
    scenes: dict[str, set] = {}
    for md in PB.glob("*/reference/表.md"):
        for m in re.finditer(r"(?:dws|dwb|dwd|ads|dim|biz)_[a-z0-9_]+\.[a-z0-9_]+", md.read_text()):
            scenes.setdefault(m.group(0).lower(), set()).add(md.parent.parent.name)

    # 关系目标类
    player_iri = zh2iri.get("玩家域") or "hk4e:Player"  # 域类（biz FK 惯例指向域类）
    player_iri = "hk4e:Player"
    avatar_iri = zh2iri.get("角色")
    version_iri = zh2iri.get("游戏版本") or zh2iri.get("版本")
    print(f"FK 目标: Player=hk4e:Player(固定)  Avatar={avatar_iri}  版本={version_iri}")

    new_ttl: list[str] = ["", "# ==== playbook 公共层增补 (build_playbook_assets.py) ====",
                          f"@prefix pub: <{PUB}> .", ""]
    new_resources: list[dict] = []
    built, skipped = [], []

    def add_resource(d: dict):
        assert d["resource_id"] not in existing_res_ids, f"resource_id 冲突: {d['resource_id']}"
        existing_res_ids.add(d["resource_id"])
        new_resources.append(d)

    for full, (en, cn, kind, dom_key, note) in CURATED.items():
        t = by_full.get(full)
        if not t:
            skipped.append((full, "不在 catalog"))
            continue
        if en.lower() in existing_obj_names:
            skipped.append((full, f"实体名冲突 {en}"))
            continue
        cols = t["columns"]
        scenes_l = sorted(scenes.get(full, set()))
        parts = ",".join(t["partition_columns"]) or "无"
        comment = (f"kind={kind}; layer={t['database'].split('_')[0]}; logical_name={t['table']}; "
                   f"tables=1; partitions={parts}; source={full}; scenes={','.join(scenes_l) or '-'}; notes={note}")
        iri = f"<{PUB}{en}>"
        new_ttl += [
            f"{iri} a owl:Class ;",
            f"    rdfs:label \"{en}\"@en,\n        \"{cn}\"@zh ;",
            f"    rdfs:comment \"{comment}\"@en ;",
            f"    rdfs:subClassOf hk4e:{dom_key} .",
            "",
        ]
        # DatatypeProperty（含类型映射与注释）
        for c in cols:
            pn = f"{en}_{c['name']}"
            if pn in existing_prop_locals:
                continue
            existing_prop_locals.add(pn)
            xsd = SQL2XSD.get(c["type"], "string")
            lbl = c["comment"] or c["name"]
            new_ttl += [
                f"<{PUB}{pn}> a owl:DatatypeProperty ;",
                f"    rdfs:label \"{lbl}\"@zh ;",
                f"    rdfs:domain {iri} ;",
                f"    rdfs:range xsd:{xsd} .",
                "",
            ]
        # 标准关系（via 列必须真实存在）
        col_names = {c["name"] for c in cols}
        fks = [("uid", "Player", player_iri, "玩家")]
        if avatar_iri and "avatar_id" in col_names:
            fks.append(("avatar_id", "Avatar", avatar_iri, "角色"))
        if version_iri and "version_num" in col_names:
            fks.append(("version_num", "GameVersion", version_iri, "游戏版本"))
        rel_resources = []
        for col, tgt_en, tgt_iri, tgt_zh in fks:
            if col not in col_names:
                continue
            prop_local = f"{en}_{col}_rel"
            if prop_local in existing_prop_locals:
                continue
            existing_prop_locals.add(prop_local)
            new_ttl += [
                f"<{PUB}{prop_local}> a owl:ObjectProperty ;",
                f"    rdfs:label \"{en}--{col}-->{tgt_en}\"@en ;",
                f"    rdfs:comment \"card=N:1; confidence=medium; via={col}（公共层标准键，非声明外键）\"@en ;",
                f"    rdfs:domain {iri} ;",
                f"    rdfs:range {tgt_iri} .",
                "",
            ]
            rel_resources.append({
                "resource_id": f"aof://mihoyo/hk4e/relation-type/{en.lower()}-{col}-{tgt_zh}",
                "kind": "RelationType", "name": f"{en.lower()}-{col}-{tgt_zh}",
                "domain": "hk4e", "owner": "hao.chai",
                "display_name": f"{cn} --{col}--> {tgt_zh}",
                "description": f"公共层标准键关联：{cn} 经列 {col} 指向 {tgt_zh}（N:1，置信 medium）",
                "tags": ["relation", "playbook"],
                "depends_on": ["aof://mihoyo/hk4e/ontology/hk4e-data-ontology"],
                "evidence": [], "spec": {"from": en, "to": tgt_en, "type": col, "via": [col], "card": "N:1"},
                "revision_id": "sha256:publayer-" + ts, "schema_version": "aof.semantic/v1",
            })
            add_resource(rel_resources[-1])

        obj = {
            "resource_id": f"aof://mihoyo/hk4e/object-type/{en.lower()}",
            "kind": "ObjectType", "name": en.lower(), "domain": "hk4e", "owner": "hao.chai",
            "display_name": cn,
            "description": f"{cn}（{kind}）｜逻辑名 {t['table']}｜{t['database'].split('_')[0]} 层｜分区 {parts}｜来源: {full}",
            "tags": [kind.lower(), t["database"].split("_")[0], "playbook"],
            "depends_on": ["aof://mihoyo/hk4e/ontology/hk4e-data-ontology"],
            "evidence": [], 
            "spec": {"entity": en, "cn": cn, "kind": kind, "layer": t["database"].split("_")[0],
                     "pk": "uid" if "uid" in col_names else ("version_num" if "version_num" in col_names else ""),
                     "attrs": [c["name"] for c in cols][:60],
                     "attr_count": len(cols), "partitions": t["partition_columns"],
                     "source_tables": [full], "scenes": scenes_l, "notes": note},
            "revision_id": "sha256:publayer-" + ts, "schema_version": "aof.semantic/v1",
        }
        add_resource(obj)
        built.append((full, en, cn, len(rel_resources)))

    # 落盘（TTL 追加 / resources 增量合并）
    with open(TTL, "a", encoding="utf-8") as f:
        f.write("\n".join(new_ttl))
    resources.extend(new_resources)
    RES.write_text(json.dumps(res_data, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 建模完成 ===")
    print(f"新建实体: {len(built)}  关系: {len(new_resources) - len(built)}  跳过: {skipped}")
    for full, en, cn, nrel in built:
        print(f"  {en:32s} {cn:24s} rels={nrel}  {full}")
    print(f"\nTTL 追加 {len(new_ttl)} 行；resources 总数 {len(resources)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
