# app/api/docs_page.py
"""
GET /docs — the live documentation page itself (single-file HTML app).

Access model (mirrors /api/docs):
  - DocsConfig ON / SCHEDULED-in-window  → page renders (public).
  - DocsConfig OFF / out-of-window       → "Not Available" page, UNLESS the
    visitor carries a valid ADMIN JWT (?token=... or previously stored in
    localStorage) — admins can always preview.
  - The "Not Available" page shows the schedule window when one is set.

The page fetches /api/docs/content, /team, /live-stats and attaches the admin
Bearer token (when present) so the JSON endpoints admit the admin preview.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.models.user import User, UserRole
from app.models.docs_config import DocsConfig, DocsVisibility

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Docs Page"])

MSG_LIVE = "Documentation is live."
MSG_OFF = "Documentation is currently not available."
MSG_SCHEDULED_LATER = "Documentation opens on {start}."
MSG_SCHEDULED_OVER = "Documentation window closed on {end}."


def _render_not_available(cfg: DocsConfig) -> HTMLResponse:
    """Static 'Not Available' page; includes the schedule window when set."""
    now = datetime.now(timezone.utc)
    vis = cfg.visibility.value if hasattr(cfg.visibility, "value") else str(cfg.visibility)
    start = cfg.schedule_start
    end = cfg.schedule_end

    if vis == "OFF":
        message = MSG_OFF
        window = None
    elif start and now < start.replace(tzinfo=start.tzinfo or timezone.utc):
        message = MSG_SCHEDULED_LATER.format(start=start.strftime("%d %b %Y, %H:%M"))
        window = (start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M") if end else None)
    elif end and now > (end if end.tzinfo else end.replace(tzinfo=timezone.utc)):
        message = MSG_SCHEDULED_OVER.format(end=end.strftime("%d %b %Y, %H:%M"))
        window = (start.strftime("%Y-%m-%d %H:%M") if start else None, end.strftime("%Y-%m-%d %H:%M"))
    else:
        message = MSG_OFF
        window = None

    window_html = ""
    if window:
        window_html = f"<p class='nb-window'>Window: {html.escape(str(window[0] or '—'))} → {html.escape(str(window[1] or '—'))} (UTC)</p>"

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Documentation — Not Available</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0d1117; color: #e6edf3;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 12px;
           padding: 48px 40px; max-width: 480px; text-align: center; }}
  h1 {{ font-size: 22px; margin: 0 0 12px; }}
  p {{ color: #8b949e; margin: 6px 0; }}
  .icon {{ font-size: 40px; margin-bottom: 8px; }}
  .nb-window {{ color: #d29922; font-size: 14px; }}
</style></head>
<body><div class="card">
  <div class="icon">&#128196;</div>
  <h1>Not Available</h1>
  <p>{html.escape(message)}</p>
  {window_html}
  <p style="margin-top:20px;font-size:13px">Advotech Copilot — Live Documentation System</p>
</div></body></html>"""
    return HTMLResponse(content=page, status_code=403)


@router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def docs_page(
    request: Request,
    token: Optional[str] = Query(None, description="Admin JWT for preview when the window is closed"),
    db: AsyncSession = Depends(get_db),
):
    """
    The live /docs page. Gated by DocsConfig visibility + schedule.
    Admins (?token= or stored token) can always preview.
    """
    result = await db.execute(select(DocsConfig).where(DocsConfig.id == 1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = DocsConfig(
            id=1,
            visibility=DocsVisibility.ON,
            schedule_start=datetime(2027, 6, 10, 0, 0, 0, tzinfo=timezone.utc),
            schedule_end=datetime(2027, 6, 14, 23, 59, 59, tzinfo=timezone.utc),
        )
        db.add(cfg)
        await db.commit()

    accessible = _is_page_accessible(cfg)
    admin_ok = False
    if not accessible and token:
        admin_ok = await _admin_check(db, token)

    if not accessible and not admin_ok:
        return _render_not_available(cfg)

    return HTMLResponse(content=PAGE_HTML)


def _is_page_accessible(cfg: DocsConfig) -> bool:
    if cfg.visibility == DocsVisibility.ON:
        return True
    if cfg.visibility == DocsVisibility.OFF:
        return False
    now = datetime.now(timezone.utc)
    start = cfg.schedule_start or datetime(2027, 6, 10, tzinfo=timezone.utc)
    end = cfg.schedule_end or datetime(2027, 6, 14, 23, 59, 59, tzinfo=timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return start <= now <= end


async def _admin_check(db: AsyncSession, token: str) -> bool:
    """Async JWT → ADMIN user validation (token_version aware)."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email = payload.get("sub")
        if not email:
            return False
    except JWTError:
        return False

    result = await db.execute(
        select(User).where(User.email == email, User.is_deleted == False)  # noqa: E712
    )
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        return False
    if int(payload.get("tv") or 1) != int(getattr(user, "token_version", 1) or 1):
        return False
    return user.role == UserRole.ADMIN


# ──────────────────────────────────────────────────────────
# PAGE TEMPLATE (single-file HTML app; data fetched at runtime)
# ──────────────────────────────────────────────────────────

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Advotech Copilot — Live Docs & Pitch Deck</title>
<meta name="description" content="Advotech Copilot live documentation: YC-style pitch deck, technical whitepaper and real-time system dashboard.">
<style>
:root{
  --bg:#0d1117; --bg2:#161b22; --bg3:#1c2431; --border:#30363d; --text:#e6edf3;
  --muted:#8b949e; --accent:#e3b341; --accent2:#58a6ff; --good:#3fb950; --warn:#d29922; --bad:#f85149;
}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);line-height:1.6}
a{color:var(--accent2);text-decoration:none}
a:hover{text-decoration:underline}
.layout{display:flex;min-height:100vh}
/* ── Sidebar ── */
.sidebar{width:250px;flex-shrink:0;background:var(--bg2);border-right:1px solid var(--border);
  position:sticky;top:0;height:100vh;overflow-y:auto;padding:18px 14px}
.sidebar .brand{font-weight:700;font-size:16px;margin-bottom:2px}
.sidebar .brand-sub{font-size:11px;color:var(--muted);margin-bottom:16px}
#search{width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:6px;
  color:var(--text);padding:7px 10px;font-size:13px;margin-bottom:14px;outline:none}
#search:focus{border-color:var(--accent2)}
#nav a{display:block;padding:5px 8px;border-radius:5px;font-size:13px;color:var(--muted)}
#nav a.active,#nav a:hover{color:var(--text);background:var(--bg3);text-decoration:none}
.navhead{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin:14px 8px 4px}
/* ── Mobile topbar ── */
.topbar{display:none;position:sticky;top:0;z-index:50;background:var(--bg2);border-bottom:1px solid var(--border);
  padding:10px 14px;align-items:center;gap:10px}
.topbar button{background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:6px 12px;cursor:pointer}
/* ── Main ── */
main{flex:1;min-width:0;padding:28px 34px 80px;max-width:980px}
section{margin:44px 0;scroll-margin-top:20px}
h1{font-size:30px;margin:0 0 6px}
h2{font-size:22px;border-bottom:1px solid var(--border);padding-bottom:8px;margin:0 0 16px}
h3{font-size:16px;margin:18px 0 8px;color:var(--accent)}
.tagline{color:var(--accent);font-size:16px;font-weight:600;margin-bottom:4px}
.oneliner{color:var(--muted);font-size:16px;max-width:760px}
.chips{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0}
.chip{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:10px 14px;min-width:120px}
.chip b{display:block;font-size:18px;color:var(--accent)}
.chip span{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:18px 20px;margin:14px 0}
ul.pts{margin:8px 0 0;padding-left:20px}
ul.pts li{margin:6px 0;color:var(--muted)}
ul.pts li::marker{color:var(--accent)}
/* ── Feature matrix ── */
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.st{font-size:11px;font-weight:700;padding:2px 9px;border-radius:20px;letter-spacing:.05em;white-space:nowrap}
.st.LIVE{background:rgba(63,185,80,.15);color:var(--good);border:1px solid var(--good)}
.st.BETA{background:rgba(210,153,34,.15);color:var(--warn);border:1px solid var(--warn)}
.st.PLANNED{background:rgba(88,166,255,.12);color:var(--accent2);border:1px solid var(--accent2)}
/* ── Team ── */
.team-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:18px;margin-top:16px}
.tcard{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:22px 16px;text-align:center}
.tcard img,.tcard .avatar{width:96px;height:96px;border-radius:50%;object-fit:cover;border:3px solid var(--bg3);
  display:block;margin:0 auto 12px;background:var(--bg3)}
.tcard .avatar{display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:700;color:var(--accent)}
.tcard .name{font-weight:700;font-size:15px}
.tcard .role{color:var(--accent2);font-size:13px;margin:3px 0}
.tcard .mail{color:var(--muted);font-size:12px;word-break:break-all}
/* ── Mermaid ── */
.mermaid-wrap{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:16px;overflow-x:auto}
.mermaid{color:#e6edf3}
pre.fallback{color:var(--muted);font-size:12px;white-space:pre-wrap}
/* ── Actions ── */
.actions{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
.btn{background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:6px;
  padding:7px 14px;font-size:13px;cursor:pointer}
.btn:hover{border-color:var(--accent2)}
.btn.primary{background:var(--accent);color:#111;border-color:var(--accent);font-weight:600}
/* ── Admin ── */
#admin{border:1px solid var(--warn)}
#admin h2{color:var(--warn);border-color:var(--warn)}
#admin textarea{width:100%;min-height:120px;background:var(--bg);color:var(--text);border:1px solid var(--border);
  border-radius:6px;padding:10px;font-family:ui-monospace,monospace;font-size:12px}
#admin label{font-size:13px;color:var(--muted);display:block;margin:10px 0 4px}
#admin input,#admin select{background:var(--bg);color:var(--text);border:1px solid var(--border);
  border-radius:6px;padding:7px 10px;font-size:13px}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
.row>div{flex:1;min-width:160px}
.msg{font-size:13px;margin-top:8px}
.msg.ok{color:var(--good)} .msg.err{color:var(--bad)}
#tokenBox{margin-top:14px}
.muted{color:var(--muted);font-size:13px}
.badge{display:inline-block;font-size:11px;padding:2px 10px;border-radius:20px;border:1px solid var(--border);color:var(--muted);margin-left:8px}
/* ── Admin: tabs, drag/drop, simple editor ── */
.tab.active{border-color:var(--accent);color:var(--accent)}
.orderchip{display:inline-flex;align-items:center;gap:6px;background:var(--bg3);border:1px solid var(--border);
  border-radius:20px;padding:5px 12px;font-size:13px;cursor:grab;margin:3px;user-select:none}
.orderchip.drag,.tm-row.drag{opacity:.4}
.sed b{display:block;color:var(--accent2);font-size:14px;margin-bottom:4px}
.fm-row,.tm-row{align-items:center}
#verList .row{border-bottom:1px dashed var(--border);padding:4px 0}
/* ── Print ── */
@media print{
  .sidebar,.topbar,.actions,#admin,#tokenBox{display:none!important}
  body{background:#fff;color:#111}
  main{max-width:100%;padding:0}
  .card,.tcard,.chip{border-color:#ccc;background:#fff}
  h2{border-color:#ccc} h3{color:#333} .oneliner,.muted,ul.pts li{color:#444}
  table th{color:#555} th,td{border-color:#ccc}
}
@media (max-width:860px){
  .layout{flex-direction:column}
  .topbar{display:flex}
  .sidebar{display:none;position:fixed;z-index:100;height:100vh}
  .sidebar.open{display:block}
  main{padding:18px 16px 60px}
  h1{font-size:24px}
}
</style>
</head>
<body>
<div class="topbar">
  <button id="menuBtn" aria-label="Menu">&#9776;</button>
  <div style="display:flex;align-items:center;gap:8px">
    <img src="/logo.svg" alt="Advotech Copilot" width="22" height="22" style="filter:invert(0.9)" onerror="this.style.display='none'">
    <b>Advotech Copilot</b><span class="badge">Live Docs</span>
  </div>
</div>
<div class="layout">
  <aside class="sidebar" id="sidebar">
    <div class="brand" style="display:flex;align-items:center;gap:10px">
      <img src="/logo.svg" alt="Advotech Copilot" width="28" height="28" style="filter:invert(0.9)" onerror="this.style.display='none'">
      <span>Advotech Copilot</span>
    </div>
    <div class="brand-sub">Advotech · Live Documentation</div>
    <input id="search" type="search" placeholder="Search docs..." autocomplete="off">
    <div id="nav"></div>
    <div class="navhead">Actions</div>
    <div class="actions" style="flex-direction:column;margin:4px 0">
      <button class="btn" onclick="window.print()">&#128438; Save as PDF</button>
      <button class="btn" id="mdBtn">&#8681; Export Markdown</button>
      <button class="btn" id="adminBtn">&#9881; Admin</button>
    </div>
    <div id="tokenBox" style="display:none">
      <div class="navhead">Admin Access</div>
      <input id="tokenInput" type="password" placeholder="Paste admin JWT" style="width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:7px;font-size:12px">
      <button class="btn" id="tokenSave" style="margin-top:8px;width:100%">Use token</button>
      <div id="tokenMsg" class="msg"></div>
    </div>
  </aside>
  <main id="main">
    <section id="hero">
      <p class="tagline" id="tagline"></p>
      <h1 id="company"></h1>
      <p class="oneliner" id="oneliner"></p>
      <div class="chips" id="statChips"></div>
      <div class="actions">
        <a class="btn primary" href="/" >Open the App</a>
        <a class="btn" href="/api/platform/metrics">Live Metrics</a>
        <a class="btn" href="/health">System Health</a>
      </div>
    </section>
    <div id="content"></div>

    <section id="team" style="display:none">
      <h2>Team <span class="badge" id="teamName" style="display:none"></span></h2>
      <div class="team-grid" id="teamGrid"></div>
    </section>

    <section id="liveAnalytics" style="display:none">
      <h2>Live System Analytics</h2>
      <p class="muted">Straight from the production database — refreshed on load. <button class="btn" id="refreshStats" style="padding:3px 10px;font-size:12px">Refresh</button></p>
      <div class="chips" id="statChips2"></div>
    </section>

    <section id="admin" style="display:none">
      <h2>&#9881; Admin Panel</h2>
      <p class="muted">Publishing controls, schedule, content editing (simple + JSON), team management and versioning. Content edits go live when you publish.</p>
      <div class="card">
        <h3>Visibility &amp; Schedule</h3>
        <div class="row">
          <div>
            <label>Visibility</label>
            <select id="cfgVis">
              <option value="ON">ON — public</option>
              <option value="OFF">OFF — hidden</option>
              <option value="SCHEDULED">SCHEDULED — window only</option>
            </select>
          </div>
          <div><label>Start (local)</label><input id="cfgStart" type="datetime-local"></div>
          <div><label>End (local)</label><input id="cfgEnd" type="datetime-local"></div>
          <div><label>or Duration (hours)</label><input id="cfgDur" type="number" min="0.5" step="0.5" placeholder="e.g. 96"></div>
          <div><button class="btn primary" id="cfgSave">Save config</button></div>
        </div>
        <div id="cfgMsg" class="msg"></div>
        <p class="muted" id="cfgNow" style="margin-top:8px"></p>
      </div>
      <div class="card">
        <h3>Content — Draft &amp; Publish <span class="badge" id="draftStatus"></span></h3>
        <div class="row" style="margin-bottom:10px">
          <div style="flex:0 0 auto">
            <button class="btn tab active" data-tab="simple">Simple editor</button>
            <button class="btn tab" data-tab="json">JSON</button>
          </div>
        </div>
        <div id="tab-simple"></div>
        <div id="tab-json" style="display:none">
          <label>pitch_deck</label><textarea id="edPitch" spellcheck="false"></textarea>
          <label>tech_docs</label><textarea id="edTech" spellcheck="false"></textarea>
          <label>roadmap</label><textarea id="edRoadmap" spellcheck="false"></textarea>
          <label>changelog</label><textarea id="edChangelog" spellcheck="false"></textarea>
        </div>
        <div class="row" style="margin-top:12px">
          <div style="flex:0 0 auto"><button class="btn" id="draftSave">&#128190; Save draft</button></div>
          <div style="flex:0 0 auto"><button class="btn primary" id="publishBtn">&#128640; Publish</button></div>
          <div style="flex:0 0 auto"><button class="btn" id="discardDraft">&#10060; Discard draft</button></div>
        </div>
        <div id="edMsg" class="msg"></div>
      </div>
      <div class="card">
        <h3>Team Members <span class="muted" style="font-weight:400;font-size:12px">(drag rows to reorder)</span></h3>
        <div class="row">
          <div><label>Full name</label><input id="tmName"></div>
          <div><label>Role</label><input id="tmRole"></div>
          <div><label>Email</label><input id="tmEmail"></div>
          <div><label>Image URL</label><input id="tmImg"></div>
          <div><button class="btn primary" id="tmAdd">Add</button></div>
        </div>
        <div id="tmMsg" class="msg"></div>
        <div id="tmList" style="margin-top:12px"></div>
      </div>
      <div class="card">
        <h3>Versions <span class="muted" style="font-weight:400;font-size:12px">(last 10 publishes, restore anytime)</span></h3>
        <div id="verList" class="muted">Loading…</div>
        <div id="verMsg" class="msg"></div>
      </div>
    </section>

    <footer style="margin-top:60px;border-top:1px solid var(--border);padding-top:16px" class="muted">
      Advotech Copilot · Live documentation, auto-synced with the running system · Built for AI Builders Congress 2026
    </footer>
  </main>
</div>

<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>
(function(){
"use strict";
var TOKEN = null;
try {
  TOKEN = new URLSearchParams(location.search).get("token") ||
          localStorage.getItem("contractmind_token") ||
          localStorage.getItem("docs_admin_token") || null;
  if (!TOKEN) {
    var rawDemo = localStorage.getItem("app_auth_v2");
    if (rawDemo) { var p = JSON.parse(rawDemo); TOKEN = p.token || p.access_token || null; }
  }
} catch(e){}
if (TOKEN) { try { localStorage.setItem("docs_admin_token", TOKEN); } catch(e){} }

function authHeaders(extra){
  var h = extra || {};
  if (TOKEN) h["Authorization"] = "Bearer " + TOKEN;
  return h;
}
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g, function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]; }); }
function titleize(k){ return String(k).replace(/_/g," ").replace(/^\w/,function(c){return c.toUpperCase();}); }
function fmtNum(n){ return (n==null?"0":String(n)).replace(/\B(?=(\d{3})+(?!\d))/g,","); }
function initials(name){
  return name.split(/\s+/).slice(0,2).map(function(w){return (w[0]||"").toUpperCase();}).join("") || "?";
}

/* ── Nav & search ── */
var NAV = [
  ["pitch","Pitch Deck",[
    "problem","solution","why-now","product-demo","market","business-model",
    "traction","competition","unique-advantage","go-to-market","vision","ask"]],
  ["tech","Technical Docs",[
    "overview","features","architecture","dataflow","stack","api","data-layer",
    "ai-layer","performance","security"]],
  ["more","More",["roadmap","changelog","team","liveAnalytics"]]
];
function buildNav(){
  var nav = document.getElementById("nav"), html = "";
  NAV.forEach(function(g){
    html += '<div class="navhead">'+esc(g[1])+'</div>';
    if (g[0]==="pitch") html += '<a href="#hero">Top</a>';
    if (g[0]==="tech") html += '<a href="#features">Feature Matrix</a>';
    if (g[0]==="more"){
      if (g[1]==="More") html += '<a href="#roadmap">Roadmap</a><a href="#changelog">Changelog</a><a href="#team">Team</a><a href="#liveAnalytics">Analytics</a>';
    }
    if (g[0]==="pitch"){
      g[2].forEach(function(id){ html += '<a href="#'+id+'">'+esc(titleize(id.replace("why-now","why_now").replace("go-to-market","go_to_market").replace("product-demo","product_demo").replace("unique-advantage","unique_advantage")))+'</a>'; });
    }
  });
  nav.innerHTML = html;
}
buildNav();
document.getElementById("menuBtn").onclick = function(){
  document.getElementById("sidebar").classList.toggle("open");
};
document.getElementById("search").addEventListener("input", function(){
  var q = this.value.trim().toLowerCase();
  var secs = document.querySelectorAll("main section");
  secs.forEach(function(s){
    if (!q) { s.style.display=""; return; }
    s.style.display = s.textContent.toLowerCase().indexOf(q) >= 0 ? "" : "none";
  });
});

/* ── Scrollspy ── */
window.addEventListener("scroll", function(){
  var links = document.querySelectorAll("#nav a");
  var current = "";
  document.querySelectorAll("main section").forEach(function(s){
    if (s.getBoundingClientRect().top < 140) current = s.id;
  });
  links.forEach(function(a){
    a.classList.toggle("active", a.getAttribute("href") === "#"+current);
  });
});

/* ── Stat chips ── */
var STAT_LABELS = {
  total_users:"Users", total_audits:"Contract Audits", total_chat_sessions:"Chat Sessions",
  total_chat_messages:"Chat Messages", total_lawyers:"Lawyers", total_enterprises:"Enterprises",
  statutes_indexed:"Statutes Indexed", languages_supported:"Languages", jurisdictions_active:"Jurisdictions"
};
function renderStats(el, stats){
  var html = "";
  Object.keys(STAT_LABELS).forEach(function(k){
    if (stats[k] === undefined) return;
    html += '<div class="chip"><b>'+esc(fmtNum(stats[k]))+'</b><span>'+esc(STAT_LABELS[k])+'</span></div>';
  });
  el.innerHTML = html;
}

/* ── Renderers ── */
function section(id, inner){
  return '<section id="'+id+'">'+inner+'</section>';
}
function headCard(title, obj){
  var pts = (obj.points||[]).map(function(p){ return "<li>"+esc(p)+"</li>"; }).join("");
  return '<h2>'+esc(title)+'</h2><div class="card"><h3>'+esc(obj.headline||"")+'</h3><ul class="pts">'+pts+'</ul></div>';
}
function renderPitch(pitch){
  var PITCH_ORDER = ["problem","solution","why_now","product_demo","market","business_model",
    "traction","competition","unique_advantage","go_to_market","vision"];
  var ORDER_LABELS = {problem:"Problem", solution:"Solution", why_now:"Why Now?",
    product_demo:"Product Demo", market:"Market Opportunity", business_model:"Business Model",
    traction:"Traction", competition:"Competition", unique_advantage:"Unique Advantage",
    go_to_market:"Go-To-Market", vision:"Vision"};
  var order = (Array.isArray(pitch.section_order) && pitch.section_order.length) ? pitch.section_order : PITCH_ORDER;
  var out = "";
  order.forEach(function(key){
    var obj = pitch[key];
    if (obj && (obj.headline || obj.points)) out += headCard(ORDER_LABELS[key] || titleize(key), obj);
  });
  if (pitch.the_ask) out += section("ask", '<h2>The Ask</h2><div class="card"><p style="font-size:17px;margin:0">'+esc(pitch.the_ask)+'</p></div>');
  return out;
}
function renderTech(tech){
  var out = "";
  var po = tech.product_overview||{};
  var poPts = [];
  if (po.what_it_does) poPts.push(po.what_it_does);
  if (po.target_users) poPts.push("Target users: " + po.target_users.join(", "));
  if (po.core_use_cases) poPts.push("Core use cases: " + po.core_use_cases.join(" · "));
  out += section("overview", '<h2>Product Overview</h2><div class="card"><ul class="pts">'+poPts.map(function(p){return "<li>"+esc(p)+"</li>";}).join("")+'</ul></div>');

  var feats = tech.feature_matrix||[];
  if (feats.length){
    var rows = feats.map(function(f){
      var st = String(f.status||"PLANNED").toUpperCase();
      return '<tr><td>'+esc(f.name)+'</td><td><span class="st '+esc(st)+'">'+esc(st)+'</span></td><td style="color:var(--muted)">'+esc(f.note||"")+'</td></tr>';
    }).join("");
    out += section("features", '<h2>Feature Matrix <span class="badge">live-synced</span></h2><table><thead><tr><th>Feature</th><th>Status</th><th>Note</th></tr></thead><tbody>'+rows+'</tbody></table>');
  }

  ["architecture_mermaid:architecture:Architecture","dataflow_mermaid:dataflow:Data Flow"].forEach(function(pair){
    var parts = pair.split(":"); var key = parts[0], id = parts[1], title = parts[2];
    var src = tech[key];
    if (!src) return;
    out += section(id, '<h2>'+esc(title)+'</h2><div class="mermaid-wrap"><pre class="mermaid" data-src="'+esc(src)+'">'+esc(src)+'</pre></div>');
  });

  var stack = tech.tech_stack||[];
  if (stack.length){
    var srows = stack.map(function(s){ return '<tr><td style="white-space:nowrap"><b>'+esc(s.layer)+'</b></td><td>'+esc(s.items)+'</td></tr>'; }).join("");
    out += section("stack", '<h2>Technology Stack</h2><table>'+srows+'</table>');
  }

  var apis = tech.api_docs||[];
  if (apis.length){
    var arows = apis.map(function(a){ return '<tr><td style="white-space:nowrap"><b>'+esc(a.group)+'</b></td><td>'+esc(a.endpoints)+'</td><td style="color:var(--muted)">'+esc(a.auth||"")+'</td></tr>'; }).join("");
    out += section("api", '<h2>API Documentation</h2><table><thead><tr><th>Group</th><th>Endpoints</th><th>Auth</th></tr></thead><tbody>'+arows+'</tbody></table>');
  }

  var dl = tech.data_layer||{};
  if (dl.sources) out += section("data-layer", '<h2>Data Layer</h2><div class="card"><h3>Sources</h3><ul class="pts">'+dl.sources.map(function(p){return "<li>"+esc(p)+"</li>";}).join("")+'</ul><h3>Storage</h3><p class="muted" style="margin:4px 0 12px">'+esc(dl.storage||"")+'</p><h3>Privacy</h3><p class="muted" style="margin:4px 0">'+esc(dl.privacy||"")+'</p></div>');

  var ai = tech.ai_layer||{};
  if (ai.models) out += section("ai-layer", '<h2>AI Layer</h2><div class="card"><h3>Models</h3><p class="muted" style="margin:4px 0 12px">'+esc(ai.models)+'</p><h3>Retrieval</h3><p class="muted" style="margin:4px 0 12px">'+esc(ai.retrieval||"")+'</p><h3>Personalization</h3><p class="muted" style="margin:4px 0 12px">'+esc(ai.personalization||"")+'</p><h3>Explainability</h3><p class="muted" style="margin:4px 0">'+esc(ai.explainability||"")+'</p></div>');

  var perf = tech.performance||{};
  if (perf.load_expectation) out += section("performance", '<h2>Performance &amp; Scalability</h2><div class="card"><h3>Load Expectation</h3><p class="muted" style="margin:4px 0 12px">'+esc(perf.load_expectation)+'</p><h3>Optimization</h3><p class="muted" style="margin:4px 0">'+esc(perf.optimization||"")+'</p></div>');

  var sec = tech.security||{};
  if (sec.auth) out += section("security", '<h2>Security</h2><div class="card"><h3>Auth</h3><p class="muted" style="margin:4px 0 12px">'+esc(sec.auth)+'</p><h3>RBAC</h3><p class="muted" style="margin:4px 0 12px">'+esc(sec.rbac||"")+'</p><h3>Data Protection</h3><p class="muted" style="margin:4px 0">'+esc(sec.data_protection||"")+'</p></div>');

  return out;
}
function renderRoadmap(rm){
  var order = ["short_term","mid_term","long_term"];
  var titles = {short_term:"Short Term", mid_term:"Mid Term", long_term:"Long Term"};
  var out = "";
  order.forEach(function(k){
    if (!rm[k]) return;
    out += '<div class="card"><h3>'+esc(titles[k])+'</h3><ul class="pts">'+rm[k].map(function(p){return "<li>"+esc(p)+"</li>";}).join("")+'</ul></div>';
  });
  return out ? section("roadmap", '<h2>Roadmap</h2>'+out) : "";
}
function renderChangelog(log){
  if (!log || !log.length) return "";
  var out = log.map(function(e){
    var items = (e.changes||[]).map(function(c){ return "<li>"+esc(c)+"</li>"; }).join("");
    return '<div class="card"><h3>'+esc(e.version||"")+' <span class="badge">'+esc(e.date||"")+'</span></h3><ul class="pts">'+items+'</ul></div>';
  }).join("");
  return section("changelog", '<h2>Changelog</h2>'+out);
}
function renderTeam(team, pitch){
  var grid = document.getElementById("teamGrid");
  var nameBadge = document.getElementById("teamName");
  if (pitch && pitch.team_name){
    nameBadge.textContent = pitch.team_name;
    nameBadge.style.display = "";
  } else {
    nameBadge.style.display = "none";
  }
  if (!team || !team.length){ document.getElementById("team").style.display="none"; return; }
  grid.innerHTML = team.map(function(m){
    var img = m.image_url
      ? '<img loading="lazy" src="'+esc(m.image_url)+'" alt="'+esc(m.full_name)+'" onerror="this.outerHTML=\'<div class=avatar>'+esc(initials(m.full_name))+'</div>\'">'
      : '<div class="avatar">'+esc(initials(m.full_name))+'</div>';
    var mail = m.email ? '<div class="mail">'+esc(m.email)+'</div>' : "";
    return '<div class="tcard">'+img+'<div class="name">'+esc(m.full_name)+'</div><div class="role">'+esc(m.role)+'</div>'+mail+'</div>';
  }).join("");
  document.getElementById("team").style.display="";
}

/* ── Mermaid (lazy: only rendered when scrolled into view) ── */
function initMermaid(){
  var els = Array.prototype.slice.call(document.querySelectorAll("pre.mermaid"));
  if (typeof mermaid === "undefined"){
    els.forEach(function(el){
      el.className = "fallback";
      el.textContent = el.getAttribute("data-src") || el.textContent;
    });
    return;
  }
  mermaid.initialize({ startOnLoad:false, theme:"dark", securityLevel:"loose" });
  if (typeof IntersectionObserver === "undefined"){
    mermaid.run({ nodes: els }).catch(function(){
      els.forEach(function(el){
        el.className = "fallback"; el.textContent = el.getAttribute("data-src") || el.textContent;
      });
    });
    return;
  }
  var io = new IntersectionObserver(function(entries){
    entries.forEach(function(en){
      if (!en.isIntersecting) return;
      io.unobserve(en.target);
      var el = en.target;
      mermaid.run({ nodes: [el] }).catch(function(){
        el.className = "fallback";
        el.textContent = el.getAttribute("data-src") || el.textContent;
      });
    });
  }, { rootMargin: "250px" });
  els.forEach(function(el){ io.observe(el); });
}

/* ── Admin ── */
var ADMIN_MODE = false;
var WORK = null;        // content being edited {pitch_deck, tech_docs, roadmap, changelog, custom_sections}
var ADMIN_TEAM = null;  // current admin team list (for drag/drop reorder)
var PITCH_ORDER = ["problem","solution","why_now","product_demo","market","business_model",
  "traction","competition","unique_advantage","go_to_market","vision"];
var ORDER_LABELS = {problem:"Problem", solution:"Solution", why_now:"Why Now?",
  product_demo:"Product Demo", market:"Market Opportunity", business_model:"Business Model",
  traction:"Traction", competition:"Competition", unique_advantage:"Unique Advantage",
  go_to_market:"Go-To-Market", vision:"Vision"};

function el(id){ return document.getElementById(id); }
function showMsg(id, text, ok){
  var box = el(id);
  box.textContent = text; box.className = "msg " + (ok ? "ok" : "err");
}
function toLocalInputValue(iso){
  if (!iso) return "";
  var d = new Date(iso);
  if (isNaN(d)) return "";
  var pad = function(n){ return (n<10?"0":"")+n; };
  return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate())+"T"+pad(d.getHours())+":"+pad(d.getMinutes());
}
function localInputToISO(v){
  if (!v) return null;
  var d = new Date(v);
  return isNaN(d) ? null : d.toISOString();
}
function api(method, url, body){
  return fetch(url, {
    method: method,
    headers: authHeaders(body ? {"Content-Type":"application/json"} : {}),
    body: body ? JSON.stringify(body) : undefined
  }).then(function(r){
    if (!r.ok) return r.json().then(function(d){ throw new Error(d.detail || r.status); });
    return r.json();
  });
}
function syncJsonTabs(){
  if (!WORK) return;
  el("edPitch").value = JSON.stringify(WORK.pitch_deck || {}, null, 2);
  el("edTech").value = JSON.stringify(WORK.tech_docs || {}, null, 2);
  el("edRoadmap").value = JSON.stringify(WORK.roadmap || {}, null, 2);
  el("edChangelog").value = JSON.stringify(WORK.changelog || [], null, 2);
}
function parseJsonTabs(silent){
  ["edPitch:pitch_deck","edTech:tech_docs","edRoadmap:roadmap","edChangelog:changelog"].forEach(function(pair){
    var parts = pair.split(":");
    var v = el(parts[0]).value.trim();
    if (!v) return;
    try { WORK[parts[1]] = JSON.parse(v); }
    catch(e){ if (!silent) throw e; }
  });
}
function currentOrder(){
  var chips = el("orderChips").children;
  var out = [];
  for (var i=0;i<chips.length;i++) out.push(chips[i].getAttribute("data-key"));
  return out;
}
function buildSimpleEditor(){
  if (!WORK) return;
  var p = WORK.pitch_deck || {};
  if (!WORK.tech_docs.feature_matrix) WORK.tech_docs.feature_matrix = [];
  if (!Array.isArray(WORK.changelog)) WORK.changelog = [];
  var order = (Array.isArray(p.section_order) && p.section_order.length) ? p.section_order : PITCH_ORDER;
  var chips = order.map(function(k){
    return '<span class="orderchip" draggable="true" data-key="'+esc(k)+'">&#9776; '+esc(ORDER_LABELS[k]||titleize(k))+'</span>';
  }).join("");
  var html = '<h3 style="margin-top:0">Pitch deck</h3>' +
    '<div class="row">' +
      '<div><label>Tagline</label><input id="seTagline" value="'+esc(p.tagline||"")+'"></div>' +
      '<div><label>One-liner</label><input id="seOneLiner" value="'+esc(p.one_liner||"")+'"></div>' +
      '<div><label>Team name</label><input id="seTeamName" value="'+esc(p.team_name||"")+'"></div>' +
    '</div>' +
    '<label style="margin-top:12px">Section order (drag to reorder)</label><div id="orderChips">'+chips+'</div>' +
    '<div id="seSections">' + order.map(function(k){
      var s = p[k]||{};
      return '<div class="card sed" data-sec="'+esc(k)+'"><b>'+esc(ORDER_LABELS[k]||titleize(k))+'</b>' +
        '<label>Headline</label><input class="seHead" value="'+esc(s.headline||"")+'">' +
        '<label>Points (one per line)</label><textarea class="sePts">'+esc((s.points||[]).join("\n"))+'</textarea></div>';
    }).join('') + '</div>' +
    '<div class="card sed"><b>The Ask</b><input id="seAsk" value="'+esc(p.the_ask||"")+'"></div>' +
    '<h3>Feature matrix</h3><div id="fmRows"></div><button class="btn" id="fmAdd" style="margin-top:6px">+ Add feature</button>' +
    '<h3>Roadmap</h3>' +
    '<label>Short term (one per line)</label><textarea id="seShort">'+esc(((WORK.roadmap||{}).short_term||[]).join("\n"))+'</textarea>' +
    '<label>Mid term</label><textarea id="seMid">'+esc(((WORK.roadmap||{}).mid_term||[]).join("\n"))+'</textarea>' +
    '<label>Long term</label><textarea id="seLong">'+esc(((WORK.roadmap||{}).long_term||[]).join("\n"))+'</textarea>' +
    '<h3>Changelog</h3><div id="clRows"></div><button class="btn" id="clAdd" style="margin-top:6px">+ Add entry</button>';
  el("tab-simple").innerHTML = html;
  buildFeatureRows();
  buildChangelogRows();
  wireOrderChips();
  el("fmAdd").onclick = function(){ WORK.tech_docs.feature_matrix.push({name:"",status:"PLANNED",note:""}); buildFeatureRows(); };
  el("clAdd").onclick = function(){ WORK.changelog.push({date:"",version:"",changes:[]}); buildChangelogRows(); };
}
function buildFeatureRows(){
  var feats = WORK.tech_docs.feature_matrix;
  el("fmRows").innerHTML = feats.map(function(f,i){
    var stSel = ["LIVE","BETA","PLANNED"].map(function(s){
      return '<option'+(String(f.status||"").toUpperCase()===s?' selected':'')+'>'+s+'</option>';
    }).join("");
    return '<div class="row fm-row" data-i="'+i+'">' +
      '<div><input data-f="name" value="'+esc(f.name||"")+'" placeholder="Feature"></div>' +
      '<div style="flex:0 0 120px"><select data-f="status">'+stSel+'</select></div>' +
      '<div><input data-f="note" value="'+esc(f.note||"")+'" placeholder="Note"></div>' +
      '<div style="flex:0 0 auto"><button class="btn" data-fmdel="'+i+'">&#10005;</button></div></div>';
  }).join("");
  el("fmRows").querySelectorAll("input,select").forEach(function(inp){
    inp.onchange = function(){
      var i = +inp.closest(".fm-row").getAttribute("data-i");
      WORK.tech_docs.feature_matrix[i][inp.getAttribute("data-f")] = inp.value;
    };
  });
  el("fmRows").querySelectorAll("[data-fmdel]").forEach(function(b){
    b.onclick = function(){ WORK.tech_docs.feature_matrix.splice(+b.getAttribute("data-fmdel"),1); buildFeatureRows(); };
  });
}
function buildChangelogRows(){
  el("clRows").innerHTML = WORK.changelog.map(function(e,i){
    return '<div class="card sed" data-cl="'+i+'">' +
      '<div class="row"><div><label>Date</label><input data-clf="date" value="'+esc(e.date||"")+'"></div>' +
      '<div><label>Version</label><input data-clf="version" value="'+esc(e.version||"")+'"></div></div>' +
      '<label>Changes (one per line)</label><textarea data-clf="changes">'+esc((e.changes||[]).join("\n"))+'</textarea>' +
      '<button class="btn" data-cldel="'+i+'" style="margin-top:8px">&#10005; Remove entry</button></div>';
  }).join("");
  el("clRows").querySelectorAll("[data-clf]").forEach(function(inp){
    inp.onchange = function(){
      var i = +inp.closest("[data-cl]").getAttribute("data-cl");
      if (inp.getAttribute("data-clf") === "changes"){
        WORK.changelog[i].changes = inp.value.split("\n").map(function(s){return s.trim();}).filter(Boolean);
      } else {
        WORK.changelog[i][inp.getAttribute("data-clf")] = inp.value;
      }
    };
  });
  el("clRows").querySelectorAll("[data-cldel]").forEach(function(b){
    b.onclick = function(){ WORK.changelog.splice(+b.getAttribute("data-cldel"),1); buildChangelogRows(); };
  });
}
function wireOrderChips(){
  var chips = Array.prototype.slice.call(el("orderChips").children);
  chips.forEach(function(ch){
    ch.ondragstart = function(e){ e.dataTransfer.setData("text/plain", ch.getAttribute("data-key")); ch.classList.add("drag"); };
    ch.ondragend = function(){ ch.classList.remove("drag"); };
    ch.ondragover = function(e){ e.preventDefault(); };
    ch.ondrop = function(e){
      e.preventDefault();
      var moved = e.dataTransfer.getData("text/plain");
      if (!moved || moved === ch.getAttribute("data-key")) return;
      var order = currentOrder();
      var from = order.indexOf(moved), to = order.indexOf(ch.getAttribute("data-key"));
      order.splice(to, 0, order.splice(from,1)[0]);
      WORK.pitch_deck.section_order = order;
      buildSimpleEditor();
    };
  });
}
function collectSimple(){
  var p = WORK.pitch_deck;
  if (el("seTagline")) p.tagline = el("seTagline").value.trim() || null;
  if (el("seOneLiner")) p.one_liner = el("seOneLiner").value.trim() || null;
  if (el("seTeamName")) p.team_name = el("seTeamName").value.trim() || null;
  if (el("seAsk")) p.the_ask = el("seAsk").value.trim() || null;
  document.querySelectorAll("#seSections .sed").forEach(function(card){
    var k = card.getAttribute("data-sec");
    var head = card.querySelector(".seHead").value.trim();
    var pts = card.querySelector(".sePts").value.split("\n").map(function(s){return s.trim();}).filter(Boolean);
    p[k] = { headline: head || null, points: pts };
  });
  ["seShort:short_term","seMid:mid_term","seLong:long_term"].forEach(function(pair){
    var parts = pair.split(":");
    WORK.roadmap[parts[1]] = el(parts[0]).value.split("\n").map(function(s){return s.trim();}).filter(Boolean);
  });
}
function currentWork(){
  if (el("tab-simple").style.display !== "none"){
    collectSimple();
  } else {
    parseJsonTabs(false);  // throws on invalid JSON — caller catches
  }
  return {
    pitch_deck: WORK.pitch_deck,
    tech_docs: WORK.tech_docs,
    roadmap: WORK.roadmap,
    changelog: WORK.changelog,
    custom_sections: WORK.custom_sections
  };
}
function loadAdmin(){
  el("admin").style.display = "";
  el("tokenBox").style.display = "";
  fetch("/api/docs/config", { headers: authHeaders() })
    .then(function(r){ if (!r.ok) throw new Error("not admin"); return r.json(); })
    .then(function(cfg){
      ADMIN_MODE = true;
      el("cfgVis").value = cfg.visibility;
      el("cfgStart").value = toLocalInputValue(cfg.schedule_start);
      el("cfgEnd").value = toLocalInputValue(cfg.schedule_end);
      el("cfgNow").textContent = "Last updated " + (cfg.updated_at || "unknown");
      loadDraftStatus();
      loadVersions();
      return Promise.all([
        fetch("/api/docs/content", { headers: authHeaders() }).then(function(r){ return r.json(); }),
        fetch("/api/docs/draft", { headers: authHeaders() }).then(function(r){ return r.ok ? r.json() : { draft: null }; }).catch(function(){ return { draft: null }; })
      ]);
    })
    .then(function(pair){
      var content = pair[0] || {};
      var draftObj = (pair[1] && pair[1].draft) ? pair[1].draft : null;
      WORK = {
        pitch_deck: content.pitch_deck || {},
        tech_docs: content.tech_docs || {},
        roadmap: content.roadmap || {},
        changelog: content.changelog || [],
        custom_sections: content.custom_sections || []
      };
      if (draftObj && draftObj.saved_at){
        if (draftObj.pitch_deck) Object.assign(WORK.pitch_deck, draftObj.pitch_deck);
        if (draftObj.tech_docs) Object.assign(WORK.tech_docs, draftObj.tech_docs);
        if (draftObj.roadmap) Object.assign(WORK.roadmap, draftObj.roadmap);
        if (draftObj.changelog) WORK.changelog = draftObj.changelog;
      }
      syncJsonTabs();
      buildSimpleEditor();
      return fetch("/api/docs/admin/team", { headers: authHeaders() }).then(function(r){ return r.json(); });
    })
    .then(function(team){ if (team) renderAdminTeam(team); })
    .catch(function(){
      ADMIN_MODE = false;
      el("admin").style.display = "none";
      el("tokenBox").style.display = "";
      showMsg("tokenMsg", "Admin token required for the admin panel.", false);
    });
}
function loadDraftStatus(){
  fetch("/api/docs/draft", { headers: authHeaders() })
    .then(function(r){ return r.ok ? r.json() : { draft: null }; })
    .then(function(d){
      if (d.draft && d.draft.saved_at){
        el("draftStatus").textContent = "draft saved " + String(d.draft.saved_at).slice(0,16).replace("T"," ");
      } else {
        el("draftStatus").textContent = "no draft";
      }
    }).catch(function(){});
}
function loadVersions(){
  fetch("/api/docs/versions", { headers: authHeaders() })
    .then(function(r){ return r.ok ? r.json() : []; })
    .then(function(vs){
      if (!vs.length){ el("verList").textContent = "No versions yet — publish something."; return; }
      el("verList").innerHTML = vs.map(function(v){
        return '<div class="row"><div>' + esc(String(v.at||"").slice(0,19).replace("T"," ")) + ' — ' + esc(v.by||"") +
          '</div><div style="flex:0 0 auto"><button class="btn" data-restore="'+v.index+'">Restore</button></div></div>';
      }).join("");
      el("verList").querySelectorAll("[data-restore]").forEach(function(b){
        b.onclick = function(){
          api("POST", "/api/docs/versions/"+b.getAttribute("data-restore")+"/restore")
            .then(function(){ showMsg("verMsg", "Restored. Reloading…", true); setTimeout(function(){ location.reload(); }, 800); })
            .catch(function(e){ showMsg("verMsg", e.message, false); });
        };
      });
    }).catch(function(){});
}
function renderAdminTeam(team){
  ADMIN_TEAM = team;
  var rows = team.map(function(m){
    return '<div class="row tm-row" draggable="true" data-id="'+m.id+'" style="margin:6px 0;cursor:grab">' +
      '<div><b>'+esc(m.full_name)+'</b> <span class="muted">('+esc(m.role)+(m.is_active?"":" · hidden")+')</span></div>' +
      '<div style="flex:0 0 auto"><button class="btn" data-del="'+m.id+'">Delete</button> <button class="btn" data-hide="'+m.id+'">'+(m.is_active?"Hide":"Show")+'</button></div></div>';
  }).join("");
  el("tmList").innerHTML = rows || '<p class="muted">No team members yet.</p>';
  el("tmList").querySelectorAll("[data-del]").forEach(function(b){
    b.onclick = function(){ api("DELETE", "/api/docs/team/"+b.getAttribute("data-del")).then(function(){ loadAdmin(); }).catch(function(e){ showMsg("tmMsg", e.message, false); }); };
  });
  el("tmList").querySelectorAll("[data-hide]").forEach(function(b){
    b.onclick = function(){
      var id = b.getAttribute("data-hide");
      var member = team.filter(function(m){ return String(m.id)===String(id); })[0];
      api("PUT", "/api/docs/team/"+id, { is_active: !member.is_active }).then(function(){ loadAdmin(); }).catch(function(e){ showMsg("tmMsg", e.message, false); });
    };
  });
  // Drag/drop reorder → sequential display_order
  el("tmList").querySelectorAll(".tm-row").forEach(function(rEl){
    rEl.ondragstart = function(e){ e.dataTransfer.setData("text/plain", rEl.getAttribute("data-id")); rEl.classList.add("drag"); };
    rEl.ondragend = function(){ rEl.classList.remove("drag"); };
    rEl.ondragover = function(e){ e.preventDefault(); };
    rEl.ondrop = function(e){
      e.preventDefault();
      var moved = e.dataTransfer.getData("text/plain");
      if (!moved || moved === rEl.getAttribute("data-id")) return;
      var order = ADMIN_TEAM.map(function(m){ return String(m.id); });
      var from = order.indexOf(moved), to = order.indexOf(rEl.getAttribute("data-id"));
      order.splice(to, 0, order.splice(from,1)[0]);
      Promise.all(order.map(function(id, idx){
        return api("PUT", "/api/docs/team/"+id, { display_order: idx });
      })).then(function(){ loadAdmin(); loadAll(); })
        .catch(function(e){ showMsg("tmMsg", e.message, false); });
    };
  });
}
document.getElementById("adminBtn").onclick = function(){ loadAdmin(); window.location.hash = "#admin"; };
document.getElementById("cfgSave").onclick = function(){
  var body = { visibility: el("cfgVis").value };
  var s = localInputToISO(el("cfgStart").value);
  var e = localInputToISO(el("cfgEnd").value);
  if (s) body.schedule_start = s;
  if (e) body.schedule_end = e;
  var dur = parseFloat(el("cfgDur").value);
  if (!isNaN(dur) && dur > 0) body.duration_hours = dur;  // end = start + duration
  api("PUT", "/api/docs/config", body)
    .then(function(){ showMsg("cfgMsg", "Saved. Reload /docs to see the new access state.", true); })
    .catch(function(e2){ showMsg("cfgMsg", e2.message, false); });
};
// Editor tabs
document.querySelectorAll("#admin .tab").forEach(function(t){
  t.onclick = function(){
    document.querySelectorAll("#admin .tab").forEach(function(x){ x.classList.remove("active"); });
    t.classList.add("active");
    var tab = t.getAttribute("data-tab");
    if (tab === "json"){
      syncJsonTabs();
      el("tab-json").style.display = "";
      el("tab-simple").style.display = "none";
    } else {
      try { parseJsonTabs(true); } catch(e){}
      el("tab-simple").style.display = "";
      el("tab-json").style.display = "none";
      buildSimpleEditor();
    }
  };
});
document.getElementById("draftSave").onclick = function(){
  var body;
  try { body = currentWork(); }
  catch(e){ showMsg("edMsg", "Invalid JSON: "+e.message, false); return; }
  api("PUT", "/api/docs/draft", body)
    .then(function(){ showMsg("edMsg", "Draft saved — not live until you publish.", true); loadDraftStatus(); })
    .catch(function(e){ showMsg("edMsg", e.message, false); });
};
document.getElementById("publishBtn").onclick = function(){
  var body;
  try { body = currentWork(); }
  catch(e){ showMsg("edMsg", "Invalid JSON: "+e.message, false); return; }
  api("PUT", "/api/docs/draft", body)          // keep the draft in sync with what is published
    .then(function(){ return api("POST", "/api/docs/publish"); })
    .then(function(){ showMsg("edMsg", "Published! Version recorded. Reloading…", true); setTimeout(function(){ location.reload(); }, 900); })
    .catch(function(e){ showMsg("edMsg", e.message, false); });
};
document.getElementById("discardDraft").onclick = function(){
  api("DELETE", "/api/docs/draft")
    .then(function(){ showMsg("edMsg", "Draft discarded — live content untouched.", true); loadDraftStatus(); })
    .catch(function(e){ showMsg("edMsg", e.message, false); });
};
document.getElementById("tmAdd").onclick = function(){
  var name = el("tmName").value.trim();
  var role = el("tmRole").value.trim();
  if (!name || !role){ showMsg("tmMsg", "Name and role are required.", false); return; }
  api("POST", "/api/docs/team", {
    full_name: name, role: role,
    email: el("tmEmail").value.trim() || null,
    image_url: el("tmImg").value.trim() || null
  }).then(function(){ showMsg("tmMsg", "Added.", true); loadAdmin(); loadAll(); })
    .catch(function(e){ showMsg("tmMsg", e.message, false); });
};
document.getElementById("tokenSave").onclick = function(){
  TOKEN = el("tokenInput").value.trim();
  try { localStorage.setItem("docs_admin_token", TOKEN); } catch(e){}
  fetch("/api/docs/config", { headers: authHeaders() })
    .then(function(r){ if (!r.ok) throw new Error("Invalid token or not an admin."); return r.json(); })
    .then(function(){ showMsg("tokenMsg", "Admin unlocked.", true); loadAdmin(); })
    .catch(function(e){ showMsg("tokenMsg", e.message, false); });
};

/* ── Markdown export ── */
document.getElementById("mdBtn").onclick = function(){
  fetch("/api/docs/export.md", { headers: authHeaders() })
    .then(function(r){ if (!r.ok) throw new Error("Export failed ("+r.status+")"); return r.text(); })
    .then(function(md){
      var blob = new Blob([md], { type:"text/markdown" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "contractmind-docs.md";
      a.click();
      URL.revokeObjectURL(a.href);
    })
    .catch(function(e){ alert(e.message); });
};

/* ── Boot ── */
function loadAll(){
  Promise.all([
    fetch("/api/docs/content", { headers: authHeaders() }).then(function(r){ return r.ok ? r.json() : Promise.reject(new Error("content "+r.status)); }),
    fetch("/api/docs/team", { headers: authHeaders() }).then(function(r){ return r.ok ? r.json() : []; }),
    fetch("/api/docs/live-stats", { headers: authHeaders() }).then(function(r){ return r.ok ? r.json() : {}; })
  ]).then(function(res){
    var content = res[0], team = res[1], stats = res[2];
    var pitch = content.pitch_deck || {};
    document.getElementById("tagline").textContent = pitch.tagline || "";
    var companyName = (pitch.company && pitch.company.indexOf("ContractMind") === -1) ? pitch.company : "Advotech Copilot";
    document.getElementById("company").textContent = companyName;
    document.getElementById("oneliner").textContent = pitch.one_liner || "";
    document.getElementById("content").innerHTML =
      renderPitch(pitch) + renderTech(content.tech_docs||{}) + renderRoadmap(content.roadmap||{}) + renderChangelog(content.changelog);
    renderTeam(team, pitch);
    renderStats(document.getElementById("statChips"), stats);
    var analytics = document.getElementById("liveAnalytics");
    analytics.style.display = "";
    renderStats(document.getElementById("statChips2"), stats);
    initMermaid();
  }).catch(function(e){
    document.getElementById("content").innerHTML =
      '<div class="card"><h2>Documentation temporarily unavailable</h2><p class="muted">'+esc(e.message)+' — please refresh.</p></div>';
  });
}
document.getElementById("refreshStats").onclick = function(){
  fetch("/api/docs/live-stats", { headers: authHeaders() })
    .then(function(r){ return r.ok ? r.json() : {}; })
    .then(function(stats){
      renderStats(document.getElementById("statChips2"), stats);
      renderStats(document.getElementById("statChips"), stats);
    });
};
loadAll();
})();
</script>
</body>
</html>
"""
