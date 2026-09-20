#!/usr/bin/env python3
"""playbook 需求建模·第二批：5 张 hk4e 族（catalog）+ 13 张跨域表（catalog_crossdb）。

跨域表决策：仅建 SemanticResource（可检索），不写入 hk4e TTL/图谱——它们属于
growth/community/advert 域，不应混入游戏域本体；FK 关系仍指向 hk4e 域类（uid→玩家）。
"""
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / "data" / "semantic_assets"
AOF_DIR = BASE
TTL, RES = AOF_DIR / "hk4e-complete.ttl", AOF_DIR / "resources_full.json"
PUB = "http://hk4e.mihoyo.com/ontology/pub#"
SQL2XSD = {"int": "integer", "bigint": "long", "smallint": "short", "string": "string",
           "double": "double", "float": "float", "decimal": "decimal", "boolean": "boolean",
           "date": "date", "timestamp": "dateTime"}

# (full, EN, cn, kind, domain_label, note)
CURATED_V2 = [
    ("biz_hk4e.all_quest_id_type_list", "AllQuestIdTypeList", "全量任务类型清单", "Dimension", "GameEntity", "任务/任务类型映射清单"),
    ("biz_hk4e.hk4e_avatar_upinfo", "Hk4eAvatarUpinfo", "角色基础信息表", "Dimension", "GameEntity", "character-poi 场景角色主数据"),
    ("biz_hk4e.quest_level_limit", "QuestLevelLimit", "任务等级限制表", "Dimension", "GameEntity", "任务解锁等级约束"),
    ("biz_hk4e.world_quest_list", "WorldQuestList", "世界任务清单", "Dimension", "GameEntity", "世界任务枚举"),
    ("dws_hk4e.dws_hk4e_sdk_recall_push_daily", "SdkRecallPushDaily", "SDK召回推送日表", "Fact", "Player", "召回触达/推送结果"),
    ("ads_advert.ads_advert_rta_hk4e_uid_login_device_df", "AdsAdvertRtaLoginDeviceDf", "RTA用户登录设备表", "Fact", "PlatformDevice", "uid+aid 双键；登录设备/注册日期/末活"),
    ("biz_growth.biz_growth_wf_zrr_final_person_map", "GrowthWfZrrFinalPersonMap", "流失回流终版人映射(zrr)", "Fact", "Player", "game_uid 即 uid；工作流产出映射"),
    ("biz_growth.biz_growth_wf_zrr_first_level_fp_info_seg", "GrowthWfZrrFirstLevelFpInfoSeg", "首日一级特征分段宽表(zrr)", "Fact", "Player", "36 列首日特征分段"),
    ("biz_growth.cz_game_user_battle_nd_v2", "CzGameUserBattleNdV2", "用户战斗行为画像日表(cz-v2)", "Fact", "Combat", "⚠️ 深渊参与为 T1 离线快照，锚点滞后≥3天不能直接解读为未参与，须交叉 dws_hk4e_user_tower_avatar_info_1d"),
    ("biz_growth.cz_game_user_profile_nd_v2", "CzGameUserProfileNdV2", "用户综合画像日表(cz-v2)", "Fact", "Player", "⚠️ 含实名性别/年龄等敏感字段，输出侧禁用；account_uid 来自 uid_aid_full_daily 桥接"),
    ("biz_growth.cz_user_game_routine_last_nd_v2", "CzUserGameRoutineLastNdV2", "用户近期习惯末次快照(cz-v2)", "Fact", "Player", "⚠️ last_login_* 为 T2 快照口径，值对应最后一次登录日"),
    ("biz_growth.hk4e_user_age_info_0330", "Hk4eUserAgeInfo0330", "用户年龄信息快照(0330)", "Entity", "Player", "describe 返回 0 列（权限或表状态待核实）"),
    ("biz_uprofile.biz_uprofile_account_game_active_core_value_daily", "UprofileAccountGameCoreValueDaily", "账号游戏活跃核心价值日表", "Fact", "Player", "账号×游戏粒度（非 uid），DAU sanity check 备用"),
    ("dwd_community.dwd_community_forum_post_reply_pid_snapshot", "CommunityForumPostReplySnapshot", "社区论坛回复快照", "Fact", "UGC", "post_id/reply_id/uid/r_uid"),
    ("dwd_growth.dwd_growth_creator_artwork_log_di", "GrowthCreatorArtworkLogDi", "创作者作品日志日增", "Fact", "UGC", "社媒回传作品/任务日志，platform_code+game_code 分区"),
    ("dwd_growth.dwd_growth_hk4e_user_profile_df", "GrowthHk4eUserProfileDf", "增长侧用户画像全量", "Fact", "Player", "aid+uid 双键；风控小号 tag"),
    ("dwd_growth.dwd_growth_hk4e_user_tag_df", "GrowthHk4eUserTagDf", "增长侧用户标签全量", "Fact", "Player", "uid；含总充值金额"),
    ("dws_growth.dws_growth_creator_artwork_order_nd", "GrowthCreatorArtworkOrderNd", "创作者作品投稿订单日表", "Fact", "UGC", "168 列宽表；投稿/订单主表"),
]


def main() -> int:
    ts = time.strftime("%Y%m%d-%H%M%S")
    catalog = {t["full_name"]: t for t in json.loads((BASE / "catalog.json").read_text())["tables"]}
    crossdb = json.loads((BASE / "catalog_crossdb.json").read_text())
    data = json.loads(RES.read_text())
    res = data["resources"]
    existing_ids = {r["resource_id"] for r in res}
    existing_names = {r["name"].lower() for r in res if r["kind"] == "ObjectType"}
    ttl_text = TTL.read_text()

    # 域 IRI 动态解析：zh label → subject
    import re
    dom_iri = {}
    for m in re.finditer(r"(hk4e:[A-Za-z0-9_]+) a owl:Class ;\n\s+rdfs:label \"[^\"]+\"@en,\s*\"([^\"]+)\"@zh", ttl_text):
        dom_iri[m.group(2)] = m.group(1)
    dom_iri.setdefault("Player", "hk4e:Player")
    print("域解析:", {k: v for k, v in dom_iri.items() if k in
          ("玩家", "战斗域", "游戏内容域", "商业域", "平台设备域", "UGC创作域", "时间版本域", "角色", "游戏版本")})

    def schema_of(full: str):
        if full in catalog:
            t = catalog[full]
            return t["columns"], t["partition_columns"], t["database"].split("_")[0]
        t = crossdb[full]
        if "columns" not in t:  # describe 返回 error 的表（如权限问题）
            return [], [], full.split("_")[0]
        cols = [{"name": c["column_name"], "type": c["data_type"], "comment": c.get("comment", ""),
                 "is_partition": bool(c.get("partition"))} for c in t["columns"]]
        parts = [c["name"] for c in cols if c["is_partition"]]
        return cols, parts, full.split("_")[0]

    def ttl_pub_prop_exists(local: str) -> bool:
        return f"ontology/pub#{local}>" in ttl_text

    new_ttl: list[str] = []
    added_ot = added_rt = 0
    crossdb_no_ttl = []

    for full, en, cn, kind, dom_label, note in CURATED_V2:
        if en.lower() in existing_names:
            print(f"!! 实体名冲突，跳过: {en}")
            continue
        cols, parts, layer = schema_of(full)
        col_names = {c["name"] for c in cols}
        scenes = sorted({md.parent.parent.name for md in Path("/Users/chaihao/LLM/brando-playbook-game-growth/playbooks").glob("*/reference/表.md")
                         if full in md.read_text()})
        is_hk4e = full.split(".")[0].endswith("_hk4e") or full.split(".")[0] in ("dws_hk4e", "dwd_hk4e", "dim_hk4e", "dwb_hk4e", "ads_hk4e", "biz_hk4e")
        dom_iri_resolved = dom_iri.get(dom_label, "hk4e:GameEntity")

        if is_hk4e:
            iri = f"<{PUB}{en}>"
            parts_s = ",".join(parts) or "无"
            comment = (f"kind={kind}; layer={layer}; logical_name={full.split('.',1)[1]}; tables=1; "
                       f"partitions={parts_s}; source={full}; scenes={','.join(scenes) or '-'}; notes={note}")
            new_ttl += [f"{iri} a owl:Class ;", f"    rdfs:label \"{en}\"@en,\n        \"{cn}\"@zh ;",
                        f"    rdfs:comment \"{comment}\"@en ;",
                        f"    rdfs:subClassOf {dom_iri_resolved} .", ""]
            for c in cols:
                pn = f"{en}_{c['name']}"
                if ttl_pub_prop_exists(pn):
                    continue
                new_ttl += [f"<{PUB}{pn}> a owl:DatatypeProperty ;",
                            f"    rdfs:label \"{c['comment'] or c['name']}\"@zh ;",
                            f"    rdfs:domain {iri} ;",
                            f"    rdfs:range xsd:{SQL2XSD.get(c['type'], 'string')} .", ""]
        else:
            crossdb_no_ttl.append(en)

        fks = []
        if "uid" in col_names:
            fks.append(("uid", "Player", dom_iri.get("玩家", "hk4e:Player"), "玩家"))
        if "avatar_id" in col_names and "角色" in dom_iri:
            fks.append(("avatar_id", "Avatar", dom_iri["角色"], "角色"))
        if "version_num" in col_names and "游戏版本" in dom_iri:
            fks.append(("version_num", "GameVersion", dom_iri["游戏版本"], "游戏版本"))
        for col, ten, tiri, tzh in fks:
            prop_local = f"{en}_{col}_rel"
            if is_hk4e and ttl_pub_prop_exists(prop_local):
                continue
            if is_hk4e:
                new_ttl += [f"<{PUB}{prop_local}> a owl:ObjectProperty ;",
                            f"    rdfs:label \"{en}--{col}-->{ten}\"@en ;",
                            f"    rdfs:comment \"card=N:1; confidence=medium; via={col}（标准键，非声明外键）\"@en ;",
                            f"    rdfs:domain <{PUB}{en}> ;",
                            f"    rdfs:range {tiri} .", ""]
            rid = f"aof://mihoyo/hk4e/relation-type/{en.lower()}-{col}-{tzh}"
            if rid not in existing_ids:
                existing_ids.add(rid)
                res.append({
                    "resource_id": rid, "kind": "RelationType", "name": f"{en.lower()}-{col}-{tzh}",
                    "domain": "hk4e", "owner": "hao.chai",
                    "display_name": f"{cn} --{col}--> {tzh}",
                    "description": f"标准键关联：{cn} 经列 {col} 指向 {tzh}（N:1，置信 medium）",
                    "tags": ["relation", "playbook"],
                    "depends_on": ["aof://mihoyo/hk4e/ontology/hk4e-data-ontology"], "evidence": [],
                    "spec": {"from": en, "to": ten, "type": col, "via": [col], "card": "N:1"},
                    "revision_id": "sha256:pb2-" + ts, "schema_version": "aof.semantic/v1"})
                added_rt += 1

        rid = f"aof://mihoyo/hk4e/object-type/{en.lower()}"
        if rid not in existing_ids:
            existing_ids.add(rid)
            res.append({
                "resource_id": rid, "kind": "ObjectType", "name": en.lower(), "domain": "hk4e",
                "owner": "hao.chai", "display_name": cn,
                "description": f"{cn}（{kind}）｜逻辑名 {full.split('.',1)[1]}｜库 {full.split('.')[0]}｜分区 {','.join(parts) or '无'}｜来源: {full}",
                "tags": [kind.lower(), layer, "playbook"],
                "depends_on": ["aof://mihoyo/hk4e/ontology/hk4e-data-ontology"], "evidence": [],
                "spec": {"entity": en, "cn": cn, "kind": kind, "layer": layer,
                         "pk": next((k for k in ("uid", "game_uid", "aid", "version_num") if k in col_names), ""),
                         "attrs": [c["name"] for c in cols][:60], "attr_count": len(cols),
                         "partitions": parts, "source_tables": [full], "scenes": scenes, "notes": note},
                "revision_id": "sha256:pb2-" + ts, "schema_version": "aof.semantic/v1"})
            added_ot += 1

    if new_ttl:
        with open(TTL, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(new_ttl))
    RES.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n新建 ObjectType {added_ot}，RelationType {added_rt}")
    print(f"跨域仅资源（未入 TTL）: {len(crossdb_no_ttl)} → {', '.join(crossdb_no_ttl)}")
    print(f"resources 总数: {len(res)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
