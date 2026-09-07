import json,sys,os,tempfile
sys.path.insert(0,'/private/tmp/aof-diagnosis-20260905/main')
os.environ['AOF_ROOT']=tempfile.mkdtemp(prefix='aof-missing-routes-')
from fastapi.testclient import TestClient
from services.semantic_middle_layer_api.app import app
c=TestClient(app,raise_server_exceptions=False)
result=[]
for method,path,body in [('GET','/v1/harness/sessions',None),('POST','/v1/training-data/generate',{'dataset_name':'diagnostic'}),('GET','/v1/graph/health',None)]:
 r=c.request(method,path,json=body)
 result.append({'method':method,'path':path,'status':r.status_code,'body':r.text[:500]})
print(json.dumps(result,indent=2,ensure_ascii=False))
