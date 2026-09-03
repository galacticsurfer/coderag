"""Self-contained observability dashboard (served at GET /dashboard).

Read-only view over persisted telemetry: what was queried and how many tokens the
budgeted context saved versus the retrieved candidate set. No external assets
(CSP-safe), no template engine — the page fetches /metrics and /queries as JSON.
Colours use the validated data-viz palette (blue = context sent to the LLM,
green = tokens saved; neutral track), theme-aware for light/dark.
"""

from __future__ import annotations

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CodeRAG · Token Dashboard</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A//www.w3.org/2000/svg%22%20viewBox%3D%220%200%2064%2064%22%20role%3D%22img%22%20aria-label%3D%22CodeRAG%20logo%22%3E%20%20%3Cpath%20d%3D%22M23%2015%20L9%2032%20L23%2049%22%20stroke%3D%22%232a78d6%22%20stroke-width%3D%228%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%20fill%3D%22none%22/%3E%20%20%3Cpath%20d%3D%22M41%2015%20L55%2032%20L41%2049%22%20stroke%3D%22%232a78d6%22%20stroke-width%3D%228%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%20fill%3D%22none%22/%3E%20%20%3Ccircle%20cx%3D%2232%22%20cy%3D%2232%22%20r%3D%227.5%22%20fill%3D%22%230ca30c%22/%3E%3C/svg%3E">
<style>
  :root{
    color-scheme: light;
    --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
    --grid:#e1e0d9; --ring:rgba(11,11,11,.10);
    --context:#2a78d6; --saved:#0ca30c; --saved-ink:#006300; --track:#e1e0d9;
    --shadow:0 1px 2px rgba(11,11,11,.05),0 4px 16px rgba(11,11,11,.06);
  }
  @media (prefers-color-scheme:dark){ :root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --ring:rgba(255,255,255,.10);
    --context:#3987e5; --saved:#0ca30c; --saved-ink:#0ca30c; --track:#2c2c2a;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 16px rgba(0,0,0,.5);
  }}
  :root[data-theme="dark"]{
    color-scheme:dark;
    --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --ring:rgba(255,255,255,.10);
    --context:#3987e5; --saved:#0ca30c; --saved-ink:#0ca30c; --track:#2c2c2a;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 16px rgba(0,0,0,.5);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--plane);color:var(--ink);
    font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.45}
  .wrap{max-width:1180px;margin:0 auto;padding:28px 20px 64px}
  header{display:flex;align-items:baseline;justify-content:space-between;gap:16px;
    flex-wrap:wrap;margin-bottom:22px}
  h1{font-size:20px;margin:0;letter-spacing:-.01em}
  h1 .dot{color:var(--context)}
  .sub{color:var(--ink2);font-size:13px;margin-top:3px}
  .controls{display:flex;gap:8px;align-items:center}
  button{font:inherit;font-size:13px;color:var(--ink2);background:var(--surface);
    border:1px solid var(--ring);border-radius:8px;padding:6px 12px;cursor:pointer}
  button:hover{color:var(--ink);border-color:var(--muted)}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;
    margin-bottom:26px}
  .tile{background:var(--surface);border:1px solid var(--ring);border-radius:12px;
    padding:14px 16px;box-shadow:var(--shadow)}
  .tile .label{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
  .tile .val{font-size:26px;margin-top:6px;font-variant-numeric:tabular-nums;
    letter-spacing:-.02em}
  .tile .unit{font-size:13px;color:var(--ink2);margin-left:3px}
  .tile.hero .val{color:var(--saved-ink)}
  .card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;
    padding:18px 18px 8px;box-shadow:var(--shadow);margin-bottom:24px}
  .card h2{font-size:14px;margin:0 0 2px}
  .card .hint{font-size:12px;color:var(--muted);margin:0 0 14px}
  .legend{display:flex;gap:16px;font-size:12px;color:var(--ink2);margin:0 0 14px}
  .legend i{display:inline-block;width:11px;height:11px;border-radius:3px;
    margin-right:6px;vertical-align:middle}
  .bars{display:flex;flex-direction:column;gap:9px}
  .bar-row{display:grid;grid-template-columns:190px 1fr 74px;gap:12px;align-items:center}
  .bar-lbl{font-size:12px;color:var(--ink2);white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis}
  .bar-lbl b{color:var(--ink);font-weight:600;font-size:10px;text-transform:uppercase;
    letter-spacing:.04em;margin-right:6px}
  .track{height:18px;background:var(--track);border-radius:5px;display:flex;overflow:hidden}
  .seg{height:100%}
  .seg.context{background:var(--context)}
  .seg.saved{background:var(--saved);margin-left:2px}
  .seg:first-child{border-radius:5px 0 0 5px}
  .seg:last-child{border-radius:0 5px 5px 0}
  .bar-red{font-size:12px;font-variant-numeric:tabular-nums;color:var(--saved-ink);
    text-align:right;font-weight:600}
  .tablewrap{overflow-x:auto}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  th,td{padding:9px 10px;text-align:right;white-space:nowrap;
    border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums}
  th{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:10.5px;
    letter-spacing:.05em;position:sticky;top:0;background:var(--surface)}
  td.l,th.l{text-align:left}
  td.q{max-width:340px;overflow:hidden;text-overflow:ellipsis;color:var(--ink2)}
  .badge{font-size:10px;text-transform:uppercase;letter-spacing:.04em;padding:2px 7px;
    border-radius:999px;border:1px solid var(--ring);color:var(--ink2)}
  .badge.ask{color:var(--context);border-color:var(--context)}
  .empty{text-align:center;color:var(--muted);padding:48px 0;font-size:14px}
  .foot{color:var(--muted);font-size:12px;margin-top:8px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1><svg viewBox="0 0 64 64" width="22" height="22" style="vertical-align:-4px;margin-right:7px" aria-hidden="true"><path d="M23 15 L9 32 L23 49" stroke="#2a78d6" stroke-width="8" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M41 15 L55 32 L41 49" stroke="#2a78d6" stroke-width="8" stroke-linecap="round" stroke-linejoin="round" fill="none"/><circle cx="32" cy="32" r="7.5" fill="#0ca30c"/></svg>CodeRAG<span class="dot">.</span> Token Dashboard</h1>
      <div class="sub">What was queried, and how much context was saved before hitting the LLM.</div>
    </div>
    <div class="controls">
      <button id="refresh">Refresh</button>
      <button id="theme">◐ Theme</button>
    </div>
  </header>

  <section class="kpis" id="kpis"></section>
  <p style="font-size:12px;color:var(--muted);margin:-14px 0 22px">Dollar figures are estimates at the configured per-million prices (CODERAG_PRICE_INPUT_PER_MTOK / _OUTPUT_PER_MTOK) — not billing data. On a flat-rate Claude plan, savings show up as headroom rather than a refund.</p>

  <section class="card">
    <h2>Context vs. saved — recent queries</h2>
    <p class="hint">Bar length = retrieved candidate tokens. Blue = context actually sent; green = tokens saved by dedup + budgeting.</p>
    <div class="legend">
      <span><i style="background:var(--context)"></i>Context sent to LLM</span>
      <span><i style="background:var(--saved)"></i>Tokens saved</span>
    </div>
    <div class="bars" id="bars"></div>
  </section>

  <section class="card">
    <h2>Doctor — where the money goes</h2>
    <p class="hint">Attribution of observed LLM traffic (via <code>coderag proxy</code>) and the levers ranked by estimated impact. Estimates at published per-model prices, not billing data.</p>
    <div id="doctor" class="empty">No observed traffic yet — run <b>coderag proxy</b> and point your agent at it.</div>
  </section>

  <section class="card">
    <h2>Query log</h2>
    <p class="hint">Most recent first. Saved = candidate tokens − context tokens.</p>
    <div class="tablewrap">
      <table>
        <thead><tr>
          <th class="l">#</th><th class="l">when</th><th class="l">mode</th><!-- 17 cols -->
          <th class="l">repo</th><th class="l">query</th>
          <th>found</th><th>selected</th><th>candidate</th><th>context</th>
          <th>saved</th><th>reduction</th>
          <th>whole files</th><th>saved vs files</th><th>vs files</th>
          <th>retr ms</th><th>llm in</th><th>llm out</th>
        </tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div class="foot" id="foot"></div>
  </section>
</div>

<script>
const n = x => (x==null? "—" : Number(x).toLocaleString());
const pct = x => (x==null? "—" : Number(x).toFixed(1) + "%");
const usd = x => (x==null? "—" : "$" + Number(x).toLocaleString(undefined,{minimumFractionDigits:2, maximumFractionDigits:2}));
const esc = s => (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function tile(label, val, unit, hero){
  return `<div class="tile ${hero?'hero':''}"><div class="label">${label}</div>`+
         `<div class="val">${val}${unit?`<span class="unit">${unit}</span>`:''}</div></div>`;
}

function renderKpis(m){
  document.getElementById('kpis').innerHTML =
    tile('Saved vs reading files', n(m.total_saved_vs_files), 'tok', true) +
    tile('Est. $ saved', usd(m.cost_saved_vs_files_usd), '', true) +
    tile('Reduction vs files', pct(m.reduction_vs_files_percent), '', true) +
    tile('Est. $ context sent', usd(m.cost_context_sent_usd)) +
    tile('Est. $ LLM spend', usd(m.cost_llm_usd)) +
    tile('Whole-file baseline', n(m.total_baseline_tokens), 'tok') +
    tile('Tokens saved (budgeting)', n(m.total_tokens_saved)) +
    tile('Overall reduction', pct(m.avg_token_reduction_percent)) +
    tile('Queries', n(m.queries)) +
    tile('Context sent', n(m.total_context_tokens), 'tok') +
    tile('Candidate tokens', n(m.total_candidate_tokens), 'tok') +
    tile('Avg retrieval', n(m.avg_retrieval_latency_ms), 'ms') +
    tile('LLM input', n(m.total_llm_input_tokens), 'tok') +
    tile('LLM output', n(m.total_llm_output_tokens), 'tok');
}

function renderBars(rows){
  const el = document.getElementById('bars');
  const shown = rows.slice(0, 16);
  if(!shown.length){ el.innerHTML = '<div class="empty">No queries yet — run a search, context, or ask.</div>'; return; }
  const max = Math.max(...shown.map(r => r.candidate_tokens || 0), 1);
  el.innerHTML = shown.map(r => {
    const total = (r.candidate_tokens/max)*100;
    const ctx = r.candidate_tokens ? (r.context_tokens/r.candidate_tokens) : 0;
    const cw = total*ctx, sw = total*(1-ctx);
    const title = `${esc(r.query)}\ncandidate ${n(r.candidate_tokens)} · context ${n(r.context_tokens)} · saved ${n(r.tokens_saved)} (${pct(r.reduction_percent)})`;
    return `<div class="bar-row" title="${title}">`+
      `<div class="bar-lbl"><b>${r.mode}</b>${esc(r.query)}</div>`+
      `<div class="track">`+
        `<div class="seg context" style="width:${cw}%"></div>`+
        `<div class="seg saved" style="width:${sw}%"></div>`+
      `</div>`+
      `<div class="bar-red">${pct(r.reduction_percent)}</div>`+
    `</div>`;
  }).join('');
}

function renderTable(rows){
  const tb = document.getElementById('rows');
  if(!rows.length){ tb.innerHTML = '<tr><td class="l empty" colspan="17">No queries recorded yet.</td></tr>'; return; }
  tb.innerHTML = rows.map(r => {
    const when = r.created_at ? new Date(r.created_at).toLocaleString() : '—';
    return `<tr>`+
      `<td class="l">${r.id}</td>`+
      `<td class="l">${esc(when)}</td>`+
      `<td class="l"><span class="badge ${r.mode==='ask'?'ask':''}">${esc(r.mode)}</span></td>`+
      `<td class="l">${esc(r.repository)}</td>`+
      `<td class="l q" title="${esc(r.query)}">${esc(r.query)}</td>`+
      `<td>${n(r.candidates_found)}</td><td>${n(r.candidates_selected)}</td>`+
      `<td>${n(r.candidate_tokens)}</td><td>${n(r.context_tokens)}</td>`+
      `<td style="color:var(--saved-ink);font-weight:600">${n(r.tokens_saved)}</td>`+
      `<td>${pct(r.reduction_percent)}</td>`+
      `<td>${r.baseline_tokens? n(r.baseline_tokens)+' ('+r.baseline_files+'f)' : '—'}</td>`+
      `<td style="color:var(--saved-ink);font-weight:600">${r.baseline_tokens? n(r.saved_vs_files):'—'}</td>`+
      `<td>${r.baseline_tokens? pct(r.reduction_vs_files):'—'}</td>`+
      `<td>${n(r.retrieval_latency_ms)}</td>`+
      `<td>${n(r.llm_input_tokens)}</td><td>${n(r.llm_output_tokens)}</td>`+
    `</tr>`;
  }).join('');
  document.getElementById('foot').textContent = `Showing ${rows.length} most recent queries.`;
}

function renderDoctor(d){
  const el = document.getElementById('doctor');
  const b = d.breakdown;
  if(!b.requests){ return; }
  el.classList.remove('empty');
  const cat = [
    ['fresh input', b.fresh_input_tokens, b.fresh_input_usd],
    ['cache reads', b.cache_read_tokens, b.cache_read_usd],
    ['cache writes', b.cache_write_tokens, b.cache_write_usd],
    ['output', b.output_tokens, b.output_usd],
  ];
  const maxUsd = Math.max(...cat.map(c=>c[2]), 1e-9);
  let html = '<div class="bars">' + cat.map(([name,tok,usdv]) =>
    `<div class="bar-row"><div class="bar-lbl">${name}</div>`+
    `<div class="track"><div class="seg context" style="width:${100*usdv/maxUsd}%"></div></div>`+
    `<div class="bar-red">${usd(usdv)}</div></div>`).join('') + '</div>';
  html += `<p class="hint" style="margin-top:10px">total ${usd(b.total_usd)} over ${n(b.requests)} requests · cache hit rate ${(100*b.cache_hit_rate).toFixed(0)}%</p>`;
  if(d.models && d.models.length > 1){
    html += '<p class="hint">by model: ' + d.models.map(m =>
      `${esc(m.model)} ${usd(m.est_usd)} (${n(m.requests)} req)`).join(' · ') + '</p>';
  }
  const ce = d.compression;
  if(ce && ce.requests_compressed){
    html += `<p class="hint">--compress (measured): saved ${n(ce.est_tokens_saved)} tokens (~${usd(ce.est_usd_saved)}) across ${n(ce.requests_compressed)} compressed requests</p>`;
  }
  const cape = d.cap_effect;
  if(cape && cape.measured_reduction != null && cape.active_requests){
    const pct = Math.abs(100*cape.measured_reduction).toFixed(0);
    const dir = cape.measured_reduction >= 0 ? 'less' : 'more';
    html += `<p class="hint">output caps (measured): ${n(Math.round(cape.avg_output_inactive))} → ${n(Math.round(cape.avg_output_active))} avg output tokens/request (${pct}% ${dir}; ${n(cape.active_requests)} capped / ${n(cape.inactive_requests)} uncapped; observational)</p>`;
  }
  const rt = d.routing;
  if(rt && rt.routed_requests){
    html += `<p class="hint">routing savings (measured): <b>${usd(rt.saved_usd)}</b> across ${n(rt.routed_requests)} routed requests</p>`;
  }
  const se = d.skill_effect;
  if(se && se.measured_reduction != null){
    const pct = Math.abs(100*se.measured_reduction).toFixed(0);
    const dir = se.measured_reduction >= 0 ? 'less' : 'more';
    html += `<p class="hint">/token-lean effect (measured): ${n(Math.round(se.avg_output_inactive))} → ${n(Math.round(se.avg_output_active))} avg output tokens/request (${pct}% ${dir}; ${n(se.active_requests)} on / ${n(se.inactive_requests)} off; observational)</p>`;
  }
  if(d.diagnoses.length){
    html += '<ol style="font-size:12.5px;padding-left:18px;margin:8px 0">' + d.diagnoses.map(x =>
      `<li style="margin-bottom:8px"><b>${esc(x.title)}</b>`+
      (x.est_saving_usd ? ` <span style="color:var(--saved-ink);font-weight:600">est. ${usd(x.est_saving_usd)}</span>` : '')+
      `<br><span style="color:var(--ink2)">${esc(x.evidence)}</span>`+
      `<br>→ ${esc(x.action)}`+
      `<br><span style="color:var(--muted)">${esc(x.assumption)}</span></li>`).join('') + '</ol>';
  } else {
    html += '<p class="hint">No obvious waste found in the observed window.</p>';
  }
  el.innerHTML = html;
}

async function load(){
  try{
    const [m, q, doc] = await Promise.all([
      fetch('metrics').then(r=>r.json()),
      fetch('queries?limit=200').then(r=>r.json()),
      fetch('doctor').then(r=>r.json()),
    ]);
    renderKpis(m); renderBars(q); renderTable(q); renderDoctor(doc);
  }catch(e){
    document.getElementById('kpis').innerHTML =
      '<div class="empty">Could not reach the API. Is the CodeRAG server running?</div>';
  }
}

document.getElementById('refresh').onclick = load;
document.getElementById('theme').onclick = () => {
  const cur = document.documentElement.dataset.theme;
  document.documentElement.dataset.theme = cur === 'dark' ? 'light' : (cur === 'light' ? 'dark' : 'light');
};
load();
setInterval(load, 15000);
</script>
</body>
</html>
"""
