from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
import json, os, time, hashlib, threading

PORT=int(os.environ.get("PORT","10000"))
LOCK=threading.Lock()
STATE={
 "schema":"icarus.provider.scheduler.v1",
 "active_provider":"vercel_browser",
 "providers":{
  "vercel_browser":{"status":"healthy","score":100,"cost":"free","role":"browser inference + evidence API"},
  "render":{"status":"standby","score":85,"cost":"free","role":"control-plane standby"},
  "railway":{"status":"connector_unavailable","score":0,"cost":"unknown","role":"candidate"}
 },
 "policy":{
  "respect_provider_limits":True,
  "quota_evasion":False,
  "multi_account_rotation":False,
  "checkpoint_before_handoff":True,
  "handoff_utilization_threshold":0.80
 },
 "checkpoint_seq":0,
 "last_checkpoint_hash":None,
 "last_handoff":None,
 "updated_at":time.time()
}

def digest(x):
 return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def recommended():
 eligible=[(p.get("score",0),n) for n,p in STATE["providers"].items() if p.get("status") in ("healthy","standby")]
 eligible.sort(reverse=True)
 return eligible[0][1] if eligible else None

def snapshot():
 with LOCK:
  x=json.loads(json.dumps(STATE))
  x["recommended_provider"]=recommended()
  x["state_hash"]=digest(x)
  return x

class Handler(BaseHTTPRequestHandler):
 def _json(self,code,obj):
  raw=json.dumps(obj,separators=(",",":")).encode()
  self.send_response(code)
  self.send_header("content-type","application/json")
  self.send_header("cache-control","no-store")
  self.send_header("content-length",str(len(raw)))
  self.end_headers()
  self.wfile.write(raw)

 def _body(self):
  n=min(int(self.headers.get("content-length","0") or 0),65536)
  try:return json.loads(self.rfile.read(n) or b"{}")
  except:return None

 def do_GET(self):
  path=urlparse(self.path).path
  if path in ("/","/health"):
   return self._json(200,{"ok":True,"service":"icarus-provider-scheduler","time":time.time(),"state":snapshot()})
  if path=="/status":
   return self._json(200,snapshot())
  return self._json(404,{"ok":False,"error":"not_found"})

 def do_POST(self):
  path=urlparse(self.path).path
  body=self._body()
  if body is None:return self._json(400,{"ok":False,"error":"bad_json"})
  if path=="/heartbeat":
   provider=str(body.get("provider",""))[:40]
   if provider not in STATE["providers"]:return self._json(400,{"ok":False,"error":"unknown_provider"})
   with LOCK:
    p=STATE["providers"][provider]
    if body.get("status") is not None:p["status"]=str(body["status"])[:40]
    if isinstance(body.get("score"),(int,float)):p["score"]=max(0,min(100,float(body["score"])))
    if isinstance(body.get("utilization"),(int,float)):p["utilization"]=max(0,min(1,float(body["utilization"])))
    if body.get("remaining") is not None:p["remaining"]=str(body["remaining"])[:100]
    p["last_heartbeat"]=time.time()
    STATE["updated_at"]=time.time()
   return self._json(200,{"ok":True,"recommended_provider":recommended(),"state_hash":snapshot()["state_hash"]})
  if path=="/checkpoint":
   receipt=body.get("receipt")
   if not isinstance(receipt,dict):return self._json(400,{"ok":False,"error":"receipt_required"})
   with LOCK:
    STATE["checkpoint_seq"]+=1
    payload={"seq":STATE["checkpoint_seq"],"previous":STATE["last_checkpoint_hash"],"receipt":receipt,"time":time.time()}
    h=digest(payload)
    STATE["last_checkpoint_hash"]=h
    STATE["updated_at"]=time.time()
   return self._json(200,{"ok":True,"checkpoint_seq":payload["seq"],"checkpoint_hash":h,"previous":payload["previous"]})
  if path=="/handoff":
   target=str(body.get("target",""))[:40]
   reason=str(body.get("reason",""))[:220]
   if target not in STATE["providers"]:return self._json(400,{"ok":False,"error":"unknown_target"})
   if STATE["providers"][target].get("status") not in ("healthy","standby"):
    return self._json(409,{"ok":False,"error":"target_not_eligible"})
   with LOCK:
    old=STATE["active_provider"]
    STATE["active_provider"]=target
    STATE["last_handoff"]={"from":old,"to":target,"reason":reason,"time":time.time(),"checkpoint_hash":STATE["last_checkpoint_hash"]}
    STATE["updated_at"]=time.time()
   return self._json(200,{"ok":True,"handoff":STATE["last_handoff"]})
  return self._json(404,{"ok":False,"error":"not_found"})

 def log_message(self,fmt,*args):
  print(json.dumps({"event":"http","message":fmt%args,"time":time.time()}),flush=True)

if __name__=="__main__":
 print(json.dumps({"event":"boot","service":"icarus-provider-scheduler","port":PORT,"time":time.time()}),flush=True)
 HTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
