from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode
from pathlib import Path
import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get("IRELAND_NEWS_DB", BASE / "news.db"))
TOKEN_FILE = Path(os.environ.get("IRELAND_NEWS_TOKEN_FILE", BASE / ".publish_token"))
PREFIX = ""
DUBLIN = ZoneInfo("Europe/Dublin")
STALE_HOURS = int(os.environ.get("IRELAND_NEWS_STALE_HOURS", "8"))
MAX_BODY_BYTES = int(os.environ.get("IRELAND_NEWS_MAX_BODY_BYTES", "1048576"))
SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}$")
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
app = FastAPI(title="Ireland News", docs_url=None, redoc_url=None)


def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def esc(v):
    return html.escape(str(v or ""))


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DUBLIN)
        return dt.astimezone(DUBLIN)
    except Exception:
        return None


def parse_required_dt(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if dt.tzinfo is None:
        raise ValueError(f"{field} must include a timezone offset")
    return dt


def epoch_for(value):
    dt = parse_dt(value)
    return int(dt.timestamp()) if dt else 0


def canonical_url(value):
    if not value:
        return ""
    try:
        p = urlsplit(str(value).strip())
        if p.scheme.lower() not in {"http", "https"} or not p.netloc:
            return ""
        query = [
            (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
        ]
        path = p.path.rstrip("/") or "/"
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(sorted(query)), ""))
    except Exception:
        return ""


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def table_columns(con, table):
    return {row["name"] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = db()
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS editions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          slug TEXT NOT NULL UNIQUE,
          scheduled_at TEXT,
          generated_at TEXT NOT NULL,
          title TEXT NOT NULL,
          item_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS news_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          edition_id INTEGER NOT NULL,
          event_key TEXT NOT NULL,
          category TEXT NOT NULL,
          region TEXT,
          title TEXT NOT NULL,
          summary TEXT NOT NULL,
          source_name TEXT,
          source_url TEXT,
          published_at TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(edition_id) REFERENCES editions(id),
          UNIQUE(edition_id, event_key)
        );
        """)
        ec = table_columns(con, "editions")
        for name, spec in {
            "published_at": "TEXT",
            "cutoff_at": "TEXT",
            "sort_ts": "INTEGER NOT NULL DEFAULT 0",
            "payload_hash": "TEXT",
        }.items():
            if name not in ec:
                con.execute(f"ALTER TABLE editions ADD COLUMN {name} {spec}")
        nc = table_columns(con, "news_items")
        for name, spec in {
            "is_update": "INTEGER NOT NULL DEFAULT 0",
            "update_note": "TEXT",
            "canonical_url": "TEXT",
        }.items():
            if name not in nc:
                con.execute(f"ALTER TABLE news_items ADD COLUMN {name} {spec}")

        editions = con.execute("SELECT id,scheduled_at,generated_at,published_at,sort_ts FROM editions").fetchall()
        for ed in editions:
            published = ed["published_at"] or ed["generated_at"]
            sort_ts = ed["sort_ts"] or epoch_for(ed["scheduled_at"] or ed["generated_at"])
            con.execute("UPDATE editions SET published_at=?,sort_ts=? WHERE id=?", (published, sort_ts, ed["id"]))

        items = con.execute("SELECT id,source_url,canonical_url FROM news_items").fetchall()
        for item in items:
            if not item["canonical_url"]:
                con.execute("UPDATE news_items SET canonical_url=? WHERE id=?", (canonical_url(item["source_url"]), item["id"]))

        con.executescript("""
        CREATE INDEX IF NOT EXISTS idx_items_created ON news_items(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_items_category ON news_items(category);
        CREATE INDEX IF NOT EXISTS idx_items_region ON news_items(region);
        CREATE INDEX IF NOT EXISTS idx_items_event_key ON news_items(event_key);
        CREATE INDEX IF NOT EXISTS idx_items_canonical_url ON news_items(canonical_url);
        CREATE INDEX IF NOT EXISTS idx_editions_generated ON editions(generated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_editions_sort ON editions(sort_ts DESC,id DESC);
        """)
        con.commit()
    finally:
        con.close()


init_db()

CSS = """
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

READ_JS = """
<script>
(function(){
  const KEY='irelandNewsReadEditionsV1';
  function getRead(){try{return new Set(JSON.parse(localStorage.getItem(KEY)||'[]'))}catch(e){return new Set()}}
  function save(s){try{localStorage.setItem(KEY,JSON.stringify(Array.from(s).slice(-500)))}catch(e){}}
  window.IrelandNewsRead={
    mark:function(slug){if(!slug)return;const s=getRead();s.add(slug);save(s)},
    refreshArchive:function(){
      const s=getRead();
      document.querySelectorAll('.archive-item[data-slug]').forEach(el=>{
        const read=s.has(el.dataset.slug),dot=el.querySelector('.unread-dot'),txt=el.querySelector('.unread-label');
        if(dot)dot.style.display=read?'none':'inline-block';
        if(txt){txt.textContent=read?'已读':'未读';txt.classList.toggle('unread-text',!read)}
      });
    }
  };
  document.addEventListener('DOMContentLoaded',()=>window.IrelandNewsRead.refreshArchive());
})();
</script>
"""


@app.middleware("http")
async def no_cache(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith(("/archive", "/edition/", "/api/")):
        response.headers["Cache-Control"] = "no-store"
    return response


def label(cat):
    return {"housing": "🏠 住房", "tech": "💻 科技", "other": "📍 综合"}.get(cat, "📍 综合")


def edition_dt(ed):
    return parse_dt(ed["scheduled_at"] or ed["generated_at"])


def edition_date(ed):
    dt = edition_dt(ed)
    return dt.strftime("%Y-%m-%d") if dt else (ed["generated_at"] or "")[:10]


def edition_time(ed):
    dt = edition_dt(ed)
    return dt.strftime("%H:%M") if dt else ((ed["generated_at"] or "")[11:16])


def status_html(latest):
    dt = parse_dt(latest["published_at"] or latest["generated_at"])
    if not dt:
        return ""
    now = datetime.now(DUBLIN)
    age = max(0, (now - dt).total_seconds() / 3600)
    stale = age > STALE_HOURS
    cls = "statusbar stale" if stale else "statusbar"
    state = f"⚠️ 已超过 {int(age)} 小时未更新" if stale else "更新正常"
    return f'<div class="{cls}"><div><span class="status-dot"></span>最新发布：{esc(dt.strftime("%Y-%m-%d %H:%M"))} · {latest["item_count"]} 条</div><div>{esc(state)}</div></div>'


def layout(content, subtitle="住房 & IT 优先 · Greater Dublin", active="latest", extra_script=""):
    now = datetime.now(DUBLIN).strftime("%Y-%m-%d %H:%M")
    latest_cls = " active" if active == "latest" else ""
    archive_cls = " active" if active == "archive" else ""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0b6bcb"><title>Ireland News</title><style>{CSS}</style></head>
<body><div class="top"><div class="topin"><a class="brand" href="{PREFIX}/">🇮🇪 Ireland News</a><div class="stamp">Dublin · {esc(now)}</div></div>
<div class="mainnav"><a class="navlink{latest_cls}" href="{PREFIX}/">最新一期</a><a class="navlink{archive_cls}" href="{PREFIX}/archive">历史简报</a></div></div>
<main class="wrap"><section class="hero"><h1>爱尔兰新闻简报</h1><p>{esc(subtitle)}<br>住房、规划、IT、AI、就业与都柏林本地重要动态。</p></section>{content}<div class="footer">AI 整理摘要仅供快速阅读 · 重要信息请以原文为准</div></main>{READ_JS}{extra_script}</body></html>"""


def cards(rows):
    if not rows:
        return '<div class="empty">这一期暂时没有新闻。</div>'
    out = ['<div class="grid">']
    for r in rows:
        u = r["source_url"] or ""
        host = urlparse(u).netloc.replace("www.", "") if u else ""
        link = f'<a href="{esc(u)}" target="_blank" rel="noopener">查看原文 ↗</a>' if u else ""
        update = '<span class="update">新进展</span>' if r["is_update"] else ""
        out.append(f"""<article class="card"><div class="meta"><span class="tag">{label(r["category"])}</span>{update}<span>{esc(r["region"])}</span><span>{esc(r["published_at"])}</span></div>
<h2>{esc(r["title"])}</h2><p>{esc(r["summary"])}</p><div class="source"><span>{esc(r["source_name"] or host)}</span>{link}</div></article>""")
    out.append("</div>")
    return "".join(out)


def archive_keep(ed, window):
    if window == "all":
        return True
    dt = edition_dt(ed)
    if not dt:
        return True
    delta = (datetime.now(DUBLIN).date() - dt.date()).days
    return {"today": delta == 0, "3d": 0 <= delta < 3, "7d": 0 <= delta < 7}.get(window, True)


def get_publish_token():
    env = os.environ.get("IRELAND_NEWS_PUBLISH_TOKEN", "").strip()
    if env:
        return env
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def require_publish_auth(request):
    expected = get_publish_token()
    if not expected:
        raise HTTPException(status_code=503, detail="publish token is not configured")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="publish token is too short")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not secrets.compare_digest(auth[7:].strip(), expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def clean_text(value, field, max_len, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required")
    if len(value) > max_len:
        raise ValueError(f"{field} exceeds {max_len} characters")
    return value


def normalize_payload(raw):
    if not isinstance(raw, dict):
        raise ValueError("JSON body must be an object")
    allowed_top = {"version", "slug", "title", "scheduled_at", "generated_at", "cutoff_at", "items"}
    unknown = sorted(set(raw) - allowed_top)
    if unknown:
        raise ValueError("unknown top-level fields: " + ", ".join(unknown))
    if raw.get("version", 1) != 1:
        raise ValueError("version must be 1")

    scheduled = parse_required_dt(raw.get("scheduled_at"), "scheduled_at")
    generated = parse_required_dt(raw.get("generated_at"), "generated_at")
    cutoff_raw = raw.get("cutoff_at")
    cutoff = parse_required_dt(cutoff_raw, "cutoff_at") if cutoff_raw else generated
    now = datetime.now(timezone.utc)
    if generated.astimezone(timezone.utc) > now + timedelta(minutes=10):
        raise ValueError("generated_at is too far in the future")
    if scheduled.astimezone(timezone.utc) > now + timedelta(minutes=30):
        raise ValueError("scheduled_at is too far in the future")
    if generated < scheduled - timedelta(minutes=15):
        raise ValueError("generated_at cannot be materially earlier than scheduled_at")
    if cutoff > generated + timedelta(minutes=10):
        raise ValueError("cutoff_at cannot be materially later than generated_at")

    local_slot = scheduled.astimezone(DUBLIN)
    if local_slot.minute != 0 or local_slot.second != 0 or local_slot.microsecond != 0 or local_slot.hour not in {0, 6, 12, 18}:
        raise ValueError("scheduled_at must be a Dublin 00:00/06:00/12:00/18:00 slot")
    slug = clean_text(raw.get("slug"), "slug", 32)
    expected_slug = local_slot.strftime("%Y-%m-%d-%H%M")
    if not SLUG_RE.fullmatch(slug) or slug != expected_slug:
        raise ValueError(f"slug must match scheduled Dublin slot: {expected_slug}")
    title = clean_text(raw.get("title"), "title", 240)
    expected_title = f"🇮🇪 爱尔兰新闻简报｜{local_slot.strftime('%Y-%m-%d %H:00')}｜住房 & IT 优先"
    if title != expected_title:
        raise ValueError("title does not match the required edition title")

    items = raw.get("items")
    if not isinstance(items, list):
        raise ValueError("items must be an array")
    if len(items) > 50:
        raise ValueError("items exceeds maximum of 50")

    clean_items, seen_events, seen_urls = [], set(), set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"items[{i}] must be an object")
        allowed = {"event_key", "category", "region", "title", "summary", "source_name", "source_url", "published_at", "is_update", "update_note"}
        extra = sorted(set(item) - allowed)
        if extra:
            raise ValueError(f"items[{i}] unknown fields: " + ", ".join(extra))
        event_key = clean_text(item.get("event_key"), f"items[{i}].event_key", 180)
        category = clean_text(item.get("category"), f"items[{i}].category", 20)
        if category not in {"housing", "tech", "other"}:
            raise ValueError(f"items[{i}].category must be housing, tech or other")
        source_url = clean_text(item.get("source_url"), f"items[{i}].source_url", 2000)
        canon = canonical_url(source_url)
        if not canon:
            raise ValueError(f"items[{i}].source_url must be an absolute http(s) URL")
        is_update = item.get("is_update", False)
        if not isinstance(is_update, bool):
            raise ValueError(f"items[{i}].is_update must be boolean")
        update_note = clean_text(item.get("update_note"), f"items[{i}].update_note", 500, required=False)
        if is_update and not update_note:
            raise ValueError(f"items[{i}].update_note is required when is_update is true")
        if event_key in seen_events:
            raise ValueError(f"duplicate event_key in edition: {event_key}")
        if canon in seen_urls:
            raise ValueError(f"duplicate source_url in edition: {source_url}")
        seen_events.add(event_key)
        seen_urls.add(canon)
        published = item.get("published_at")
        if published:
            parse_required_dt(published, f"items[{i}].published_at")
        clean_items.append({
            "event_key": event_key,
            "category": category,
            "region": clean_text(item.get("region", "Ireland"), f"items[{i}].region", 160),
            "title": clean_text(item.get("title"), f"items[{i}].title", 300),
            "summary": clean_text(item.get("summary"), f"items[{i}].summary", 2500),
            "source_name": clean_text(item.get("source_name"), f"items[{i}].source_name", 160),
            "source_url": source_url,
            "canonical_url": canon,
            "published_at": published,
            "is_update": is_update,
            "update_note": update_note,
        })

    return {
        "version": 1,
        "slug": slug,
        "title": title,
        "scheduled_at": scheduled.isoformat(),
        "generated_at": generated.isoformat(),
        "cutoff_at": cutoff.isoformat(),
        "sort_ts": int(scheduled.timestamp()),
        "items": clean_items,
    }


def payload_hash(payload):
    body = {k: v for k, v in payload.items() if k != "sort_ts"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@app.get("/health")
def health():
    con = db()
    try:
        count = con.execute("SELECT COUNT(*) FROM editions").fetchone()[0]
        latest = con.execute("SELECT * FROM editions ORDER BY sort_ts DESC,id DESC LIMIT 1").fetchone()
    finally:
        con.close()
    published = latest["published_at"] if latest else None
    dt = parse_dt(published)
    age = max(0, (datetime.now(DUBLIN) - dt).total_seconds() / 3600) if dt else None
    return {
        "ok": True,
        "editions": count,
        "latest_slug": latest["slug"] if latest else None,
        "latest_published_at": published,
        "stale": age is not None and age > STALE_HOURS,
        "age_hours": round(age, 2) if age is not None else None,
    }


@app.get(PREFIX + "/", response_class=HTMLResponse)
def home(category: str | None = None):
    con = db()
    latest = con.execute("SELECT * FROM editions ORDER BY sort_ts DESC,id DESC LIMIT 1").fetchone()
    if not latest:
        con.close()
        return layout('<div class="empty">暂时还没有新闻。第一期内容正在准备中。</div>')
    previous = con.execute("SELECT * FROM editions WHERE id<>? ORDER BY sort_ts DESC,id DESC LIMIT 1", (latest["id"],)).fetchone()
    q, args = "SELECT * FROM news_items WHERE edition_id=?", [latest["id"]]
    if category in {"housing", "tech", "other"}:
        q += " AND category=?"
        args.append(category)
    rows = con.execute(q + " ORDER BY id", args).fetchall()
    con.close()
    active = category or "all"
    nav = '<div class="filters">' + "".join([
        f'<a class="pill {"active" if active==k else ""}" href="{PREFIX}/' + (f'?category={k}' if k != "all" else '') + f'">{v}</a>'
        for k, v in [("all", "全部"), ("housing", "🏠 住房"), ("tech", "💻 IT / AI"), ("other", "📍 其他")]
    ]) + "</div>"
    info = f'<div class="edition">最新一期：{esc(latest["title"])} · {esc(edition_date(latest))} {esc(edition_time(latest))} · {latest["item_count"]} 条</div>'
    prev = f'<a class="action" href="{PREFIX}/edition/{esc(previous["slug"])}">← 上一期</a>' if previous else '<span class="action disabled">← 暂无上一期</span>'
    actions = f'<div class="home-actions">{prev}<a class="action" href="{PREFIX}/archive">查看全部历史</a></div>'
    mark = f'<script>document.addEventListener("DOMContentLoaded",()=>window.IrelandNewsRead.mark({latest["slug"]!r}));</script>'
    return layout(status_html(latest) + nav + info + cards(rows) + actions, active="latest", extra_script=mark)


@app.get(PREFIX + "/archive", response_class=HTMLResponse)
def archive(window: str = "all"):
    if window not in {"today", "3d", "7d", "all"}:
        window = "all"
    con = db()
    editions = con.execute("SELECT * FROM editions ORDER BY sort_ts DESC,id DESC").fetchall()
    con.close()
    editions = [ed for ed in editions if archive_keep(ed, window)]
    labels = [("today", "今天"), ("3d", "最近 3 天"), ("7d", "最近 7 天"), ("all", "全部")]
    toolbar = '<div class="archive-toolbar">' + "".join(f'<a class="pill {"active" if window==k else ""}" href="{PREFIX}/archive?window={k}">{v}</a>' for k, v in labels) + "</div>"
    if not editions:
        return layout(toolbar + '<div class="empty">这个时间范围内还没有简报。</div>', "按日期回看每一期新闻", active="archive")
    out, current_day = [toolbar], None
    for ed in editions:
        day = edition_date(ed) or "未知日期"
        if day != current_day:
            if current_day is not None:
                out.append("</div>")
            out.append(f'<h2 class="archive-day">{esc(day)}</h2><div class="archive-list">')
            current_day = day
        out.append(f'''<a class="archive-item" data-slug="{esc(ed["slug"])}" href="{PREFIX}/edition/{esc(ed["slug"])}"><div><div class="archive-title">{esc(edition_time(ed) or "本期")} · {esc(ed["title"])}</div><div class="archive-meta">{ed["item_count"]} 条新闻 · <span class="unread-label unread-text">未读</span></div></div><div class="archive-right"><span class="unread-dot"></span><span class="archive-arrow">›</span></div></a>''')
    if current_day is not None:
        out.append("</div>")
    return layout("".join(out), "按日期回看每一期新闻 · 未读会显示蓝点", active="archive")


@app.get(PREFIX + "/edition/{slug}", response_class=HTMLResponse)
def edition(slug: str):
    con = db()
    ed = con.execute("SELECT * FROM editions WHERE slug=?", (slug,)).fetchone()
    if not ed:
        con.close()
        raise HTTPException(404)
    rows = con.execute("SELECT * FROM news_items WHERE edition_id=? ORDER BY id", (ed["id"],)).fetchall()
    older = con.execute("SELECT * FROM editions WHERE (sort_ts < ? OR (sort_ts=? AND id<?)) ORDER BY sort_ts DESC,id DESC LIMIT 1", (ed["sort_ts"], ed["sort_ts"], ed["id"])).fetchone()
    newer = con.execute("SELECT * FROM editions WHERE (sort_ts > ? OR (sort_ts=? AND id>?)) ORDER BY sort_ts ASC,id ASC LIMIT 1", (ed["sort_ts"], ed["sort_ts"], ed["id"])).fetchone()
    con.close()
    left = f'<a class="left" href="{PREFIX}/edition/{esc(older["slug"])}">← 上一期</a>' if older else '<span class="left">← 已是最早</span>'
    right = f'<a class="right" href="{PREFIX}/edition/{esc(newer["slug"])}">下一期 →</a>' if newer else '<span class="right">已是最新 →</span>'
    pager = f'<div class="pager">{left}<a class="center" href="{PREFIX}/archive">历史简报</a>{right}</div>'
    info = f'<div class="edition">{esc(ed["title"])} · {esc(edition_date(ed))} {esc(edition_time(ed))} · {ed["item_count"]} 条</div>'
    mark = f'<script>document.addEventListener("DOMContentLoaded",()=>window.IrelandNewsRead.mark({ed["slug"]!r}));</script>'
    return layout(pager + info + cards(rows), esc(ed["title"]), active="archive", extra_script=mark)


@app.get(PREFIX + "/api/latest")
def api_latest():
    con = db()
    ed = con.execute("SELECT * FROM editions ORDER BY sort_ts DESC,id DESC LIMIT 1").fetchone()
    if not ed:
        con.close()
        return JSONResponse({"edition": None, "items": []})
    rows = con.execute("SELECT * FROM news_items WHERE edition_id=? ORDER BY id", (ed["id"],)).fetchall()
    con.close()
    return {"edition": dict(ed), "items": [dict(r) for r in rows]}


@app.get(PREFIX + "/api/editions")
def api_editions():
    con = db()
    editions = con.execute("SELECT * FROM editions ORDER BY sort_ts DESC,id DESC LIMIT 200").fetchall()
    con.close()
    return {"editions": [dict(x) for x in editions]}


@app.get(PREFIX + "/api/events/recent")
def api_recent_events(days: int = 14):
    days = min(max(days, 1), 60)
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    con = db()
    rows = con.execute("""
        SELECT n.event_key,n.title,n.source_name,n.source_url,n.is_update,e.slug,e.scheduled_at
        FROM news_items n JOIN editions e ON e.id=n.edition_id
        WHERE e.sort_ts>=?
        ORDER BY e.sort_ts DESC,n.id DESC LIMIT 500
    """, (cutoff,)).fetchall()
    con.close()
    return {"days": days, "events": [dict(r) for r in rows]}


@app.post(PREFIX + "/api/news/publish")
async def api_publish(request: Request):
    require_publish_auth(request)
    length = request.headers.get("Content-Length")
    if length:
        try:
            if int(length) > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="request body too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid Content-Length")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request body too large")
    try:
        raw = json.loads(body.decode("utf-8"))
        payload = normalize_payload(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="invalid JSON")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    digest = payload_hash(payload)
    con = db()
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute("SELECT id,payload_hash,item_count FROM editions WHERE slug=?", (payload["slug"],)).fetchone()
        if existing:
            if existing["payload_hash"] and secrets.compare_digest(existing["payload_hash"], digest):
                con.rollback()
                return {"ok": True, "status": "exists", "slug": payload["slug"], "items": existing["item_count"]}
            con.rollback()
            raise HTTPException(status_code=409, detail="slug already exists with different content")

        for item in payload["items"]:
            prior_event = con.execute("SELECT n.id,e.slug,e.sort_ts FROM news_items n JOIN editions e ON e.id=n.edition_id WHERE n.event_key=? ORDER BY e.sort_ts DESC,n.id DESC LIMIT 1", (item["event_key"],)).fetchone()
            prior_event_older = con.execute("SELECT n.id,e.slug,e.sort_ts FROM news_items n JOIN editions e ON e.id=n.edition_id WHERE n.event_key=? AND e.sort_ts<? ORDER BY e.sort_ts DESC,n.id DESC LIMIT 1", (item["event_key"], payload["sort_ts"])).fetchone()
            prior_url = con.execute("SELECT n.id,e.slug FROM news_items n JOIN editions e ON e.id=n.edition_id WHERE n.canonical_url=? ORDER BY e.sort_ts DESC,n.id DESC LIMIT 1", (item["canonical_url"],)).fetchone()
            if item["is_update"]:
                if not prior_event_older:
                    con.rollback()
                    raise HTTPException(status_code=409, detail=f"is_update requires the same event_key in an older edition: {item['event_key']}")
            elif prior_event or prior_url:
                duplicate = item["event_key"] if prior_event else item["source_url"]
                con.rollback()
                raise HTTPException(status_code=409, detail=f"cross-edition duplicate requires is_update=true: {duplicate}")

        published = utc_now_iso()
        cur = con.execute("""INSERT INTO editions
            (slug,scheduled_at,generated_at,title,item_count,published_at,cutoff_at,sort_ts,payload_hash)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (payload["slug"], payload["scheduled_at"], payload["generated_at"], payload["title"], len(payload["items"]),
             published, payload["cutoff_at"], payload["sort_ts"], digest))
        eid = cur.lastrowid
        for item in payload["items"]:
            con.execute("""INSERT INTO news_items
                (edition_id,event_key,category,region,title,summary,source_name,source_url,published_at,created_at,is_update,update_note,canonical_url)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (eid, item["event_key"], item["category"], item["region"], item["title"], item["summary"],
                 item["source_name"], item["source_url"], item["published_at"], published, int(item["is_update"]),
                 item["update_note"], item["canonical_url"]))
        con.commit()
        return {"ok": True, "status": "created", "slug": payload["slug"], "items": len(payload["items"]), "published_at": published}
    except HTTPException:
        raise
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise HTTPException(status_code=409, detail=f"database integrity error: {exc}")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
