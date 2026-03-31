#!/usr/bin/env python3
"""
web_app.py — FastAPI live dashboard for Frank.

Usage:
    python web_app.py                     # most recent session, port 8765
    python web_app.py --session <slug>    # specific session
    python web_app.py --port 9000         # custom port
"""

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import config
import sessions as session_manager

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
    import uvicorn
except ImportError:
    print("ERROR: fastapi and uvicorn required. Run: pip install fastapi uvicorn[standard]")
    sys.exit(1)

app = FastAPI(title="Frank Research Dashboard", docs_url=None, redoc_url=None)

# ── HTML template ──────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FRANK — Research Dashboard</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
    --cyan: #00bcd4; --cyan2: #006978;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --text: #c9d1d9; --dim: #6e7681;
    --border: #30363d;
  }
  body { background: var(--bg); color: var(--text); font-family: 'Cascadia Code', 'Fira Code', 'Courier New', monospace; font-size: 13px; }
  header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 10px 20px; display: flex; align-items: center; justify-content: space-between; }
  header h1 { color: var(--cyan); font-size: 15px; letter-spacing: 2px; }
  header .brand { color: var(--dim); font-size: 11px; }
  header .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--green); margin-right: 6px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
  .container { max-width: 1400px; margin: 0 auto; padding: 16px; }
  .stats-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
  .stat-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; padding: 14px 16px; }
  .stat-card .label { color: var(--dim); font-size: 11px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }
  .stat-card .value { color: var(--cyan); font-size: 24px; font-weight: bold; }
  .progress-bar { background: var(--bg3); border-radius: 3px; height: 6px; margin-top: 8px; }
  .progress-fill { background: var(--cyan); height: 100%; border-radius: 3px; transition: width 0.5s; }
  .grid2 { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; margin-bottom: 16px; }
  .panel { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .panel-header { background: var(--bg3); padding: 8px 14px; font-size: 11px; color: var(--cyan); text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
  .panel-body { padding: 12px; overflow-y: auto; max-height: 420px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; color: var(--dim); font-size: 11px; padding: 4px 8px; border-bottom: 1px solid var(--border); font-weight: normal; }
  td { padding: 5px 8px; border-bottom: 1px solid var(--bg3); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--bg3); }
  .conf-high { color: var(--green); font-weight: bold; }
  .conf-med  { color: var(--yellow); }
  .conf-low  { color: var(--red); opacity: 0.8; }
  .conf-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; }
  .search-bar { width: 100%; background: var(--bg3); border: 1px solid var(--border); border-radius: 4px; color: var(--text); padding: 6px 10px; font-family: inherit; font-size: 12px; margin-bottom: 8px; }
  .search-bar:focus { outline: none; border-color: var(--cyan); }
  .queue-item { display: flex; align-items: center; padding: 5px 0; border-bottom: 1px solid var(--bg3); gap: 8px; }
  .queue-item:last-child { border-bottom: none; }
  .pri { display: inline-block; width: 20px; height: 20px; border-radius: 3px; text-align: center; line-height: 20px; font-size: 11px; font-weight: bold; }
  .pri-1 { background: #1a3a1a; color: var(--green); }
  .pri-2 { background: #1a2a1a; color: #52a052; }
  .pri-3 { background: #2a2a10; color: var(--yellow); }
  .pri-4 { background: var(--bg3); color: var(--dim); }
  #graph { width: 100%; height: 420px; }
  .node circle { stroke-width: 1.5px; cursor: pointer; }
  .node text { font-size: 10px; fill: var(--text); pointer-events: none; }
  .link { stroke: var(--border); stroke-opacity: 0.6; }
  .last-updated { color: var(--dim); font-size: 11px; }
  .verified { color: var(--green); }
</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; FRANK &mdash; Autonomous Research Dashboard</h1>
    <div class="brand">SIK FUK ENTERPRISES</div>
  </div>
  <div style="text-align:right">
    <span class="dot" id="dot"></span>
    <span id="seed-topic" style="color:var(--cyan)"></span><br>
    <span class="last-updated" id="last-updated"></span>
  </div>
</header>

<div class="container">
  <div class="stats-row">
    <div class="stat-card">
      <div class="label">Topics Researched</div>
      <div class="value" id="stat-topics">—</div>
      <div class="progress-bar"><div class="progress-fill" id="prog-bar" style="width:0%"></div></div>
    </div>
    <div class="stat-card">
      <div class="label">Total Facts</div>
      <div class="value" id="stat-facts">—</div>
    </div>
    <div class="stat-card">
      <div class="label">Queue Size</div>
      <div class="value" id="stat-queue">—</div>
    </div>
    <div class="stat-card">
      <div class="label">Objective Coverage</div>
      <div class="value" id="stat-coverage">—</div>
    </div>
  </div>

  <div class="grid2">
    <div class="panel">
      <div class="panel-header">
        <span>Facts</span>
        <input class="search-bar" id="fact-search" placeholder="Search facts..." style="width:200px;margin:0">
      </div>
      <div class="panel-body">
        <table id="facts-table">
          <thead><tr><th>Topic</th><th>Conf</th><th>Fact</th><th>Src</th></tr></thead>
          <tbody id="facts-body"></tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header"><span>Queue</span><span id="queue-count" style="color:var(--dim)"></span></div>
      <div class="panel-body" id="queue-body"></div>
    </div>
  </div>

  <div class="panel">
    <div class="panel-header"><span>Topic Graph</span><span style="color:var(--dim);font-size:11px">nodes sized by fact count</span></div>
    <svg id="graph"></svg>
  </div>
</div>

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
let allFacts = [];

async function loadStats() {
  const r = await fetch('/api/stats');
  const d = await r.json();
  document.getElementById('stat-topics').textContent = d.topics_researched ?? '—';
  document.getElementById('stat-facts').textContent = d.total_facts ?? '—';
  document.getElementById('stat-queue').textContent = d.queue_size ?? '—';
  document.getElementById('stat-coverage').textContent = d.objective_coverage != null ? (d.objective_coverage*100).toFixed(0)+'%' : '—';
  document.getElementById('seed-topic').textContent = d.seed_topic ?? '';
  document.getElementById('last-updated').textContent = 'Updated: ' + (d.last_updated ?? '').substring(0,19);
  const done = d.topics_researched ?? 0;
  const total = done + (d.queue_size ?? 0);
  const pct = total > 0 ? (done/total*100).toFixed(1) : 0;
  document.getElementById('prog-bar').style.width = pct + '%';
}

async function loadFacts() {
  const r = await fetch('/api/facts');
  allFacts = await r.json();
  renderFacts(allFacts);
}

function renderFacts(facts) {
  const tbody = document.getElementById('facts-body');
  const q = document.getElementById('fact-search').value.toLowerCase();
  const filtered = q ? facts.filter(f => (f.content+f.topic).toLowerCase().includes(q)) : facts;
  tbody.innerHTML = filtered.slice(0, 200).map(f => {
    const cls = f.confidence === 'high' ? 'conf-high' : f.confidence === 'medium' ? 'conf-med' : 'conf-low';
    const badge = `<span class="conf-badge ${cls}">${(f.confidence||'?')[0].toUpperCase()}</span>`;
    const sc = f.source_count > 1 ? ` <span style="color:var(--cyan);font-size:10px">×${f.source_count}</span>` : '';
    const verified = f.hardware_verified ? ' <span class="verified">✅</span>' : '';
    const src = f.source_url ? `<a href="${f.source_url}" target="_blank" style="color:var(--dim);font-size:10px">↗</a>` : '';
    return `<tr><td style="color:var(--cyan);white-space:nowrap;max-width:140px;overflow:hidden;text-overflow:ellipsis">${f.topic}</td><td>${badge}${sc}</td><td>${f.content}${verified}</td><td>${src}</td></tr>`;
  }).join('');
}

async function loadQueue() {
  const r = await fetch('/api/queue');
  const items = await r.json();
  document.getElementById('queue-count').textContent = items.length + ' topics';
  const priClass = {1:'pri-1',2:'pri-2',3:'pri-3',4:'pri-4'};
  document.getElementById('queue-body').innerHTML = items.slice(0,50).map(item =>
    `<div class="queue-item"><span class="pri ${priClass[item.priority]||'pri-4'}">${item.priority}</span><span>${item.topic}</span></div>`
  ).join('');
}

let graphLoaded = false;
async function loadGraph() {
  const r = await fetch('/api/graph');
  const {nodes, edges} = await r.json();
  if (!nodes.length) return;

  const svg = d3.select('#graph');
  const W = document.getElementById('graph').clientWidth || 1000;
  const H = 420;
  svg.attr('viewBox', `0 0 ${W} ${H}`);
  svg.selectAll('*').remove();

  const maxFacts = d3.max(nodes, d => d.facts_count) || 1;
  const r_scale = d3.scaleSqrt().domain([0, maxFacts]).range([4, 22]);

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(edges).id(d => d.id).distance(80))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(W/2, H/2))
    .force('collision', d3.forceCollide().radius(d => r_scale(d.facts_count) + 4));

  const link = svg.append('g').selectAll('line').data(edges).join('line').attr('class','link');

  const node = svg.append('g').selectAll('g').data(nodes).join('g').attr('class','node')
    .call(d3.drag()
      .on('start', (e,d) => { if(!e.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; })
      .on('drag',  (e,d) => { d.fx=e.x; d.fy=e.y; })
      .on('end',   (e,d) => { if(!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }));

  node.append('circle')
    .attr('r', d => r_scale(d.facts_count))
    .attr('fill', '#006978')
    .attr('stroke', '#00bcd4');

  node.append('text')
    .attr('dy', d => r_scale(d.facts_count) + 12)
    .attr('text-anchor','middle')
    .text(d => d.label.length > 18 ? d.label.substring(0,16)+'…' : d.label);

  sim.on('tick', () => {
    link.attr('x1', d=>d.source.x).attr('y1', d=>d.source.y)
        .attr('x2', d=>d.target.x).attr('y2', d=>d.target.y);
    node.attr('transform', d=>`translate(${d.x},${d.y})`);
  });
  graphLoaded = true;
}

document.getElementById('fact-search').addEventListener('input', () => renderFacts(allFacts));

// SSE for live updates
const evtSource = new EventSource('/events');
evtSource.onmessage = e => {
  const d = JSON.parse(e.data);
  document.getElementById('stat-topics').textContent = d.topics_researched ?? '—';
  document.getElementById('stat-facts').textContent = d.total_facts ?? '—';
  document.getElementById('stat-queue').textContent = d.queue_size ?? '—';
  document.getElementById('dot').style.background = '#3fb950';
  setTimeout(() => document.getElementById('dot').style.background = '', 500);
};
evtSource.onerror = () => {
  document.getElementById('dot').style.background = '#f85149';
};

async function refresh() {
  await Promise.all([loadStats(), loadFacts(), loadQueue()]);
  if (!graphLoaded) await loadGraph();
}

refresh();
setInterval(refresh, 10000);
setInterval(loadGraph, 30000);
</script>
</body>
</html>"""


# ── Data helpers ───────────────────────────────────────────────────────────────

def _read_kb() -> dict:
    try:
        with open(config.KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


@app.get("/api/stats")
async def api_stats():
    data = _read_kb()
    meta = data.get("meta", {})
    objectives = data.get("objectives", [])
    cov = 0.0
    if objectives:
        cov = sum(o.get("score", 0.0) for o in objectives) / len(objectives)
    return JSONResponse({
        "topics_researched": meta.get("topics_researched", 0),
        "total_facts":       meta.get("total_facts", 0),
        "queue_size":        len(data.get("research_queue", [])),
        "seed_topic":        meta.get("seed_topic", ""),
        "last_updated":      meta.get("last_updated", ""),
        "objective_coverage": round(cov, 3),
    })


@app.get("/api/facts")
async def api_facts():
    data = _read_kb()
    facts = []
    for key, entry in data.get("knowledge", {}).items():
        for f in entry.get("facts", []):
            facts.append({
                "topic":             key,
                "content":           f.get("content", ""),
                "confidence":        f.get("confidence", ""),
                "source_count":      f.get("source_count", 1),
                "source_url":        f.get("source_url", ""),
                "hardware_verified": f.get("hardware_verified", False),
            })
    # Sort: high confidence first
    _ord = {"high": 0, "medium": 1, "low": 2}
    facts.sort(key=lambda x: _ord.get(x["confidence"], 3))
    return JSONResponse(facts)


@app.get("/api/queue")
async def api_queue():
    data = _read_kb()
    return JSONResponse([
        {"topic": item.get("topic", ""), "priority": item.get("priority", 2)}
        for item in data.get("research_queue", [])
    ])


@app.get("/api/graph")
async def api_graph():
    data = _read_kb()
    nodes = []
    edges = []
    seen_edges = set()
    for key, entry in data.get("knowledge", {}).items():
        nodes.append({"id": key, "label": key, "facts_count": len(entry.get("facts", []))})
        for rel in entry.get("related_topics", []):
            if rel:
                ek = (key, rel)
                if ek not in seen_edges:
                    seen_edges.add(ek)
                    edges.append({"source": key, "target": rel})
    return JSONResponse({"nodes": nodes, "edges": edges})


@app.get("/events")
async def events():
    async def stream():
        while True:
            data = _read_kb()
            meta = data.get("meta", {})
            payload = {
                "topics_researched": meta.get("topics_researched", 0),
                "total_facts":       meta.get("total_facts", 0),
                "queue_size":        len(data.get("research_queue", [])),
            }
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(3)
    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Frank web dashboard")
    parser.add_argument("--session", metavar="SLUG", help="Session slug")
    parser.add_argument("--port", type=int, default=config.WEB_DASHBOARD_PORT)
    args = parser.parse_args()

    existing = session_manager.list_sessions()
    if args.session:
        match = next((s for s in existing if s["slug"] == args.session or s["name"] == args.session), None)
        if match:
            session_manager.activate_session(match)
    elif existing:
        session_manager.activate_session(existing[0])  # most recent

    print(f"Frank dashboard → http://localhost:{args.port}")
    print(f"Reading: {config.KNOWLEDGE_FILE}")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
