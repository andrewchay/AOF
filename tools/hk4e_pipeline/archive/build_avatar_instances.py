#!/usr/bin/env python3
"""Build internal-only Avatar instances, RDF and AOF context assertions."""
import csv, hashlib, json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

AOF=Path('/Users/chaihao/LLM/AOF'); sys.path.insert(0,str(AOF))
from bridge.context_exchange.contracts import ContextAssertion, ContextAssertionEvidence
from bridge.semantic_core.models import ResourceKind, SemanticResource
from bridge.semantic_core.adapters.base import AdapterContext
from bridge.semantic_core.canonical import canonical_data
from rdflib import Graph, Namespace, Literal, RDF, RDFS, OWL, URIRef
from rdflib.namespace import XSD

BASE=Path.home()/'.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta'
DIR=BASE/'instances'; AOFOUT=BASE/'aof'; OBSERVED='2026-09-17T18:07:00+08:00'
FILES={
 'element_name':DIR/'avatar_element_name_20260916.json',
 'element_type':DIR/'avatar_element_type_20260913.json',
 'gacha':DIR/'avatar_gacha_mapping_type104.json'}
def rows(k): return json.loads(FILES[k].read_text())['rows']
def digest(p): return 'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()

name_by=defaultdict(list); type_by=defaultdict(list); gacha_by={}
for r in rows('element_name'): name_by[str(r['avatar_id'])].append(r)
for r in rows('element_type'): type_by[str(r['avatar_id'])].append(r)
for r in rows('gacha'): gacha_by[str(r['avatar_id'])]=r

# Derive the numeric enum solely from IDs where both internal sources are single-valued.
pair_votes=defaultdict(lambda:defaultdict(int))
for aid in set(name_by)&set(type_by):
 ns={r['element_name'] for r in name_by[aid]}; ts={str(r['element_type']) for r in type_by[aid]}
 if len(ns)==1 and len(ts)==1:
  pair_votes[next(iter(ts))][next(iter(ns))]+=1
type_to_name={t:max(v,key=v.get) for t,v in pair_votes.items()}
expected={'1':'Fire','2':'Water','3':'Grass','4':'Electro','5':'Ice','7':'Wind','8':'Earth'}
assert type_to_name==expected,(type_to_name,expected)

instances=[]
for aid in sorted(set(name_by)|set(type_by)|set(gacha_by),key=int):
 nrows=name_by.get(aid,[]); trows=type_by.get(aid,[]); gr=gacha_by.get(aid)
 names=sorted({r['element_name'] for r in nrows})
 types=sorted({int(r['element_type']) for r in trows})
 if not names and types: names=sorted({type_to_name[str(x)] for x in types if str(x) in type_to_name})
 evidence=[]
 if nrows: evidence.append({'source':'ads_hk4e.ads_hk4e_tower_combat_avatar_relation_di','partition':{'logregion':'cn_gf01','logdate':'2026-09-16'},'evidence_rows':sum(int(r['evidence_rows']) for r in nrows),'artifact':FILES['element_name'].name})
 if trows: evidence.append({'source':'ads_hk4e.ads_hk4e_recommend_avatar_relation_result','partition':{'logdate':'2026-09-13'},'evidence_rows':sum(int(r['evidence_rows']) for r in trows),'artifact':FILES['element_type'].name})
 if gr: evidence.append({'source':'ads_hk4e.ads_hk4e_user_gacha_avatar_hold_period','partition':{'logregion':'cn_gf01','type_num':104},'evidence_rows':int(gr['get_user_num']),'artifact':FILES['gacha'].name})
 anomalies=[]
 if int(aid)>=100000000: anomalies.append('nonstandard_avatar_id_width')
 if len(types)>1: anomalies.append('multi_element_avatar')
 if not names: anomalies.append('element_unresolved')
 inst={
  'instance_id':f'hk4e:Avatar_{aid}','entity_type':'Avatar','avatar_id':int(aid),
  'avatar_name':None,'avatar_name_status':'unresolved_internal_name_source_unavailable',
  'element_types':types,'element_names':names,
  'avatarcard_id':int(gr['avatarcard_id']) if gr else None,
  'avatar_star':int(gr['avatar_star']) if gr else None,
  'is_up_in_type_104':bool(int(gr['is_up_avatar'])) if gr else None,
  'confidence':{'identity':1.0,'element':1.0 if names else None,'gacha_attributes':0.95 if gr else None,'avatar_name':0.0},
  'anomalies':anomalies,'evidence':evidence}
 instances.append(inst)

(DIR/'avatar_instances.json').write_text(json.dumps({'policy':'internal-only; avatar_name unresolved','count':len(instances),'element_type_dictionary':type_to_name,'instances':instances},ensure_ascii=False,indent=2)+'\n')
with open(DIR/'avatar_instances.jsonl','w',encoding='utf-8') as f:
 for x in instances:f.write(json.dumps(x,ensure_ascii=False)+'\n')
with open(DIR/'avatar_instances.csv','w',newline='',encoding='utf-8') as f:
 fields=['avatar_id','avatar_name','avatar_name_status','element_types','element_names','avatarcard_id','avatar_star','is_up_in_type_104','anomalies']
 w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
 for x in instances:w.writerow({k:('|'.join(map(str,x[k])) if isinstance(x[k],list) else x[k]) for k in fields})

# RDF instances, intentionally no fabricated avatar_name.
HK=Namespace('http://hk4e.mihoyo.com/ontology#'); PROV=Namespace('http://www.w3.org/ns/prov#')
g=Graph();g.bind('hk4e',HK);g.bind('prov',PROV);g.bind('owl',OWL)
g.add((HK.avatarNameStatus,RDF.type,OWL.DatatypeProperty));g.add((HK.avatarNameStatus,RDFS.domain,HK.Avatar));g.add((HK.avatarNameStatus,RDFS.range,XSD.string))
g.add((HK.isUpInType104,RDF.type,OWL.DatatypeProperty));g.add((HK.isUpInType104,RDFS.domain,HK.Avatar));g.add((HK.isUpInType104,RDFS.range,XSD.boolean))
for x in instances:
 s=HK[f"Avatar_{x['avatar_id']}"];g.add((s,RDF.type,HK.Avatar));g.add((s,HK.Avatar_avatar_id,Literal(x['avatar_id'],datatype=XSD.integer)))
 g.add((s,RDFS.label,Literal(f"Avatar {x['avatar_id']} (name unresolved)",lang='en')));g.add((s,HK.avatarNameStatus,Literal(x['avatar_name_status'])))
 for v in x['element_types']:g.add((s,HK.Avatar_element_type,Literal(v,datatype=XSD.integer)))
 for v in x['element_names']:g.add((s,HK.Avatar_element_name,Literal(v)))
 if x['avatarcard_id'] is not None:g.add((s,HK.Avatar_avatarcard_id,Literal(x['avatarcard_id'],datatype=XSD.integer)))
 if x['avatar_star'] is not None:g.add((s,HK.Avatar_avatar_star,Literal(x['avatar_star'],datatype=XSD.integer)))
 if x['is_up_in_type_104'] is not None:g.add((s,HK.isUpInType104,Literal(x['is_up_in_type_104'],datatype=XSD.boolean)))
 for ev in x['evidence']:g.add((s,PROV.wasDerivedFrom,URIRef('mcp://andrew-mcp/cn/'+ev['source'])))
g.serialize(destination=str(DIR/'avatar_instances.ttl'),format='turtle')
# Build a single graph consumable by AOF/cognee: schema + semantic ontology + instances.
unified=AOFOUT/'hk4e-unified.ttl'
if unified.exists():
    merged=Graph(); merged.parse(str(unified),format='turtle'); merged.parse(str(DIR/'avatar_instances.ttl'),format='turtle')
    merged.serialize(destination=str(AOFOUT/'hk4e-unified-with-instances.ttl'),format='turtle')

# AOF ContextAssertion contracts + promoted SemanticResource equivalents.
ctx=AdapterContext(tenant='mihoyo',domain='hk4e',owner='hao.chai'); assertions=[]; resources=[]
file_hash={k:digest(v) for k,v in FILES.items()}
for x in instances:
 evs=[]
 for ev in x['evidence']:
  key={'avatar_element_name_20260916.json':'element_name','avatar_element_type_20260913.json':'element_type','avatar_gacha_mapping_type104.json':'gacha'}[ev['artifact']]
  evs.append(ContextAssertionEvidence.create(evidence_id=f"avatar-{x['avatar_id']}-{key.replace('_','-')}",source_ref='mcp://andrew-mcp/cn/'+ev['source'],content_hash=file_hash[key],observed_at=OBSERVED,disclosure='reference'))
 attrs=[]
 if x['element_names']:attrs.append('element='+','.join(x['element_names']))
 if x['avatar_star'] is not None:attrs.append(f"star={x['avatar_star']}")
 if x['avatarcard_id'] is not None:attrs.append(f"avatarcard_id={x['avatarcard_id']}")
 statement=f"Avatar {x['avatar_id']} has internally observed attributes: "+('; '.join(attrs) if attrs else 'identity only')+"; avatar_name unresolved."
 assertion=ContextAssertion.create(assertion_id=f"avatar-{x['avatar_id']}-profile",category='fact',statement=statement,confidence=min(v for v in x['confidence'].values() if v is not None and v>0),evidence=evs,valid_time={'observed_at':OBSERVED})
 assertions.append(assertion.to_dict())
 resources.append(SemanticResource.create(resource_id=ctx.resource_id('context-assertion',f"avatar-{x['avatar_id']}-profile"),kind=ResourceKind.CONTEXT_ASSERTION,name=f"avatar-{x['avatar_id']}-profile",display_name=f"Avatar {x['avatar_id']} 属性断言",domain='hk4e',owner='hao.chai',description=statement,tags=('avatar','instance','internal-only'),depends_on=(ctx.resource_id('object-type','avatar'),),evidence=[e.to_dict() for e in evs],valid_time={'observed_at':OBSERVED},spec={'assertion':assertion.to_dict(),'instance':x}))

def dump(r):return canonical_data({'resource_id':r.resource_id,'kind':r.kind.value,'name':r.name,'domain':r.domain,'owner':r.owner,'display_name':r.display_name,'description':r.description,'tags':list(r.tags),'depends_on':list(r.depends_on),'evidence':r.evidence,'security_policy':r.security_policy,'valid_time':r.valid_time,'spec':r.spec,'revision_id':r.revision_id,'schema_version':r.schema_version})
(DIR/'avatar_context_assertions.json').write_text(json.dumps({'schema_version':'aof.context/v1','assertions':assertions},ensure_ascii=False,indent=2)+'\n')
(DIR/'avatar_instance_resources.json').write_text(json.dumps({'schema_version':'aof.semantic/v1','resources':[dump(r) for r in resources]},ensure_ascii=False,indent=2)+'\n')
base=json.loads((AOFOUT/'resources.json').read_text()); combined=base['resources']+[dump(r) for r in resources]
(DIR/'resources_with_avatar_instances.json').write_text(json.dumps({'schema_version':'aof.semantic/v1','resources':combined},ensure_ascii=False,indent=2)+'\n')
print('instances',len(instances),'assertions',len(assertions),'combined_resources',len(combined),'triples',len(g))
