from presscheck.ai import VisionProvider
import httpx

def test_ai_missing_key(monkeypatch,tmp_path):
    monkeypatch.delenv('VISION_API_KEY',raising=False)
    monkeypatch.setenv('VISION_BASE_URL','https://api.openai.com/v1')
    assert not VisionProvider().interpret(tmp_path/'a',tmp_path/'b',[])['available']

def test_compatible_vision_contract(monkeypatch,tmp_path):
    monkeypatch.setenv('VISION_BASE_URL','http://127.0.0.1:1234/v1')
    a=tmp_path/'a.png';a.write_bytes(b'test')
    calls=[]
    def post(self,url,**kwargs):
        calls.append(kwargs['json'])
        return httpx.Response(200,json={'choices':[{'message':{'content':'V001 needs visual review.'}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.Client,'post',post)
    r=VisionProvider().interpret(a,a,[{'id':'V001'}])
    assert r['available'] and r['finding_ids']==['V001']
    assert r['label'].startswith('AI-generated')
    assert len(calls[0]['messages'][0]['content'])==3

def test_vision_failure_does_not_claim_success(monkeypatch,tmp_path):
    monkeypatch.setenv('VISION_BASE_URL','http://localhost:1234/v1')
    a=tmp_path/'a.png';a.write_bytes(b'test')
    def post(*args,**kwargs):raise httpx.ConnectError('offline')
    monkeypatch.setattr(httpx.Client,'post',post)
    assert not VisionProvider().interpret(a,a,[])['available']
