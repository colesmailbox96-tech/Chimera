#!/usr/bin/env python3
import json, sys, time, urllib.request

VERCEL="https://blank-autonomy-core-oct25-icarus-17c1.vercel.app"

def get_json(url, timeout=12):
    req=urllib.request.Request(url,headers={"User-Agent":"Icarus-Watchdog/1.1"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return r.status,json.loads(r.read().decode())

def get_text(url, timeout=12):
    req=urllib.request.Request(url,headers={"User-Agent":"Icarus-Watchdog/1.1"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return r.status,r.read().decode(errors="replace")

def check():
    out={"schema":"icarus.watchdog.result.v1.1","checked_at":time.time(),"checks":{},"healthy":False}
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

if __name__=="__main__":
    result=check()
    with open("watchdog-result.json","w",encoding="utf-8") as f:
        json.dump(result,f,indent=2,sort_keys=True)
    print(json.dumps(result,indent=2,sort_keys=True))
    sys.exit(0 if result["healthy"] else 1)
