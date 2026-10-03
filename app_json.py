from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from urllib.parse import urlparse
from pathlib import Path
import html
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from json_store import JsonNewsStore, canonical_url

BASE=Path(__file__).resolve().parent
DATA_ROOT=Path(os.environ.get("IRELAND_NEWS_JSON_DIR",BASE/"json_data"))
TOKEN_FILE=Path(os.environ.get("IRELAND_NEWS_TOKEN_FILE",BASE/".publish_token"))
DUBLIN=ZoneInfo("Europe/Dublin")
STALE_HOURS=int(os.environ.get("IRELAND_NEWS_STALE_HOURS","8"))
MAX_BODY_BYTES=int(os.environ.get("IRELAND_NEWS_MAX_BODY_BYTES","1048576"))
SLUG_RE=re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}$")
store=JsonNewsStore(DATA_ROOT)
app=FastAPI(title="Ireland News",docs_url=None,redoc_url=None)

def esc(v): return html.escape(str(v or ""))

def parse_dt(value):
    if not value: return None
    try:
        dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=DUBLIN)
        return dt.astimezone(DUBLIN)
    except Exception: return None

def parse_required_dt(value,field):
    if not isinstance(value,str) or not value.strip(): raise ValueError(f"{field} is required")
    try: dt=datetime.fromisoformat(value.strip().replace("Z","+00:00"))
    except Exception as exc: raise ValueError(f"{field} must be ISO-8601") from exc
    if dt.tzinfo is None: raise ValueError(f"{field} must include a timezone offset")
    return dt

def clean_text(value,field,max_len,required=True):
    if value is None and not required: return None
    if not isinstance(value,str): raise ValueError(f"{field} must be a string")
    value=value.strip()
    if required and not value: raise ValueError(f"{field} is required")
    if len(value)>max_len: raise ValueError(f"{field} exceeds {max_len} characters")
    return value

def get_publish_token():
    env=os.environ.get("IRELAND_NEWS_PUBLISH_TOKEN","").strip()
    if env: return env
    try: return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError: return ""

def require_publish_auth(request):
    expected=get_publish_token()
    if not expected or len(expected)<32: raise HTTPException(status_code=503,detail="publish token is not configured")
    auth=request.headers.get("Authorization","")
    if not auth.startswith("Bearer ") or not secrets.compare_digest(auth[7:].strip(),expected):
        raise HTTPException(status_code=401,detail="unauthorized")

def normalize_payload(raw):
    if not isinstance(raw,dict): raise ValueError("JSON body must be an object")
    allowed_top={"version","slug","title","scheduled_at","generated_at","cutoff_at","items"}
    extra=sorted(set(raw)-allowed_top)
    if extra: raise ValueError("unknown top-level fields: "+", ".join(extra))
    if raw.get("version",1)!=1: raise ValueError("version must be 1")
    scheduled=parse_required_dt(raw.get("scheduled_at"),"scheduled_at")
    generated=parse_required_dt(raw.get("generated_at"),"generated_at")
    cutoff=parse_required_dt(raw.get("cutoff_at"),"cutoff_at") if raw.get("cutoff_at") else generated
    now=datetime.now(timezone.utc)
    if generated.astimezone(timezone.utc)>now+timedelta(minutes=10): raise ValueError("generated_at is too far in the future")
    if scheduled.astimezone(timezone.utc)>now+timedelta(minutes=30): raise ValueError("scheduled_at is too far in the future")
    if generated<scheduled-timedelta(minutes=15): raise ValueError("generated_at cannot be materially earlier than scheduled_at")
    if cutoff>generated+timedelta(minutes=10): raise ValueError("cutoff_at cannot be materially later than generated_at")
    slot=scheduled.astimezone(DUBLIN)
    if slot.minute!=0 or slot.second!=0 or slot.microsecond!=0 or slot.hour not in {0,6,12,18}:
        raise ValueError("scheduled_at must be a Dublin 00:00/06:00/12:00/18:00 slot")
    slug=clean_text(raw.get("slug"),"slug",32)
    expected_slug=slot.strftime("%Y-%m-%d-%H%M")
    if not SLUG_RE.fullmatch(slug) or slug!=expected_slug: raise ValueError(f"slug must match scheduled Dublin slot: {expected_slug}")
    title=clean_text(raw.get("title"),"title",240)
    expected_title=f"🇮🇪 爱尔兰新闻简报｜{slot.strftime('%Y-%m-%d %H:00')}｜住房 & IT 优先"
    if title!=expected_title: raise ValueError("title does not match the required edition title")
    items=raw.get("items")
    if not isinstance(items,list): raise ValueError("items must be an array")
    if len(items)>50: raise ValueError("items exceeds maximum of 50")
    clean_items=[]; seen_events=set(); seen_urls=set()
    for i,item in enumerate(items):
        if not isinstance(item,dict): raise ValueError(f"items[{i}] must be an object")
        allowed={"event_key","category","region","title","summary","source_name","source_url","published_at","is_update","update_note"}
        unknown=sorted(set(item)-allowed)
        if unknown: raise ValueError(f"items[{i}] unknown fields: "+", ".join(unknown))
        event_key=clean_text(item.get("event_key"),f"items[{i}].event_key",180)
        category=clean_text(item.get("category"),f"items[{i}].category",20)
        if category not in {"housing","tech","other"}: raise ValueError(f"items[{i}].category must be housing, tech or other")
        source_url=clean_text(item.get("source_url"),f"items[{i}].source_url",2000)
        canon=canonical_url(source_url)
        if not canon: raise ValueError(f"items[{i}].source_url must be an absolute http(s) URL")
        is_update=item.get("is_update",False)
        if not isinstance(is_update,bool): raise ValueError(f"items[{i}].is_update must be boolean")
        update_note=clean_text(item.get("update_note"),f"items[{i}].update_note",500,required=False)
        if is_update and not update_note: raise ValueError(f"items[{i}].update_note is required when is_update is true")
        if event_key in seen_events: raise ValueError(f"duplicate event_key in edition: {event_key}")
        if canon in seen_urls: raise ValueError(f"duplicate source_url in edition: {source_url}")
        seen_events.add(event_key); seen_urls.add(canon)
        published=item.get("published_at")
        if published: parse_required_dt(published,f"items[{i}].published_at")
        out={
            "event_key":event_key,"category":category,
            "region":clean_text(item.get("region","Ireland"),f"items[{i}].region",160),
            "title":clean_text(item.get("title"),f"items[{i}].title",300),
            "summary":clean_text(item.get("summary"),f"items[{i}].summary",2500),
            "source_name":clean_text(item.get("source_name"),f"items[{i}].source_name",160),
            "source_url":source_url,"published_at":published,"is_update":is_update,
        }
        if update_note: out["update_note"]=update_note
        clean_items.append(out)
    return {
        "version":1,"slug":slug,"title":title,"scheduled_at":scheduled.isoformat(),
        "generated_at":generated.isoformat(),"cutoff_at":cutoff.isoformat(),
        "sort_ts":int(scheduled.timestamp()),"items":clean_items,
    }

CSS="""
:root{--bg:#f4f6f8;--card:#fff;--text:#13202f;--muted:#667085;--line:#e7eaf0;--accent:#0b6bcb;--green:#0f8a5f;--warn:#b54708;--warnbg:#fff7ed}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans CJK SC",Arial,sans-serif}
a{color:inherit}.wrap{max-width:1100px;margin:auto;padding:16px}.top{position:sticky;top:0;z-index:10;background:rgba(244,246,248,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.topin{max-width:1100px;margin:auto;padding:10px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px}.brand{font-weight:800;font-size:20px;text-decoration:none}.stamp{font-size:12px;color:var(--muted)}
.mainnav{max-width:1100px;margin:auto;padding:0 16px 10px;display:flex;gap:8px;overflow:auto}.navlink{white-space:nowrap;text-decoration:none;font-size:13px;padding:7px 11px;border-radius:999px;color:#344054}.navlink.active{background:#13202f;color:#fff}
.hero{background:linear-gradient(135deg,#0b6bcb,#0f8a5f);color:#fff;border-radius:22px;padding:22px;margin:8px 0 16px}.hero h1{margin:0 0 8px;font-size:28px}.hero p{margin:0;opacity:.9;line-height:1.55}
.statusbar{display:flex;justify-content:space-between;gap:10px;align-items:center;background:#fff;border:1px solid var(--line);border-radius:14px;padding:11px 13px;margin:0 0 14px;font-size:13px;color:#475467}.statusbar.stale{background:var(--warnbg);border-color:#fed7aa;color:var(--warn)}.status-dot{width:8px;height:8px;border-radius:50%;background:#12b76a;display:inline-block;margin-right:7px}.stale .status-dot{background:#f79009}
.filters{display:flex;gap:8px;overflow:auto;padding:2px 0 14px}.pill{white-space:nowrap;text-decoration:none;color:var(--text);background:#fff;border:1px solid var(--line);padding:9px 13px;border-radius:999px;font-size:14px}.pill.active{background:#13202f;color:#fff;border-color:#13202f}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px;display:flex;flex-direction:column;min-height:220px;box-shadow:0 5px 18px rgba(16,24,40,.04)}
.meta{font-size:12px;color:var(--muted);display:flex;gap:7px;flex-wrap:wrap}.tag{font-weight:700;color:var(--accent)}.update{color:#b54708;font-weight:700}.card h2{font-size:18px;line-height:1.4;margin:10px 0}.card p{font-size:14px;line-height:1.65;color:#344054;margin:0 0 14px}.source{margin-top:auto;display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;color:var(--muted)}
.source a{color:var(--accent);text-decoration:none;font-weight:700}.empty{padding:40px;background:#fff;border-radius:18px;text-align:center;color:var(--muted)}
.footer{text-align:center;color:var(--muted);font-size:12px;padding:28px 0}.edition{margin:0 0 14px;color:var(--muted);font-size:13px}
.home-actions{display:flex;gap:10px;margin:16px 0}.action{flex:1;background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 14px;text-decoration:none;text-align:center;font-size:14px;font-weight:700;color:#344054}.action.disabled{font-weight:500;color:#98a2b3}
.archive-toolbar{display:flex;gap:8px;overflow:auto;margin-bottom:8px}.archive-day{margin:22px 0 10px;font-size:20px}.archive-list{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.archive-item{display:flex;justify-content:space-between;align-items:center;gap:14px;background:#fff;border:1px solid var(--line);border-radius:16px;padding:15px;text-decoration:none;position:relative}.archive-item:active{transform:scale(.995)}.archive-title{font-weight:750;line-height:1.4}.archive-meta{font-size:12px;color:var(--muted);margin-top:5px}.archive-right{display:flex;align-items:center;gap:9px}.archive-arrow{font-size:18px;color:var(--accent)}.unread-dot{width:9px;height:9px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 3px #e9f2ff;display:inline-block}.unread-text{color:var(--accent);font-weight:700}
.pager{display:grid;grid-template-columns:1fr auto 1fr;gap:8px;align-items:center;margin:0 0 14px}.pager a,.pager span{background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 12px;text-decoration:none;font-size:13px}.pager .right{text-align:right}.pager .center{text-align:center;color:var(--muted)}
@media(max-width:820px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){.wrap{padding:12px}.grid{grid-template-columns:1fr}.archive-list{grid-template-columns:1fr}.hero{border-radius:18px;padding:18px}.hero h1{font-size:23px}.card{min-height:0}.brand{font-size:18px}.stamp{display:none}.topin{padding-bottom:8px}.mainnav{padding-bottom:9px}.pager{grid-template-columns:1fr 1fr}.pager .center{grid-column:1/-1;grid-row:1}.pager .left{grid-column:1}.pager .right{grid-column:2}.statusbar{align-items:flex-start;flex-direction:column}.home-actions{flex-direction:column}}
"""
READ_JS="""<script>(function(){const KEY='irelandNewsReadEditionsV1';function getRead(){try{return new Set(JSON.parse(localStorage.getItem(KEY)||'[]'))}catch(e){return new Set()}}function save(s){try{localStorage.setItem(KEY,JSON.stringify(Array.from(s).slice(-500)))}catch(e){}}window.IrelandNewsRead={mark:function(slug){if(!slug)return;const s=getRead();s.add(slug);save(s)},refreshArchive:function(){const s=getRead();document.querySelectorAll('.archive-item[data-slug]').forEach(el=>{const read=s.has(el.dataset.slug),dot=el.querySelector('.unread-dot'),txt=el.querySelector('.unread-label');if(dot)dot.style.display=read?'none':'inline-block';if(txt){txt.textContent=read?'已读':'未读';txt.classList.toggle('unread-text',!read)}})}};document.addEventListener('DOMContentLoaded',()=>window.IrelandNewsRead.refreshArchive());})();</script>"""

@app.middleware("http")
async def no_cache(request,call_next):
    response=await call_next(request)
    if request.url.path=="/" or request.url.path.startswith(("/archive","/edition/","/api/")):
        response.headers["Cache-Control"]="no-store"
    return response

def label(cat): return {"housing":"🏠 住房","tech":"💻 科技","other":"📍 综合"}.get(cat,"📍 综合")

def edition_dt(ed): return parse_dt(ed.get("scheduled_at") or ed.get("generated_at"))
def edition_date(ed):
    dt=edition_dt(ed); return dt.strftime("%Y-%m-%d") if dt else str(ed.get("generated_at") or "")[:10]
def edition_time(ed):
    dt=edition_dt(ed); return dt.strftime("%H:%M") if dt else str(ed.get("generated_at") or "")[11:16]

def status_html(latest):
    dt=parse_dt(latest.get("published_at") or latest.get("generated_at"))
    if not dt: return ""
    age=max(0,(datetime.now(DUBLIN)-dt).total_seconds()/3600); stale=age>STALE_HOURS
    cls="statusbar stale" if stale else "statusbar"; state=f"⚠️ 已超过 {int(age)} 小时未更新" if stale else "更新正常"
    return f'<div class="{cls}"><div><span class="status-dot"></span>最新发布：{esc(dt.strftime("%Y-%m-%d %H:%M"))} · {latest.get("item_count",0)} 条</div><div>{esc(state)}</div></div>'

def layout(content,subtitle="住房 & IT 优先 · Greater Dublin",active="latest",extra_script=""):
    now=datetime.now(DUBLIN).strftime("%Y-%m-%d %H:%M")
    lc=" active" if active=="latest" else ""; ac=" active" if active=="archive" else ""
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0b6bcb"><title>Ireland News</title><style>{CSS}</style></head><body><div class="top"><div class="topin"><a class="brand" href="/">🇮🇪 Ireland News</a><div class="stamp">Dublin · {esc(now)}</div></div><div class="mainnav"><a class="navlink{lc}" href="/">最新一期</a><a class="navlink{ac}" href="/archive">历史简报</a></div></div><main class="wrap"><section class="hero"><h1>爱尔兰新闻简报</h1><p>{esc(subtitle)}<br>住房、规划、IT、AI、就业与都柏林本地重要动态。</p></section>{content}<div class="footer">AI 整理摘要仅供快速阅读 · 重要信息请以原文为准</div></main>{READ_JS}{extra_script}</body></html>'''

def cards(rows):
    if not rows: return '<div class="empty">这一期暂时没有新闻。</div>'
    out=['<div class="grid">']
    for r in rows:
        u=r.get("source_url") or ""; host=urlparse(u).netloc.replace("www.","") if u else ""
        link=f'<a href="{esc(u)}" target="_blank" rel="noopener">查看原文 ↗</a>' if u else ""
        update='<span class="update">新进展</span>' if r.get("is_update") else ""
        out.append(f'<article class="card"><div class="meta"><span class="tag">{label(r.get("category"))}</span>{update}<span>{esc(r.get("region"))}</span><span>{esc(r.get("published_at"))}</span></div><h2>{esc(r.get("title"))}</h2><p>{esc(r.get("summary"))}</p><div class="source"><span>{esc(r.get("source_name") or host)}</span>{link}</div></article>')
    out.append("</div>"); return "".join(out)

def archive_keep(ed,window):
    if window=="all": return True
    dt=edition_dt(ed)
    if not dt: return True
    delta=(datetime.now(DUBLIN).date()-dt.date()).days
    return {"today":delta==0,"3d":0<=delta<3,"7d":0<=delta<7}.get(window,True)

@app.get("/health")
def health():
    latest=store.latest(); count=len(store.list_editions())
    published=latest.get("published_at") if latest else None; dt=parse_dt(published)
    age=max(0,(datetime.now(DUBLIN)-dt).total_seconds()/3600) if dt else None
    return {"ok":True,"storage":"json","editions":count,"latest_slug":latest.get("slug") if latest else None,"latest_published_at":published,"stale":age is not None and age>STALE_HOURS,"age_hours":round(age,2) if age is not None else None}

@app.get("/",response_class=HTMLResponse)
def home(category:str|None=None):
    latest=store.latest()
    if not latest: return layout('<div class="empty">暂时还没有新闻。</div>')
    metas=store.list_editions(); previous=store.get(metas[1]["slug"]) if len(metas)>1 else None
    rows=list(latest.get("items",[]))
    if category in {"housing","tech","other"}: rows=[x for x in rows if x.get("category")==category]
    active=category or "all"
    nav='<div class="filters">'+''.join([f'<a class="pill {"active" if active==k else ""}" href="/'+(f'?category={k}' if k!="all" else '')+f'">{v}</a>' for k,v in [("all","全部"),("housing","🏠 住房"),("tech","💻 IT / AI"),("other","📍 其他")]])+'</div>'
    info=f'<div class="edition">最新一期：{esc(latest.get("title"))} · {esc(edition_date(latest))} {esc(edition_time(latest))} · {latest.get("item_count",0)} 条</div>'
    prev=f'<a class="action" href="/edition/{esc(previous.get("slug"))}">← 上一期</a>' if previous else '<span class="action disabled">← 暂无上一期</span>'
    actions=f'<div class="home-actions">{prev}<a class="action" href="/archive">查看全部历史</a></div>'
    mark=f'<script>document.addEventListener("DOMContentLoaded",()=>window.IrelandNewsRead.mark({latest.get("slug")!r}));</script>'
    return layout(status_html(latest)+nav+info+cards(rows)+actions,active="latest",extra_script=mark)

@app.get("/archive",response_class=HTMLResponse)
def archive(window:str="all"):
    if window not in {"today","3d","7d","all"}: window="all"
    editions=[x for x in store.list_editions() if archive_keep(x,window)]
    labels=[("today","今天"),("3d","最近 3 天"),("7d","最近 7 天"),("all","全部")]
    toolbar='<div class="archive-toolbar">'+''.join(f'<a class="pill {"active" if window==k else ""}" href="/archive?window={k}">{v}</a>' for k,v in labels)+'</div>'
    if not editions: return layout(toolbar+'<div class="empty">这个时间范围内还没有简报。</div>',"按日期回看每一期新闻",active="archive")
    out=[toolbar]; current_day=None
    for ed in editions:
        day=edition_date(ed) or "未知日期"
        if day!=current_day:
            if current_day is not None: out.append("</div>")
            out.append(f'<h2 class="archive-day">{esc(day)}</h2><div class="archive-list">'); current_day=day
        out.append(f'<a class="archive-item" data-slug="{esc(ed.get("slug"))}" href="/edition/{esc(ed.get("slug"))}"><div><div class="archive-title">{esc(edition_time(ed) or "本期")} · {esc(ed.get("title"))}</div><div class="archive-meta">{ed.get("item_count",0)} 条新闻 · <span class="unread-label unread-text">未读</span></div></div><div class="archive-right"><span class="unread-dot"></span><span class="archive-arrow">›</span></div></a>')
    out.append("</div>")
    return layout("".join(out),"按日期回看每一期新闻 · 未读会显示蓝点",active="archive")

@app.get("/edition/{slug}",response_class=HTMLResponse)
def edition(slug:str):
    ed=store.get(slug)
    if not ed: raise HTTPException(404)
    metas=store.list_editions(); pos=next((i for i,x in enumerate(metas) if x.get("slug")==slug),None)
    newer=metas[pos-1] if pos is not None and pos>0 else None
    older=metas[pos+1] if pos is not None and pos+1<len(metas) else None
    left=f'<a class="left" href="/edition/{esc(older.get("slug"))}">← 上一期</a>' if older else '<span class="left">← 已是最早</span>'
    right=f'<a class="right" href="/edition/{esc(newer.get("slug"))}">下一期 →</a>' if newer else '<span class="right">已是最新 →</span>'
    pager=f'<div class="pager">{left}<a class="center" href="/archive">历史简报</a>{right}</div>'
    info=f'<div class="edition">{esc(ed.get("title"))} · {esc(edition_date(ed))} {esc(edition_time(ed))} · {ed.get("item_count",0)} 条</div>'
    mark=f'<script>document.addEventListener("DOMContentLoaded",()=>window.IrelandNewsRead.mark({slug!r}));</script>'
    return layout(pager+info+cards(ed.get("items",[])),esc(ed.get("title")),active="archive",extra_script=mark)

@app.get("/api/latest")
def api_latest():
    ed=store.latest()
    return {"edition":{k:v for k,v in ed.items() if k!="items"} if ed else None,"items":ed.get("items",[]) if ed else []}

@app.get("/api/editions")
def api_editions(): return {"editions":store.list_editions(200)}

@app.get("/api/editions/{slug}")
def api_edition(slug:str):
    ed=store.get(slug)
    if not ed: raise HTTPException(404)
    return ed

@app.get("/api/events/recent")
def api_recent_events(days:int=14):
    days=min(max(days,1),60)
    cutoff=int((datetime.now(timezone.utc)-timedelta(days=days)).timestamp())
    return {"days":days,"events":store.recent_events(cutoff,500)}

@app.post("/api/news/publish")
async def api_publish(request:Request):
    require_publish_auth(request)
    length=request.headers.get("Content-Length")
    if length:
        try:
            if int(length)>MAX_BODY_BYTES: raise HTTPException(status_code=413,detail="request body too large")
        except ValueError: raise HTTPException(status_code=400,detail="invalid Content-Length")
    body=await request.body()
    if len(body)>MAX_BODY_BYTES: raise HTTPException(status_code=413,detail="request body too large")
    try:
        raw=json.loads(body.decode("utf-8")); payload=normalize_payload(raw)
    except (UnicodeDecodeError,json.JSONDecodeError): raise HTTPException(status_code=400,detail="invalid JSON")
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc))
    try: return store.publish(payload)
    except ValueError as exc: raise HTTPException(status_code=409,detail=str(exc))
