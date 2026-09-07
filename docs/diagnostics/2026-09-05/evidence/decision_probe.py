import os,sys,json,tempfile
sys.path.insert(0,'/private/tmp/aof-diagnosis-20260905/main')
os.environ['AOF_ROOT']=tempfile.mkdtemp(prefix='aof-decisions-probe-')
from fastapi.testclient import TestClient
from services.semantic_middle_layer_api.app import app, _decision_store
c=TestClient(app)
created=c.post('/v1/decisions',json={'decision_id':'decision:diagnostic','agent_id':'publisher:impersonated','tenant_id':'tenant-a','decision_type':'diagnostic','conclusion':'synthetic-only','rationale':'diagnostic'})
read=c.get('/v1/decisions/decision:diagnostic')
search=c.post('/v1/decisions/precedents/search',json={'decision_type':'diagnostic'})
print(json.dumps({'anonymous_create':created.status_code,'accepted_agent':created.json()['decision']['agent_id'],'accepted_tenant':created.json()['decision']['tenant_id'],'anonymous_read':read.status_code,'anonymous_search':search.status_code,'search_matches':len(search.json()['results']),'shared_ledger':str(_decision_store().path)},indent=2))
