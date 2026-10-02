import json, os, re, tempfile
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory

BASE_DIR=Path(__file__).resolve().parent
DATA_DIR=Path(os.environ.get("NEWS_DATA_DIR", BASE_DIR/"data"))
EDITIONS_DIR=DATA_DIR/"editions"; LATEST_FILE=DATA_DIR/"latest.json"; INDEX_FILE=DATA_DIR/"index.json"
TOKEN=os.environ.get("IRELAND_NEWS_PUBLISH_TOKEN","")
SLUG_RE=re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}$")
app=Flask(__name__, static_folder="static"); app.config["MAX_CONTENT_LENGTH"]=int(os.environ.get("MAX_BODY_BYTES","1048576"))

def atomic_json(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=path.name+".",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(payload,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def ensure_data():
    EDITIONS_DIR.mkdir(parents=True,exist_ok=True)
    if not INDEX_FILE.exists(): atomic_json(INDEX_FILE,{"editions":[]})

def load_json(path,default=None):
    if not path.exists(): return default
    with path.open("r",encoding="utf-8") as f: return json.load(f)

def validate(p):
    if not isinstance(p,dict): return "JSON body must be an object"
    e=p.get("edition"); items=p.get("items")
    if not isinstance(e,dict): return "edition is required"
    if not SLUG_RE.match(e.get("slug","")): return "edition.slug must use YYYY-MM-DD-HHMM"
    if not isinstance(e.get("title"),str) or not e["title"].strip(): return "edition.title is required"
    if not isinstance(items,list): return "items must be an array"
    if len(items)>100: return "items exceeds maximum of 100"
    for i,x in enumerate(items):
        if not isinstance(x,dict): return f"items[{i}] must be an object"
        for field in ("event_key","category","title","summary","source_name","source_url"):
            if not isinstance(x.get(field),str) or not x[field].strip(): return f"items[{i}].{field} is required"
        if x["category"] not in {"housing","tech","other"}: return f"items[{i}].category must be housing, tech or other"
        if not x["source_url"].startswith(("https://","http://")): return f"items[{i}].source_url must be http(s)"
    return None

@app.get("/health")
def health():
    ensure_data(); latest=load_json(LATEST_FILE,{})
    return jsonify({"ok":True,"service":"irish-news","updated_at":latest.get("updated_at")})

@app.post("/api/news/publish")
def publish():
    ensure_data()
    auth=request.headers.get("Authorization","")
    if not TOKEN or auth!="Bearer "+TOKEN: return jsonify({"ok":False,"error":"unauthorized"}),401
    p=request.get_json(silent=True); error=validate(p)
    if error: return jsonify({"ok":False,"error":error}),400
    e=p["edition"]; slug=e["slug"]; now=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    record={"version":p.get("version",1),"edition":e,"items":p["items"],"item_count":len(p["items"]),"updated_at":now}
    atomic_json(EDITIONS_DIR/f"{slug}.json",record); atomic_json(LATEST_FILE,record)
    index=load_json(INDEX_FILE,{"editions":[]})
    entries=[x for x in index.get("editions",[]) if x.get("slug")!=slug]
    entries.append({"slug":slug,"title":e["title"],"scheduled_at":e.get("scheduled_at"),"item_count":len(p["items"]),"updated_at":now})
    entries.sort(key=lambda x:x["slug"],reverse=True); atomic_json(INDEX_FILE,{"editions":entries[:500]})
    return jsonify({"ok":True,"edition":slug,"item_count":len(p["items"]),"updated_at":now})

@app.get("/api/news/latest")
def latest():
    ensure_data(); d=load_json(LATEST_FILE)
    return jsonify(d) if d else (jsonify({"ok":False,"error":"no editions published"}),404)

@app.get("/api/news/editions")
def editions():
    ensure_data(); return jsonify(load_json(INDEX_FILE,{"editions":[]}))

@app.get("/api/news/editions/<slug>")
def edition(slug):
    if not SLUG_RE.match(slug): return jsonify({"ok":False,"error":"invalid slug"}),400
    d=load_json(EDITIONS_DIR/f"{slug}.json")
    return jsonify(d) if d else (jsonify({"ok":False,"error":"not found"}),404)

@app.get("/")
def home(): return send_from_directory(app.static_folder,"index.html")

if __name__=="__main__":
    ensure_data(); app.run(host="0.0.0.0",port=int(os.environ.get("PORT","5310")))
