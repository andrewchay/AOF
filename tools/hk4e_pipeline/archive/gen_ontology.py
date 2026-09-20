#!/usr/bin/env python3
"""生成本体骨架 JSON：entities.json + relations.json + domains.json"""
import json, glob, os

BASE = os.path.expanduser("~/.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta")

def load(db, table):
    fp = os.path.join(BASE, db, f"{table}.json")
    if os.path.exists(fp):
        return json.load(open(fp))
    return None

# ===== 实体定义（手工提炼自 dim 层分析）=====
entities = {
  "Avatar": {"cn":"角色","domain":"GameEntity","pk":"avatar_id","pk_type":"int",
    "attrs":["avatar_name","avatar_star","element_type","skill_depot_id","release_version","release_part"],
    "source_tables":["dim_hk4e.dim_hk4e_avatar_id_start_info","dim_hk4e.avatar_element_type","dim_hk4e.dim_hk4e_anecdote_map_info"]},
  "Weapon": {"cn":"武器","domain":"GameEntity","pk":"weapon_id","pk_type":"int",
    "attrs":["weapon_rating"],"source_tables":["dwb_hk4e.dwb_inr_pqt_comp_hk4e_weapon_rating"]},
  "Reliquary": {"cn":"圣遗物","domain":"GameEntity","pk":"relic_affix_id","pk_type":"int",
    "attrs":["main_prop_id","affix"],"source_tables":["dwb_hk4e.dwb_inr_pqt_comp_hk4e_reliquary_rating"]},
  "Item": {"cn":"道具/材料","domain":"GameEntity","pk":"item_id","pk_type":"int",
    "attrs":["item_name","item_type","value","rank"],
    "source_tables":["dim_hk4e.dim_hk4e_resource_mapping","dim_hk4e.dim_item_value_info","dim_hk4e.dim_daily_report_material_info"]},
  "Monster": {"cn":"怪物","domain":"GameEntity","pk":"monster_id","pk_type":"int",
    "attrs":["monster_tag"],"source_tables":["dim_hk4e.dim_hk4e_monster_tag_df"]},
  "Boss": {"cn":"Boss","domain":"GameEntity","pk":"boss_id","pk_type":"int",
    "attrs":["real_boss_id","boss_name","scene_id","type","version_num"],
    "source_tables":["dim_hk4e.dim_hk4e_boss_mapping"]},
  "Dungeon": {"cn":"副本/秘境","domain":"GameEntity","pk":"dungeon_id","pk_type":"int",
    "attrs":["dungeon_type","dungeon_name","level_id","dungeon_avatar_id"],
    "source_tables":["dim_hk4e.dim_hk4e_dungeon_mapping","dim_hk4e.dim_hk4e_char_master_avatar_mapping"]},
  "Quest": {"cn":"任务","domain":"GameEntity","pk":"quest_id","pk_type":"int",
    "attrs":["quest_name","quest_type_name","parent_task_id","subtasks_start_id","subtasks_end_id"],
    "source_tables":["dim_hk4e.dim_hk4e_quest_name_df","dim_hk4e.dim_hk4e_main_task_list","dim_hk4e.dim_hk4e_legend_task_list"]},
  "Scene": {"cn":"场景","domain":"GameEntity","pk":"scene_id","pk_type":"int",
    "attrs":["transport_id","transport_name","god_statue_tag","version"],
    "source_tables":["dim_hk4e.dim_hk4e_transport_scene_id_df"]},
  "Area": {"cn":"区域","domain":"GameEntity","pk":"area_id","pk_type":"int",
    "attrs":["level1_area_id","all_point","city_id","second_area_id"],
    "source_tables":["dim_hk4e.dim_hk4e_explore_mapping","dim_hk4e.dim_hk4e_transport_area_id_df"]},
  "City": {"cn":"城市","domain":"GameEntity","pk":"city_id","pk_type":"int",
    "attrs":["area_id"],"source_tables":["dim_hk4e.dim_hk4e_city_area_mapping"]},
  "Achievement": {"cn":"成就","domain":"GameEntity","pk":"id","pk_type":"int",
    "attrs":["group_id"],"source_tables":["dim_hk4e.dim_hk4e_achievement_mapping"]},
  "Codex": {"cn":"图鉴","domain":"GameEntity","pk":"type","pk_type":"int",
    "attrs":["codex_all_num","version","name"],"source_tables":["dim_hk4e.dim_hk4e_codex_mapping"]},
  "Fashion": {"cn":"时装/衣装","domain":"GameEntity","pk":"fashion_id","pk_type":"int",
    "attrs":["goods_id","avatar_id","version","avatar_name","clothes_name"],
    "source_tables":["dim_hk4e.dim_hk4e_version_clothes"]},
  "Gacha": {"cn":"卡池","domain":"Commerce","pk":"schedule_id","pk_type":"int",
    "attrs":["region","type","type_num","start_date","end_date","version_num","avatar_id1","avatar_id2"],
    "source_tables":["dim_hk4e.gacha_schedule_v2_simplify"]},
  "Player": {"cn":"玩家","domain":"Player","pk":"uid","pk_type":"int",
    "attrs":["register_date","register_platform","age","gender","city_level","active_level","user_label","aid","tag"],
    "source_tables":["dim_hk4e.dim_hk4e_ugc_user_profile_td"]},
  "Account": {"cn":"通行证账号","domain":"Player","pk":"account_id","pk_type":"int",
    "attrs":["qd_uid","gf_uid","xiaomi_qd_uid"],
    "source_tables":["dwd_hk4e.dwd_hk4e_account_relate_info_td","dim_hk4e.dim_hk4e_gf_uid_mapping_xiaomi_qd_uid"]},
  "Creator": {"cn":"UGC创作者","domain":"UGC","pk":"creator_id","pk_type":"string",
    "attrs":["creator_aid","creator_name","creator_status","kolugc_level","agency_code","follow_cnt","follow_level","creator_lvl"],
    "source_tables":["dim_hk4e.dim_hk4e_ugc_creator_df"]},
  "UGCLevel": {"cn":"UGC关卡","domain":"UGC","pk":"level_id","pk_type":"bigint",
    "attrs":["game_uid","level_name","level_desc","publish_type","tags","play_type","play_cate","first_online_time","latest_online_time"],
    "source_tables":["dim_hk4e.dim_hk4e_ugc_level_info_hf","dim_hk4e.dim_hk4e_ugc_level_info_analytics_df"]},
  "Platform": {"cn":"平台","domain":"Platform","pk":"platform","pk_type":"int",
    "attrs":["platform_name","platform_type_1","platform_type_2","platform_type_3"],
    "source_tables":["dim_hk4e.dim_hk4e_platform_name_df","dim_hk4e.dim_hk4e_platform_cube"]},
  "Device": {"cn":"设备","domain":"Platform","pk":"device_id","pk_type":"string",
    "attrs":["device_uuid","uuid","device_name"],"source_tables":["dim_hk4e.dim_hk4e_ios_device_mapping","dim_hk4e.dim_hk4e_client_customized_device_id_df"]},
  "Gpu": {"cn":"显卡","domain":"Platform","pk":"gpu_regrex","pk_type":"string",
    "attrs":["gpu_performance_type"],"source_tables":["dim_hk4e.dim_hk4e_android_gpu_regrex_category_df","dim_hk4e.dim_hk4e_ios_gpu_regrex_category_df"]},
  "Country": {"cn":"国家","domain":"Platform","pk":"country","pk_type":"string",
    "attrs":["country_type","is_core","core_country","country_name"],
    "source_tables":["dim_hk4e.dim_hk4e_country_type","dim_hk4e.dim_hk4e_core_country","dim_hk4e.dim_hk4e_country_name_map_df"]},
  "GameVersion": {"cn":"游戏版本","domain":"Time","pk":"version_num","pk_type":"int",
    "attrs":["version_name","version_start_date","version_end_date"],
    "source_tables":["dim_hk4e.dim_hk4e_game_version","dim_hk4e.dim_hk4e_game_version_public_df","dim_hk4e.logdate_version_map"]},
  "Schedule": {"cn":"档期","domain":"Time","pk":"schedule_id","pk_type":"int",
    "attrs":["start_date"],"source_tables":["dim_hk4e.battle_pass_schedule","dim_hk4e.role_combat_schedule","dim_hk4e.tower_schedule"]},
  "Product": {"cn":"充值商品","domain":"Commerce","pk":"product_name","pk_type":"string",
    "attrs":["product_price_usd","commercial_tp","product_type","is_been_blocked"],
    "source_tables":["dim_hk4e.dim_hk4e_product_id_price_mapping"]},
  "PriceTier": {"cn":"价格档位","domain":"Commerce","pk":"tier_id","pk_type":"string",
    "attrs":["country","currency","price","start_date","end_date"],
    "source_tables":["dim_hk4e.dim_hk4e_t_price_tier_scd","dim_hk4e.dim_hk4e_t_price_tier_daily"]},
  "Language": {"cn":"语言","domain":"Platform","pk":"language_type","pk_type":"int",
    "attrs":["en_name","cn_name","type_name"],"source_tables":["dim_hk4e.dim_hk4e_language_type"]},
}

# ===== 关系定义 =====
relations = [
  {"from":"Player","to":"Account","type":"HAS_ACCOUNT","via":["uid","qd_uid/gf_uid"],"card":"1:N","note":"一个玩家可关联 qd_uid/gf_uid 多账号体系"},
  {"from":"Player","to":"Creator","type":"MAY_BE","via":["uid","creator_uid"],"card":"1:0..1","note":"玩家可能是创作者"},
  {"from":"Account","to":"Creator","type":"identifies","via":["aid","creator_aid"],"card":"1:0..1"},
  {"from":"Player","to":"UGCLevel","type":"CREATES","via":["uid","game_uid"],"card":"1:N"},
  {"from":"UGCLevel","to":"Creator","type":"authored_by","via":["game_uid","creator_uid"],"card":"N:1"},
  {"from":"Avatar","to":"Item","type":"NEEDS_MATERIAL","via":["avatar_id","material_id"],"card":"M:N","note":"培养材料，见 biz_hk4e_avatar_material_mapping"},
  {"from":"Avatar","to":"Fashion","type":"HAS_COSTUME","via":["avatar_id","fashion_id"],"card":"1:N"},
  {"from":"Fashion","to":"Product","type":"sold_as","via":["goods_id"],"card":"N:1"},
  {"from":"Dungeon","to":"Avatar","type":"REWARDS/DESIGNED_FOR","via":["dungeon_id","dungeon_avatar_id"],"card":"N:1"},
  {"from":"Dungeon","to":"Scene","type":"LOCATED_IN","via":["dungeon_id","scene_id"],"card":"N:1"},
  {"from":"Boss","to":"Monster","type":"IS_A","via":["boss_id","real_boss_id"],"card":"N:1"},
  {"from":"Boss","to":"Scene","type":"APPEARS_IN","via":["scene_id"],"card":"N:1"},
  {"from":"Quest","to":"Quest","type":"SUBTASK_OF","via":["id","parent_task_id"],"card":"N:1","note":"任务树自关联"},
  {"from":"Area","to":"Area","type":"PART_OF","via":["area_id","level1_area_id"],"card":"N:1","note":"区域层级"},
  {"from":"Scene","to":"Area","type":"IN_AREA","via":["scene_id","area_id"],"card":"N:1"},
  {"from":"City","to":"Area","type":"CONTAINS","via":["city_id","area_id"],"card":"1:N"},
  {"from":"Achievement","to":"Achievement","type":"GROUPED_IN","via":["id","group_id"],"card":"N:1"},
  {"from":"Gacha","to":"Avatar","type":"UP_AVATAR","via":["schedule_id","avatar_id1/2"],"card":"1:N","note":"卡池UP角色"},
  {"from":"Gacha","to":"GameVersion","type":"IN_VERSION","via":["version_num"],"card":"N:1"},
  {"from":"Event","to":"Player","type":"ACTED_BY","via":["uid"],"card":"N:1","note":"所有事实表核心外键"},
  {"from":"Event","to":"Avatar","type":"ABOUT","via":["avatar_id"],"card":"N:1"},
  {"from":"Event","to":"GameVersion","type":"IN_VERSION","via":["game_version","version_num"],"card":"N:1"},
  {"from":"Event","to":"Platform","type":"ON_PLATFORM","via":["platform"],"card":"N:1"},
  {"from":"Event","to":"Device","type":"FROM_DEVICE","via":["device_id","device_uuid"],"card":"N:1"},
  {"from":"Event","to":"Country","type":"FROM_COUNTRY","via":["country"],"card":"N:1"},
  {"from":"Device","to":"Gpu","type":"HAS_GPU","via":["gpu_regrex"],"card":"N:1"},
  {"from":"Product","to":"PriceTier","type":"PRICED_BY","via":["product_name","tier_id"],"card":"1:N"},
  {"from":"Product","to":"Country","type":"SOLD_IN","via":["country"],"card":"N:M"},
  {"from":"Event","to":"Action","type":"HAS_ACTION","via":["action_id"],"card":"N:1","note":"→ dim_hk4e_action_id_mapping"},
]

# ===== 域定义 =====
domains = {
  "Player":   {"cn":"玩家域","entities":["Player","Account","Creator"]},
  "GameEntity":{"cn":"游戏内容域","entities":["Avatar","Weapon","Reliquary","Item","Monster","Boss","Dungeon","Quest","Scene","Area","City","Achievement","Codex","Fashion"]},
  "Combat":   {"cn":"战斗域","entities":[]},
  "Commerce": {"cn":"商业域","entities":["Gacha","Product","PriceTier"]},
  "UGC":      {"cn":"UGC创作域","entities":["Creator","UGCLevel"]},
  "Platform": {"cn":"平台设备域","entities":["Platform","Device","Gpu","Country","Language"]},
  "Time":     {"cn":"时间版本域","entities":["GameVersion","Schedule"]},
}

meta = {
  "version": "0.1",
  "generated_from": "hk4e CN region metadata snapshot 2026-09-17",
  "note": "本体结构层基于全库元数据；实例层数据受 dim_hk4e 无SELECT权限约束",
  "entity_count": len(entities),
  "relation_count": len(relations),
  "domain_count": len(domains),
}

os.makedirs(os.path.join(BASE, "ontology"), exist_ok=True)
out = os.path.join(BASE, "ontology")
with open(os.path.join(out, "entities.json"),"w",encoding="utf-8") as f:
    json.dump({"meta":meta,"entities":entities}, f, ensure_ascii=False, indent=2)
with open(os.path.join(out, "relations.json"),"w",encoding="utf-8") as f:
    json.dump({"meta":meta,"relations":relations}, f, ensure_ascii=False, indent=2)
with open(os.path.join(out, "domains.json"),"w",encoding="utf-8") as f:
    json.dump({"meta":meta,"domains":domains}, f, ensure_ascii=False, indent=2)

print(f"entities: {len(entities)}")
print(f"relations: {len(relations)}")
print(f"domains: {len(domains)}")
print(f"output: {out}")
