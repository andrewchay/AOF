import asyncio, json, os, sys, tempfile, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
ROOT=Path('/private/tmp/aof-diagnosis-20260905/main')
sys.path.insert(0,str(ROOT))
sandbox=Path(tempfile.mkdtemp(prefix='aof-diagnostic-probes-'))
os.environ['AOF_ROOT']=str(sandbox)
os.environ['AOF_SEMANTIC_IDENTITY_SECRET']='diagnostic-only-secret'
from fastapi.testclient import TestClient
import services.semantic_middle_layer_api.app as api
from bridge.auth.rbac import RBACManager
from bridge.tenant.manager import TenantManager
from bridge.decision_provenance import DecisionProvenanceStore
results={}
client=TestClient(api.app)
results['anonymous_metadata_status']=client.post('/v1/ingest/metadata',json={'topic':'diagnostic','metadata':{'marker':'test-only'}}).status_code
results['anonymous_metadata_persisted']=(sandbox/'data/api_runs/diagnostic/metadata_diagnostic.json').exists()
results['anonymous_governed_status']=client.get('/v1/semantic/proposals/not-real').status_code
results['k8s_health_status']=client.get('/health').status_code
results['docker_health_status']=client.get('/healthz').status_code
results['default_readiness']=client.get('/v1/ops/readiness').json()
results['middleware']=[m.cls.__name__ for m in api.app.user_middleware]
results['api_paths']=len(api.app.openapi()['paths'])
results['api_operations']=sum(len([m for m in p if m in {'get','post','put','patch','delete','options','head'}]) for p in api.app.openapi()['paths'].values())
async def persistence():
    r=RBACManager()
    user=await r.create_user('diagnostic-user')
    t=TenantManager()
    tenant=await t.create_tenant('Diagnostic',slug='diagnostic')
    duplicate=await t.create_tenant('Diagnostic 2',slug='diagnostic')
    return {'user_returned':bool(user.id),'user_readback':await r.get_user(user.id),'tenant_status':tenant.status.value,'tenant_readback':await t.get_tenant(tenant.id),'duplicate_slug_created':duplicate.id!=tenant.id}
results['legacy_persistence']=asyncio.run(persistence())
# Force the legitimate interleaving where both writers read the previous head before either append.
ledger=sandbox/'ledger.jsonl'
barrier=threading.Barrier(2)
def writer(n):
    store=DecisionProvenanceStore(ledger)
    original=store._entries
    def synchronized_read():
        entries=original(); barrier.wait(timeout=5); return entries
    store._entries=synchronized_read
    return store.record(agent_id='diagnostic',decision_type='probe',conclusion='test',rationale='test',decision_id=f'decision:{n}')
with ThreadPoolExecutor(max_workers=2) as pool:
    written=list(pool.map(writer,range(2)))
results['concurrent_ledger']={'writes_returned':len(written),'integrity':DecisionProvenanceStore(ledger).verify_integrity()}
ledger2=sandbox/'tampered.jsonl'
s=DecisionProvenanceStore(ledger2)
s.record(agent_id='diagnostic',decision_type='probe',conclusion='original',rationale='test',decision_id='decision:original')
payload=json.loads(ledger2.read_text()); payload['decision']['conclusion']='modified'; ledger2.write_text(json.dumps(payload)+'\n')
results['tampered_ledger']={'integrity':s.verify_integrity(),'read_conclusion':s.get('decision:original')['decision']['conclusion']}
print(json.dumps(results,ensure_ascii=False,indent=2))
