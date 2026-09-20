#!/usr/bin/env python3
"""Add internally evidenced Avatar instance attributes and source tables."""
import json
from pathlib import Path
p=Path.home()/'.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta/ontology/entities.json'
d=json.loads(p.read_text()); a=d['entities']['Avatar']
for attr in ['element_name','avatarcard_id']:
    if attr not in a['attrs']: a['attrs'].append(attr)
for table in [
 'ads_hk4e.ads_hk4e_tower_combat_avatar_relation_di',
 'ads_hk4e.ads_hk4e_recommend_avatar_relation_result',
 'ads_hk4e.ads_hk4e_user_gacha_avatar_hold_period']:
    if table not in a['source_tables']: a['source_tables'].append(table)
p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(a,ensure_ascii=False,indent=2))
