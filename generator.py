#!/usr/bin/env python3
import argparse
import concurrent.futures
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
MAX_CANDIDATES=50
MAX_ARTICLE_CHARS=5000

QUERIES=[
    '(Dublin OR Ireland) (housing OR homes OR apartments OR planning OR rent OR technology OR AI OR software OR jobs OR health OR transport)',
]

SECTIONS=[
    ("https://www.rte.ie/news/","rte.ie"),
    ("https://www.rte.ie/news/business/","rte.ie"),
    ("https://www.rte.ie/news/dublin/","rte.ie"),
    ("https://www.irishtimes.com/ireland/housing-planning/","irishtimes.com"),
    ("https://www.irishtimes.com/business/","irishtimes.com"),
    ("https://www.irishtimes.com/ireland/dublin/","irishtimes.com"),
    ("https://www.siliconrepublic.com/","siliconrepublic.com"),
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
    r=requests.get(GDELT,params=params,headers={"User-Agent":UA},timeout=12)
    r.raise_for_status()
    data=r.json()
    return data.get("articles",[]) if isinstance(data,dict) else []

def infer_published_from_url(url):
    try:
        p=urlsplit(url).path
        m=re.search(r"/(20\d\d)/(\d\d)/(\d\d)/",p)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00:00+00:00"
        m=re.search(r"/(20\d\d)/(\d{2})(\d{2})/",p)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00:00+00:00"
    except Exception:
        pass
    return None

def extract_article(url):
    try:
        r=requests.get(url,headers={"User-Agent":UA,"Accept-Language":"en-IE,en;q=0.9"},timeout=8,allow_redirects=True)
        if r.status_code!=200 or "text/html" not in r.headers.get("content-type",""):
            return None
        final=canonical_url(r.url)
        soup=BeautifulSoup(r.text,"html.parser")
        published=None
        for attrs in (
            {"property":"article:published_time"},{"name":"article:published_time"},
            {"name":"date"},{"itemprop":"datePublished"},
        ):
            m=soup.find("meta",attrs=attrs)
            if m and m.get("content"):
                published=m["content"].strip(); break
        if not published:
            for node in soup.find_all("script",attrs={"type":"application/ld+json"}):
                try:
                    data=json.loads(node.get_text() or "{}")
                    stack=data if isinstance(data,list) else [data]
                    for obj in stack:
                        if isinstance(obj,dict) and obj.get("datePublished"):
                            published=str(obj["datePublished"]); break
                    if published: break
                except Exception: pass
        if not published:
            published=infer_published_from_url(final)
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
        return {"url":final,"title":title[:400],"text":text,"published_at":published}
    except Exception:
        return None

def section_links():
    found={}
    article_patterns={
        "rte.ie": re.compile(r"^https://www\.rte\.ie/news/(?:[a-z-]+/)?20\d\d/\d{4}/\d+"),
        "irishtimes.com": re.compile(r"^https://www\.irishtimes\.com/.+/20\d\d/\d\d/\d\d/"),
        "siliconrepublic.com": re.compile(r"^https://www\.siliconrepublic\.com/[a-z0-9-]+/[a-z0-9-]+"),
    }
    for section,domain in SECTIONS:
        try:
            r=requests.get(section,headers={"User-Agent":UA,"Accept-Language":"en-IE,en;q=0.9"},timeout=10)
            if r.status_code!=200: continue
            soup=BeautifulSoup(r.text,"html.parser")
            for a in soup.find_all("a",href=True):
                href=a["href"].strip()
                if href.startswith("/"):
                    href="https://www."+domain+href
                href=canonical_url(href)
                if article_patterns[domain].search(href):
                    title=re.sub(r"\s+"," ",a.get_text(" ",strip=True))
                    if len(title)>=18:
                        found.setdefault(href,{"url":href,"gdelt_title":title,"seen_date":None,"domain":domain,"sourcecountry":"Ireland","score":score({"title":title,"domain":domain})})
        except Exception as e:
            print("SECTION_ERROR",section,repr(e),file=sys.stderr)
    return list(found.values())

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
    seen={canonical_url(x["url"]):x for x in section_links()}
    if len(seen)<35:
        try:
            arts=fetch_gdelt(QUERIES[0],"24h")
            for a in arts:
                u=a.get("url") or ""
                if not u.startswith(("http://","https://")): continue
                cu=canonical_url(u)
                seen.setdefault(cu,{"url":u,"gdelt_title":a.get("title",""),"seen_date":a.get("seendate"),
                    "domain":a.get("domain",""),"sourcecountry":a.get("sourcecountry",""),"score":score(a)})
        except Exception as e:
            print("GDELT_FALLBACK_ERROR",repr(e),file=sys.stderr)
    ordered=sorted(seen.values(),key=lambda x:(x["score"],x.get("seen_date") or ""),reverse=True)[:MAX_CANDIDATES]
    cutoff=datetime.now(DUBLIN)-timedelta(hours=50)
    out=[]
    def enrich(a):
        page=extract_article(a["url"])
        if not page: return None
        pub=page.get("published_at")
        if not pub:
            return None
        try:
            dt=datetime.fromisoformat(str(pub).replace("Z","+00:00"))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=DUBLIN)
            if dt.astimezone(DUBLIN)<cutoff: return None
        except Exception:
            return None
        page.update({k:a.get(k) for k in ("gdelt_title","seen_date","domain","sourcecountry","score")})
        return page
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures=[ex.submit(enrich,a) for a in ordered]
        for fut in concurrent.futures.as_completed(futures):
            try: page=fut.result()
            except Exception: page=None
            if page: out.append(page)
            if len(out)>=35:
                for other in futures: other.cancel()
                break
    out.sort(key=lambda x:(x.get("score",0),x.get("published_at") or x.get("seen_date") or ""),reverse=True)
    return out[:35]

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
