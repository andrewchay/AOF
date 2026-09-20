#!/usr/bin/env python3
import csv,json,os,time,urllib.request
from pathlib import Path
CONF=Path.home()/'.gravitas/agent-workspaces/aof/mcp.json'
OUT=Path.home()/'.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta/instances'
OUT.mkdir(parents=True,exist_ok=True)
s=json.load(open(CONF))['servers']['andrew-mcp']; URL=s['url']; AUTH=s['headers']['Authorization']
def tool(name,args,i=1):
 body=json.dumps({'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':name,'arguments':args}}).encode()
 req=urllib.request.Request(URL,data=body,headers={'Authorization':AUTH,'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
 raw=urllib.request.urlopen(req,timeout=120).read().decode(); line=next((x[5:].strip() for x in raw.splitlines() if x.startswith('data:')),raw)
 obj=json.loads(line); return json.loads(obj['result']['content'][0]['text'])
def query(sql):
 r=tool('query_data',{'region':'cn','sql':sql})
 if r.get('task_id'):
  tid=r['task_id']
  for _ in range(60):
   st=tool('get_query_status',{'region':'cn','task_id':tid})
   if str(st.get('status','')).upper() in ('COMPLETED','SUCCEEDED'): return tool('get_query_result',{'region':'cn','task_id':tid,'timeout':20})
   if str(st.get('status','')).upper() in ('FAILED','CANCELLED'): raise RuntimeError(st)
   time.sleep(2)
  raise TimeoutError(tid)
 return r
def norm_rows(r):
 cols=r.get('columns',[]); rows=r.get('rows',[])
 return [dict(zip(cols,x)) if not isinstance(x,dict) else x for x in rows]
def save(name,sql):
 r=query(sql); rows=norm_rows(r)
 (OUT/f'{name}.json').write_text(json.dumps({'query':sql,'row_count':len(rows),'rows':rows},ensure_ascii=False,indent=2)+'\n')
 if rows:
  with open(OUT/f'{name}.csv','w',newline='',encoding='utf-8') as f:
   w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 print(name,len(rows)); return rows

save('avatar_element_name_20260916',"""
SELECT avatar_id, element_name, SUM(evidence_rows) AS evidence_rows
FROM (
  SELECT aim_avatar AS avatar_id, aim_element AS element_name, COUNT(1) AS evidence_rows
  FROM ads_hk4e.ads_hk4e_tower_combat_avatar_relation_di
  WHERE logregion='cn_gf01' AND logdate='2026-09-16'
  GROUP BY aim_avatar, aim_element
  UNION ALL
  SELECT box_avatar AS avatar_id, box_element AS element_name, COUNT(1) AS evidence_rows
  FROM ads_hk4e.ads_hk4e_tower_combat_avatar_relation_di
  WHERE logregion='cn_gf01' AND logdate='2026-09-16'
  GROUP BY box_avatar, box_element
) x
WHERE avatar_id IS NOT NULL AND element_name IS NOT NULL
GROUP BY avatar_id, element_name
ORDER BY avatar_id, evidence_rows DESC
LIMIT 1000
""")
save('avatar_element_type_20260913',"""
SELECT avatar_id, element_type, SUM(evidence_rows) AS evidence_rows
FROM (
  SELECT aim_avatar_id AS avatar_id, aim_element_type AS element_type, COUNT(1) AS evidence_rows
  FROM ads_hk4e.ads_hk4e_recommend_avatar_relation_result
  WHERE logdate='2026-09-13'
  GROUP BY aim_avatar_id, aim_element_type
  UNION ALL
  SELECT box_avatar_id AS avatar_id, box_element_type AS element_type, COUNT(1) AS evidence_rows
  FROM ads_hk4e.ads_hk4e_recommend_avatar_relation_result
  WHERE logdate='2026-09-13'
  GROUP BY box_avatar_id, box_element_type
) x
WHERE avatar_id IS NOT NULL AND element_type IS NOT NULL
GROUP BY avatar_id, element_type
ORDER BY avatar_id, evidence_rows DESC
LIMIT 1000
""")
save('avatar_gacha_mapping_type104',"""
SELECT avatar_id, avatarcard_id, avatar_star, is_up_avatar,
       SUM(get_user_num) AS get_user_num, SUM(get_avatar_num) AS get_avatar_num
FROM ads_hk4e.ads_hk4e_user_gacha_avatar_hold_period
WHERE logregion='cn_gf01' AND type_num=104
GROUP BY avatar_id, avatarcard_id, avatar_star, is_up_avatar
ORDER BY avatar_id, get_user_num DESC
LIMIT 1000
""")
