#!/usr/bin/env python3
"""Idempotently add evidence-backed Combat entities and relations to hk4e ontology JSON."""
import json
from pathlib import Path

BASE = Path.home()/'.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta/ontology'

def load(name, key):
    p=BASE/name; d=json.loads(p.read_text()); return p,d,d[key]

pe,de,entities=load('entities.json','entities')
pr,dr,relations=load('relations.json','relations')
pd,dd,domains=load('domains.json','domains')

combat_entities = {
  'CombatStage': {
    'cn':'战斗关卡','domain':'Combat','pk':'level_id','pk_type':'int',
    'attrs':['stage_id','tower_level','floor_index','level_index','round_id','difficulty','scene_type'],
    'source_tables':['dws_hk4e.dws_hk4e_role_combat_fight_info_1sche','ads_hk4e.ads_hk4e_tower_combat_detail_1d','ads_hk4e.ads_hk4e_daily_global_gcg_level_battle_info_1d']},
  'CombatTeam': {
    'cn':'战斗队伍','domain':'Combat','pk':'team_key','pk_type':'string',
    'attrs':['team_con_key','avatar_id_1','avatar_id_2','avatar_id_3','avatar_id_4','battle_num','total_hurt','total_time','dps_num'],
    'source_tables':['dws_hk4e.dws_hk4e_ai_world_team_dps_1d','dws_hk4e.dws_hk4e_ai_team_dps_rank_versionly','dim_hk4e.dim_hk4e_front_tower_team','dim_hk4e.dim_hk4e_front_role_combat_team']},
  'TowerRun': {
    'cn':'深境螺旋挑战','domain':'Combat','pk':'tower_trans','pk_type':'string',
    'attrs':['uid','schedule_id','tower_level','floor_index','level_index','path','is_win','total_time','level_star_num','combat_end_time'],
    'source_tables':['dws_hk4e.dws_hk4e_tower_combat_basic_info_daily','dws_hk4e.dws_daily_tower_fight_info','ads_hk4e.ads_hk4e_tower_combat_detail_1d','ads_hk4e.ads_hk4e_tower_avatar_combat_stat_1d']},
  'RoleCombatRun': {
    'cn':'幻想真境剧诗挑战','domain':'Combat','pk':'transaction','pk_type':'string',
    'attrs':['fight_transaction','uid','schedule_id','round_id','difficulty','level_id','result','medal_num','total_use_time_ms','tarot_num','avatar_list'],
    'source_tables':['dws_hk4e.dws_hk4e_user_role_combat_play_settle_1d','dwd_hk4e.dwd_hk4e_role_combat_user_avatar_list_fight_info_1d','ads_hk4e.ads_hk4e_hourly_role_combat_fight_settle_1sche','ads_hk4e.ads_hk4e_hourly_role_combat_battle_settle_1sche']},
  'DungeonRun': {
    'cn':'副本挑战','domain':'Combat','pk':'transaction_id','pk_type':'string',
    'attrs':['uid','dungeon_id','dungeon_type','start_time','end_time','use_time','is_succ','avatar_list','cur_world_level'],
    'source_tables':['dws_hk4e.dws_daily_dungeon_fight_info','dws_hk4e.dws_hk4e_user_char_master_dungeon_fight_info_td','ads_hk4e.ads_hk4e_versionly_dungeon_fight_info']},
  'WorldBossFight': {
    'cn':'世界Boss战斗','domain':'Combat','pk':'transaction_id','pk_type':'string',
    'attrs':['uid','boss_id','monster_id','group_id','scene_id','start_time','end_time','battle_time','is_succ','avatar_list','cur_world_level'],
    'source_tables':['dws_hk4e.dws_daily_world_boss_fight_info','dws_hk4e.dws_inr_pqt_comp_hk4e_daily_world_boss','ads_hk4e.ads_hk4e_versionly_world_boss_fight_info']},
  'GCGCard': {
    'cn':'七圣召唤卡牌','domain':'Combat','pk':'card_id','pk_type':'int',
    'attrs':['game_type','level_id','tier','total_num','win_num','battle_num','total_times'],
    'source_tables':['ads_hk4e.ads_hk4e_daily_global_gcg_pvp_card_info_1d','ads_hk4e.ads_hk4e_versionly_global_gcg_pvp_character_card','ads_hk4e.ads_hk4e_versionly_global_gcg_pvp_card_info']},
}
entities.update(combat_entities)

def rel(frm,to,typ,via,card,note):
    return {'from':frm,'to':to,'type':typ,'via':via,'card':card,'note':note}
combat_relations = [
 rel('Player','TowerRun','ATTEMPTS_TOWER_RUN',['uid'],'1:N','玩家参与深境螺旋挑战'),
 rel('Player','RoleCombatRun','ATTEMPTS_ROLE_COMBAT_RUN',['uid'],'1:N','玩家参与幻想真境剧诗挑战'),
 rel('Player','DungeonRun','ATTEMPTS_DUNGEON_RUN',['uid'],'1:N','玩家参与副本挑战'),
 rel('Player','WorldBossFight','CHALLENGES_WORLD_BOSS',['uid'],'1:N','玩家发起世界Boss战斗'),
 rel('TowerRun','CombatStage','HAS_TOWER_STAGE',['tower_level','floor_index','level_index'],'N:1','螺旋挑战对应层间关卡'),
 rel('RoleCombatRun','CombatStage','HAS_ROLE_COMBAT_STAGE',['level_id','round_id'],'N:1','剧诗挑战对应轮次关卡'),
 rel('TowerRun','CombatTeam','USES_TOWER_TEAM',['tower_trans','avatar_id_1..4'],'N:1','螺旋场次使用队伍'),
 rel('RoleCombatRun','CombatTeam','USES_ROLE_COMBAT_TEAM',['transaction','avatar_list'],'N:1','剧诗场次使用初始/候补角色队伍'),
 rel('CombatTeam','Avatar','HAS_COMBAT_MEMBER',['team_key','avatar_id_1..4'],'M:N','队伍由角色组成'),
 rel('DungeonRun','Dungeon','TARGETS_DUNGEON',['dungeon_id'],'N:1','副本挑战对应副本实体'),
 rel('WorldBossFight','Boss','TARGETS_WORLD_BOSS',['boss_id','monster_id'],'N:1','世界Boss战斗对应Boss/怪物'),
 rel('TowerRun','Schedule','IN_TOWER_SCHEDULE',['schedule_id'],'N:1','螺旋挑战属于排期'),
 rel('RoleCombatRun','Schedule','IN_ROLE_COMBAT_SCHEDULE',['schedule_id'],'N:1','剧诗挑战属于排期'),
]
keys={(r['from'],r['to'],r['type']) for r in relations}
relations.extend(r for r in combat_relations if (r['from'],r['to'],r['type']) not in keys)
domains.setdefault('Combat',{'cn':'战斗域','entities':[]})['entities']=list(combat_entities)

de['meta']['entity_count']=len(entities)
dr['meta']['relation_count']=len(relations)
for p,d in [(pe,de),(pr,dr),(pd,dd)]:
    p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print('entities',len(entities),'relations',len(relations),'combat',len(combat_entities),len(combat_relations))
