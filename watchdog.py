#!/usr/bin/env python3
import json, sys, time, urllib.request, urllib.error

VERCEL="https://blank-autonomy-core-oct25-icarus-17c1.vercel.app"
RENDER="https://icarus-provider-scheduler.onrender.com"

def get_json(url, timeout=12):
    req=urllib.request.Request(url,headers={"User-Agent":"Icarus-Watchdog/1.0"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return r.status,json.loads(r.read().decode())

def get_text(url, timeout=12):
    req=urllib.request.Request(url,headers={"User-Agent":"Icarus-Watchdog/1.0"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return r.status,r.read().decode(errors="replace")

def post_json(url, payload, timeout=20):
    raw=json.dumps(payload).encode()
    req=urllib.request.Request(url,data=raw,method="POST",headers={"content-type":"application/json","User-Agent":"Icarus-Watchdog/1.0"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return r.status,json.loads(r.read().decode())

def check():
    out={"schema":"icarus.watchdog.result.v1","checked_at":time.time(),"checks":{},"healthy":False}
    try:
        s,html=get_text(VERCEL+"/icarus-continuous.html")
        out["checks"]["runtime"]={"status":s,"ok":s==200 and "Continuous Learned Policy" in html}
    except Exception as e:
        out["checks"]["runtime"]={"ok":False,"error":str(e)}
    try:
        s,p=get_json(VERCEL+"/icarus-policy.json")
        joint=float(p.get("validation",{}).get("joint_accuracy",0))
        out["checks"]["policy"]={"status":s,"ok":s==200 and joint>=0.60,"joint_accuracy":joint,"schema":p.get("schema")}
    except Exception as e:
        out["checks"]["policy"]={"ok":False,"error":str(e)}
    try:
        s,o=get_json(VERCEL+"/api/icarus-opportunities")
        out["checks"]["evidence"]={"status":s,"ok":s==200 and o.get("qualified") is True and len(o.get("results",[]))>=2,"results":len(o.get("results",[]))}
    except Exception as e:
        out["checks"]["evidence"]={"ok":False,"error":str(e)}
    out["healthy"]=all(v.get("ok") for v in out["checks"].values())
    return out

def wake_render():
    last=None
    for _ in range(8):
        try:
            s,j=get_json(RENDER+"/health",timeout=20)
            if s==200 and j.get("ok"): return True,j
        except Exception as e:
            last=str(e)
        time.sleep(10)
    return False,{"error":last or "render wake timeout"}

def record_failure(result):
    awake,health=wake_render()
    if not awake:
        return {"render_awake":False,"render_error":health}
    status={
        "provider":"vercel_browser",
        "status":"unavailable",
        "score":0,
        "remaining":"watchdog detected failed production health checks"
    }
    hb_status,hb=post_json(RENDER+"/heartbeat",status)
    cp_status,cp=post_json(RENDER+"/checkpoint",{
        "receipt":{
            "schema":"icarus.watchdog.failure.v1",
            "source":"github_actions",
            "checked_at":result["checked_at"],
            "checks":result["checks"],
            "reason":"production health gate failed"
        }
    })
    return {"render_awake":True,"heartbeat_status":hb_status,"heartbeat":hb,"checkpoint_status":cp_status,"checkpoint":cp}

if __name__=="__main__":
    result=check()
    print(json.dumps(result,indent=2,sort_keys=True))
    if result["healthy"]:
        sys.exit(0)
    escalation=record_failure(result)
    print(json.dumps({"escalation":escalation},indent=2,sort_keys=True))
    sys.exit(1)
