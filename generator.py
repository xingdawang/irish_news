#!/usr/bin/env python3
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

BASE=Path(__file__).resolve().parent
DB=Path(os.environ.get("IRELAND_NEWS_DB", BASE/"news.db"))
ENV_FILE=Path(os.environ.get("IRELAND_NEWS_GENERATOR_ENV", BASE/".generator.env"))
PUBLISH_URL=os.environ.get("IRELAND_NEWS_LOCAL_PUBLISH_URL","http://127.0.0.1:8200/api/news/publish")
TOKEN_FILE=Path(os.environ.get("IRELAND_NEWS_TOKEN_FILE",BASE/".publish_token")
)
GDELT="https://api.gdeltproject.org/api/v2/doc/doc"
DUBLIN=__import__("zoneinfo").ZoneInfo("Europe/Dublin")
UA="IrelandNewsGenerator/1.0 (+personal news digest)"
MAX_CANDIDATES=80
MAX_ARTICLE_CHARS=5000

QUERIES=[
    '(Dublin OR Ireland) (housing OR homes OR apartments OR planning OR rent OR rental OR "cost rental" OR development)',
    '(Dublin OR Ireland) (AI OR "artificial intelligence" OR software OR technology OR cybersecurity OR data OR cloud OR SaaS OR semiconductor OR jobs OR hiring)',
    '(Dublin OR Ireland) (transport OR health OR hospital OR crime OR court OR economy OR public services)',
]

def load_env():
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line=raw.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k,v=line.split("=",1)
            os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))

def canonical_url(url):
    p=urlsplit(url.strip())
    q=[x for x in p.query.split("&") if x and not x.lower().startswith("utm_")]
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/") or "/", "&".join(q),""))

def fetch_gdelt(query,timespan):
    params={"query":query,"mode":"artlist","format":"json","maxrecords":"250","sort":"datedesc","timespan":timespan}
    r=requests.get(GDELT,params=params,headers={"User-Agent":UA},timeout=30)
    r.raise_for_status()
    data=r.json()
    return data.get("articles",[]) if isinstance(data,dict) else []

def extract_article(url):
    try:
        r=requests.get(url,headers={"User-Agent":UA,"Accept-Language":"en-IE,en;q=0.9"},timeout=20,allow_redirects=True)
        if r.status_code!=200 or "text/html" not in r.headers.get("content-type",""):
            return None
        final=canonical_url(r.url)
        soup=BeautifulSoup(r.text,"html.parser")
        for tag in soup(["script","style","noscript","svg","nav","footer","header","form"]): tag.decompose()
        title=""
        og=soup.find("meta",attrs={"property":"og:title"})
        if og and og.get("content"): title=og["content"].strip()
        if not title and soup.title: title=soup.title.get_text(" ",strip=True)
        parts=[]
        for p in soup.find_all(["p","h2"]):
            t=re.sub(r"\s+"," ",p.get_text(" ",strip=True))
            if len(t)>=45: parts.append(t)
            if sum(map(len,parts))>=MAX_ARTICLE_CHARS: break
        text="\n".join(parts)[:MAX_ARTICLE_CHARS]
        if len(text)<180: return None
        return {"url":final,"title":title[:400],"text":text}
    except Exception:
        return None

def recent_events(days=14):
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    cutoff=int((datetime.now(timezone.utc)-timedelta(days=days)).timestamp())
    rows=con.execute("""select n.event_key,n.title,n.source_url,n.is_update,e.slug,e.sort_ts
        from news_items n join editions e on e.id=n.edition_id
        where e.sort_ts>=? order by e.sort_ts desc,n.id desc limit 400""",(cutoff,)).fetchall()
    con.close()
    return [dict(x) for x in rows]

def score(a):
    s=(a.get("title") or "").lower()+" "+(a.get("domain") or "").lower()
    n=0
    for k,w in [("dublin",8),("housing",7),("planning",6),("homes",5),("apartment",5),("rent",5),
                ("ai",6),("software",5),("technology",4),("cyber",5),("data",4),("jobs",4),
                ("fingal",5),("tallaght",5),("lucan",5),("swords",5),("adamstown",5),("clondalkin",5)]:
        if k in s:n+=w
    return n

def collect_candidates():
    seen={}
    for span in ("6h","24h","48h"):
        for q in QUERIES:
            try: arts=fetch_gdelt(q,span)
            except Exception as e:
                print("GDELT_ERROR",span,q,repr(e),file=sys.stderr); continue
            for a in arts:
                u=a.get("url") or ""
                if not u.startswith(("http://","https://")): continue
                cu=canonical_url(u)
                if cu not in seen:
                    seen[cu]={
                        "url":u,"gdelt_title":a.get("title",""),"seen_date":a.get("seendate"),
                        "domain":a.get("domain",""),"sourcecountry":a.get("sourcecountry",""),
                        "score":score(a),
                    }
        if len(seen)>=45: break
    ordered=sorted(seen.values(),key=lambda x:(x["score"],x.get("seen_date") or ""),reverse=True)[:MAX_CANDIDATES]
    out=[]
    for a in ordered:
        page=extract_article(a["url"])
        if not page: continue
        page.update({k:a.get(k) for k in ("gdelt_title","seen_date","domain","sourcecountry","score")})
        out.append(page)
        if len(out)>=45: break
        time.sleep(0.15)
    return out

def llm_select(slot,candidates,previous):
    base=os.environ.get("LLM_BASE_URL","").rstrip("/")
    key=os.environ.get("LLM_API_KEY","")
    model=os.environ.get("LLM_MODEL","")
    if not (base and key and model):
        raise RuntimeError("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must be configured")
    compact=[{"id":i,"url":x["url"],"title":x["title"] or x["gdelt_title"],"domain":x["domain"],
              "seen_date":x["seen_date"],"text":x["text"][:3000]} for i,x in enumerate(candidates)]
    prior=[{"event_key":x["event_key"],"title":x["title"],"source_url":x["source_url"],"slug":x["slug"]} for x in previous]
    system="""You are the editor of a private Chinese Ireland news digest. Select only genuinely new, high-value Ireland stories from the supplied verified article pages. Never invent facts or URLs. Strictly deduplicate against previous events. If the same real-world event has a material new fact/decision/data/stage, reuse the previous event_key, set is_update=true and explain update_note. Otherwise create a short stable lowercase ASCII event_key. Prioritize Greater Dublin (target 60-70%), housing/planning (25-35%), and IT/AI/software/data/cyber/jobs (20-30%), but never pad with weak or old stories. Sports max 1. Political/public-policy stories must be neutral factual summaries. Output JSON only."""
    user={"slot":slot.isoformat(),"requirements":{"target_items":"8-18, fewer if insufficient","categories":["housing","tech","other"]},
          "previous_events":prior,"candidates":compact,
          "output_schema":{"items":[{"candidate_id":0,"event_key":"...","category":"housing|tech|other","region":"...",
              "title":"Chinese headline","summary":"1-2 Chinese sentences","source_name":"publisher","published_at":"ISO8601 or null",
              "is_update":False,"update_note":None}]}}
    payload={"model":model,"messages":[{"role":"system","content":system},{"role":"user","content":json.dumps(user,ensure_ascii=False)}],
             "temperature":0.1,"response_format":{"type":"json_object"}}
    r=requests.post(base+"/chat/completions",headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},
                    json=payload,timeout=120)
    r.raise_for_status()
    content=r.json()["choices"][0]["message"]["content"]
    data=json.loads(content)
    return data.get("items",[])

def build_payload(slot, selected, candidates):
    now=datetime.now(DUBLIN)
    items=[]
    used=set()
    for x in selected:
        try: cid=int(x["candidate_id"]); c=candidates[cid]
        except Exception: continue
        url=c["url"]
        if url in used: continue
        used.add(url)
        category=x.get("category")
        if category not in {"housing","tech","other"}: continue
        event_key=re.sub(r"[^a-z0-9-]+","-",str(x.get("event_key","")).lower()).strip("-")[:180]
        if not event_key: continue
        is_update=bool(x.get("is_update",False))
        item={"event_key":event_key,"category":category,"region":str(x.get("region") or "Ireland")[:160],
              "title":str(x.get("title") or "")[:300],"summary":str(x.get("summary") or "")[:2500],
              "source_name":str(x.get("source_name") or c.get("domain") or "Source")[:160],
              "source_url":url,"published_at":x.get("published_at"),"is_update":is_update}
        if is_update:
            note=str(x.get("update_note") or "").strip()
            if not note: continue
            item["update_note"]=note[:500]
        if not item["title"] or not item["summary"]: continue
        items.append(item)
    slug=slot.strftime("%Y-%m-%d-%H%M")
    return {"version":1,"slug":slug,
        "title":f"🇮🇪 爱尔兰新闻简报｜{slot.strftime('%Y-%m-%d %H:00')}｜住房 & IT 优先",
        "scheduled_at":slot.isoformat(),"generated_at":now.isoformat(),"cutoff_at":now.isoformat(),"items":items}

def publish(payload):
    token=TOKEN_FILE.read_text(encoding="utf-8").strip()
    r=requests.post(PUBLISH_URL,headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},json=payload,timeout=60)
    if r.status_code!=200:
        raise RuntimeError(f"publish HTTP {r.status_code}: {r.text[:1000]}")
    data=r.json()
    if not data.get("ok"): raise RuntimeError("publish did not return ok")
    return data

def slot_for_now(now):
    hour=max(h for h in (0,6,12,18) if h<=now.hour)
    return now.replace(hour=hour,minute=0,second=0,microsecond=0)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--slot")
    args=ap.parse_args()
    load_env()
    now=datetime.now(DUBLIN)
    slot=datetime.fromisoformat(args.slot) if args.slot else slot_for_now(now)
    if slot.tzinfo is None: slot=slot.replace(tzinfo=DUBLIN)
    candidates=collect_candidates()
    print(json.dumps({"stage":"candidates","count":len(candidates),"top":[{"title":x["title"],"url":x["url"]} for x in candidates[:8]]},ensure_ascii=False))
    if args.dry_run: return
    selected=llm_select(slot,candidates,recent_events())
    payload=build_payload(slot,selected,candidates)
    if not payload["items"]: raise RuntimeError("LLM selected no valid items")
    result=publish(payload)
    print(json.dumps({"stage":"published","result":result},ensure_ascii=False))

if __name__=="__main__":
    main()
