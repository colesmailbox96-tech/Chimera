from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
import json, os, time, hashlib, threading, hmac

PORT=int(os.environ.get("PORT","10000"))
CONTROL_TOKEN=os.environ.get("ICARUS_CONTROL_TOKEN","")
LOCK=threading.Lock()
STATE={
 "schema":"icarus.provider.scheduler.v1.1",
 "active_provider":"vercel_browser",
 "providers":{
  "vercel_browser":{"status":"unverified","score":100,"cost":"free","role":"browser inference + evidence API","workload_eligible":True},
  "render":{"status":"standby","score":85,"cost":"free","role":"control-plane standby","workload_eligible":False,
            "limits":{"memory_bytes":536870912,"monthly_free_instance_hours":750,"idle_spin_down_seconds":900}},
  "railway":{"status":"connector_unavailable","score":0,"cost":"unknown","role":"candidate"}
 },
 "policy":{
  "respect_provider_limits":True,
  "quota_evasion":False,
  "multi_account_rotation":False,
  "checkpoint_before_handoff":True,
  "handoff_utilization_threshold":0.80,
  "hard_stop_utilization_threshold":0.92
 },
 "checkpoint_seq":0,
 "last_checkpoint_hash":None,
 "handoff_pending":None,
 "last_handoff":None,
 "updated_at":time.time()
}

def digest(x):
 return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def eligible(name,p):
 return p.get("workload_eligible",False) and p.get("status") in ("healthy","standby") and p.get("score",0)>0

def recommended(exclude=None):
 choices=[]
 for name,p in STATE["providers"].items():
  if name==exclude: continue
  if eligible(name,p): choices.append((p.get("score",0),name))
 choices.sort(reverse=True)
 return choices[0][1] if choices else None

def evaluate_pressure(provider):
 p=STATE["providers"][provider]
 u=p.get("utilization")
 if not isinstance(u,(int,float)): return None
 if u>=STATE["policy"]["hard_stop_utilization_threshold"]: return "critical"
 if u>=STATE["policy"]["handoff_utilization_threshold"]: return "pre_handoff"
 return "normal"

def snapshot():
 with LOCK:
  x=json.loads(json.dumps(STATE))
  x["recommended_provider"]=recommended()
  x["state_hash"]=digest(x)
  return x

class Handler(BaseHTTPRequestHandler):
 def _json(self,code,obj):
  raw=json.dumps(obj,separators=(",",":")).encode()
  self.send_response(code); self.send_header("content-type","application/json")
  self.send_header("cache-control","no-store"); self.send_header("content-length",str(len(raw)))
  self.end_headers(); self.wfile.write(raw)

 def _body(self):
  n=min(int(self.headers.get("content-length","0") or 0),65536)
  try:return json.loads(self.rfile.read(n) or b"{}")
  except:return None

 def do_GET(self):
  path=urlparse(self.path).path
  if path in ("/","/health"):
   return self._json(200,{"ok":True,"service":"icarus-provider-scheduler","time":time.time(),"state":snapshot()})
  if path=="/status": return self._json(200,snapshot())
  return self._json(404,{"ok":False,"error":"not_found"})

 def do_POST(self):
  path=urlparse(self.path).path
  if not CONTROL_TOKEN: return self._json(503,{"ok":False,"error":"writes_disabled_unconfigured"})
  supplied=self.headers.get("Authorization","")
  if not hmac.compare_digest(supplied,"Bearer "+CONTROL_TOKEN):
   return self._json(401,{"ok":False,"error":"unauthorized"})
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
    if body.get("remaining") is not None:p["remaining"]=str(body["remaining"])[:120]
    if isinstance(body.get("memory_bytes"),(int,float)):p["memory_bytes"]=max(0,int(body["memory_bytes"]))
    if provider=="render" and "memory_bytes" in p:
     p["utilization"]=min(1,p["memory_bytes"]/p["limits"]["memory_bytes"])
    p["last_heartbeat"]=time.time()
    pressure=evaluate_pressure(provider)
    if provider==STATE["active_provider"] and pressure in ("pre_handoff","critical"):
     target=recommended(exclude=provider)
     STATE["handoff_pending"]={
      "from":provider,"to":target,"reason":"resource_pressure:"+pressure,
      "requires_checkpoint":True,"created_at":time.time()
     }
    STATE["updated_at"]=time.time()
   return self._json(200,{
    "ok":True,"pressure":evaluate_pressure(provider),
    "handoff_pending":STATE["handoff_pending"],
    "recommended_provider":recommended(),
    "state_hash":snapshot()["state_hash"]
   })

  if path=="/checkpoint":
   receipt=body.get("receipt")
   if not isinstance(receipt,dict):return self._json(400,{"ok":False,"error":"receipt_required"})
   if not isinstance(receipt.get("artifact_sha256"),str) or len(receipt["artifact_sha256"])!=64 or not receipt.get("durable_artifact_uri"):
    return self._json(400,{"ok":False,"error":"durable_checkpoint_artifact_required"})
   with LOCK:
    STATE["checkpoint_seq"]+=1
    payload={"seq":STATE["checkpoint_seq"],"previous":STATE["last_checkpoint_hash"],"receipt":receipt,"time":time.time()}
    h=digest(payload); STATE["last_checkpoint_hash"]=h; STATE["updated_at"]=time.time()
    pending=STATE["handoff_pending"]
    if pending and pending.get("requires_checkpoint") and pending.get("to"):
     old=STATE["active_provider"]; target=pending["to"]
     if eligible(target,STATE["providers"][target]):
      STATE["active_provider"]=target
      STATE["last_handoff"]={"from":old,"to":target,"reason":pending["reason"],"time":time.time(),"checkpoint_hash":h}
      STATE["handoff_pending"]=None
   return self._json(200,{
    "ok":True,"checkpoint_seq":payload["seq"],"checkpoint_hash":h,
    "previous":payload["previous"],"active_provider":STATE["active_provider"],
    "handoff":STATE["last_handoff"]
   })

  if path=="/handoff":
   target=str(body.get("target",""))[:40]; reason=str(body.get("reason",""))[:220]
   if target not in STATE["providers"]:return self._json(400,{"ok":False,"error":"unknown_target"})
   if not eligible(target,STATE["providers"][target]):return self._json(409,{"ok":False,"error":"target_not_eligible"})
   if STATE["policy"]["checkpoint_before_handoff"] and not STATE["last_checkpoint_hash"]:
    return self._json(409,{"ok":False,"error":"checkpoint_required"})
   with LOCK:
    old=STATE["active_provider"]; STATE["active_provider"]=target
    STATE["last_handoff"]={"from":old,"to":target,"reason":reason,"time":time.time(),"checkpoint_hash":STATE["last_checkpoint_hash"]}
    STATE["handoff_pending"]=None; STATE["updated_at"]=time.time()
   return self._json(200,{"ok":True,"handoff":STATE["last_handoff"]})

  return self._json(404,{"ok":False,"error":"not_found"})

 def log_message(self,fmt,*args):
  print(json.dumps({"event":"http","message":fmt%args,"time":time.time()}),flush=True)

if __name__=="__main__":
 print(json.dumps({"event":"boot","service":"icarus-provider-scheduler","port":PORT,"time":time.time()}),flush=True)
 HTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
