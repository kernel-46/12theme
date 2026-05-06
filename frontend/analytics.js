// Pratyaya — Civic Sensor analytics page

const PALETTE = ["#22d3ee","#14b8a6","#fb923c","#fbbf24","#a78bfa","#f87171","#34d399","#60a5fa","#f472b6","#facc15"];
let issueChart, urgencyChart, langChart, stateChart, sentHeatChart;

function $(s, r=document) { return r.querySelector(s); }
function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
function fmtPct(x) { return Math.round((x||0) * 100) + "%"; }
function toast(msg, kind="") {
  const root = $("#toast-stack");
  if (!root) return;
  const el = document.createElement("div");
  el.className = "toast " + (kind || "");
  el.textContent = msg;
  root.appendChild(el);
  setTimeout(() => el.remove(), 2500);
}
async function api(p) { const r = await fetch(p); if (!r.ok) throw new Error(p); return r.json(); }

async function refreshAll() {
  try {
    const [a, calls, audit] = await Promise.all([
      api("/api/analytics"),
      api("/api/calls?limit=50"),
      api("/api/audit/verify"),
    ]);
    renderMetrics(a, calls, audit);
    renderIssueChart(a.issue_types || {});
    renderUrgencyChart(a.urgency || {});
    renderLangChart(a.languages || {});
    renderDialectList(a.dialects || {});
    renderStateChart(a.states || {});
    renderLocationList(a.locations || {});
    renderSentHeat(a.recent_sentiment || []);
    renderRecentCalls(calls.calls || []);
    $("#last-updated").textContent = new Date().toLocaleTimeString();
  } catch (e) { console.error(e); toast("Refresh failed", "danger"); }
}

function renderMetrics(a, calls, audit) {
  $("#m-total").textContent = a.total_calls ?? (calls.calls?.length || 0);
  $("#m-live").textContent = calls.live_count ?? 0;
  $("#m-verified").textContent = fmtPct(a.verified_rate);
  $("#m-handover").textContent = fmtPct(a.handover_rate);
  $("#m-distress").textContent = (a.avg_distress != null) ? a.avg_distress.toFixed(2) : "—";
  $("#m-audit").innerHTML = audit.ok ?
    `<span class="hash-good">✓ intact</span>` :
    `<span class="hash-bad">✗ tampered</span>`;
  $("#m-audit-d").textContent = `${audit.rows} rows`;
}

function destroyChart(c) { try { c?.destroy(); } catch {} }

function renderIssueChart(d) {
  destroyChart(issueChart);
  const labels = Object.keys(d); const data = Object.values(d);
  issueChart = new Chart($("#issue-chart"), {
    type: "bar",
    data: { labels, datasets: [{ data, backgroundColor: PALETTE, borderRadius: 8 }] },
    options: {
      responsive: true, maintainAspectRatio: false, indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: { x: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(148,163,184,0.12)" } },
                y: { ticks: { color: "#94a3b8" }, grid: { display: false } } },
    },
  });
}
function renderUrgencyChart(d) {
  destroyChart(urgencyChart);
  const labels = ["low","normal","high","critical"];
  const data = labels.map(l => d[l] || 0);
  urgencyChart = new Chart($("#urgency-chart"), {
    type: "doughnut",
    data: { labels, datasets: [{ data, backgroundColor: ["#34d399","#22d3ee","#fbbf24","#f87171"], borderColor: "transparent" }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: "65%",
               plugins: { legend: { position: "bottom", labels: { color: "#94a3b8", font: { size: 11 } } } } },
  });
}
function renderLangChart(d) {
  destroyChart(langChart);
  const labels = Object.keys(d); const data = Object.values(d);
  langChart = new Chart($("#lang-chart"), {
    type: "polarArea",
    data: { labels, datasets: [{ data, backgroundColor: PALETTE.map(c => c + "99"), borderColor: PALETTE }] },
    options: { responsive: true, maintainAspectRatio: false,
               plugins: { legend: { position: "bottom", labels: { color: "#94a3b8", font: { size: 11 } } } },
               scales: { r: { ticks: { color: "#94a3b8", backdropColor: "transparent" }, grid: { color: "rgba(148,163,184,0.15)" } } } },
  });
}
function renderDialectList(d) {
  const root = $("#dialect-list");
  const total = Object.values(d).reduce((a,b) => a+b, 0) || 1;
  if (!Object.keys(d).length) {
    root.innerHTML = `<div class="placeholder">no data yet</div>`;
    return;
  }
  root.innerHTML = Object.entries(d).sort((a,b) => b[1]-a[1]).map(([k,v]) => `
    <div class="row">
      <div>
        <div class="name">${escapeHTML(k)}</div>
        <div class="meter" style="margin-top:4px;"><div class="fill" style="width:${(v/total*100).toFixed(0)}%;"></div></div>
      </div>
      <span class="count">${v} (${(v/total*100).toFixed(0)}%)</span>
      <span class="muted">·</span>
    </div>`).join("");
}
function renderStateChart(d) {
  destroyChart(stateChart);
  const labels = ["VERIFIED","CLARIFY","HANDOVER"];
  const data = labels.map(l => d[l] || 0);
  stateChart = new Chart($("#state-chart"), {
    type: "doughnut",
    data: { labels, datasets: [{ data, backgroundColor: ["#34d399","#fbbf24","#f87171"], borderColor: "transparent" }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: "60%",
               plugins: { legend: { position: "bottom", labels: { color: "#94a3b8", font: { size: 11 } } } } },
  });
}
function renderLocationList(d) {
  const root = $("#location-list");
  if (!Object.keys(d).length) { root.innerHTML = `<div class="placeholder">no locations reported yet</div>`; return; }
  const total = Object.values(d).reduce((a,b) => a+b, 0) || 1;
  root.innerHTML = Object.entries(d).sort((a,b) => b[1]-a[1]).slice(0, 10).map(([k,v]) => `
    <div class="row">
      <div>
        <div class="name">📍 ${escapeHTML(k)}</div>
        <div class="meter" style="margin-top:4px;"><div class="fill" style="width:${(v/total*100).toFixed(0)}%;"></div></div>
      </div>
      <span class="count">${v}</span>
      <span class="muted">·</span>
    </div>`).join("");
}
function renderSentHeat(rows) {
  destroyChart(sentHeatChart);
  if (!rows.length) {
    sentHeatChart = null;
    const ctx = $("#sent-heat-chart");
    if (ctx) ctx.getContext("2d").clearRect(0,0,ctx.width, ctx.height);
    return;
  }
  const dims = ["distress","urgency","anger","fear","confusion","calm"];
  const colors = { distress:"#f87171", urgency:"#fb923c", anger:"#fbbf24", fear:"#a78bfa", confusion:"#60a5fa", calm:"#34d399" };
  const labels = rows.map((_, i) => `t-${rows.length - 1 - i}`).reverse();
  const datasets = dims.map(d => ({
    label: d,
    data: rows.map(r => r[d] || 0),
    borderColor: colors[d], backgroundColor: colors[d] + "44",
    fill: true, tension: 0.35, borderWidth: 1.5, pointRadius: 0,
  }));
  sentHeatChart = new Chart($("#sent-heat-chart"), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { x: { ticks: { color: "#94a3b8", font: { size: 10 } } },
                y: { min: 0, max: 1, ticks: { color: "#94a3b8", stepSize: 0.25 },
                     grid: { color: "rgba(148,163,184,0.12)" } } },
      plugins: { legend: { position: "bottom", labels: { color: "#94a3b8", boxWidth: 10, padding: 8, font: { size: 11 } } },
                 tooltip: { mode: "index", intersect: false } },
    },
  });
}
function renderRecentCalls(rows) {
  const el = $("#recent-calls");
  if (!rows.length) { el.innerHTML = `<div class="placeholder">no calls yet</div>`; return; }
  el.innerHTML = rows.slice(0, 25).map(c => `
    <div class="audit-row">
      <span class="seq mono">${escapeHTML(c.call_id || "")}</span>
      <span>${c.live ? '<span class="tag green">LIVE</span>' : '<span class="tag">ended</span>'}
            ${c.final_state ? `<span class="state-pill ${escapeHTML(c.final_state)}">${escapeHTML(c.final_state)}</span>` : ""}</span>
      <div>
        <div style="font-size:13px;">${escapeHTML(c.summary || "(no summary)")}</div>
        <div style="font-size:11px; color:var(--muted); margin-top:2px;">
          ${(c.started_at || "").replace("T"," ").slice(0,19)}
          ${c.citizen_lang_pref ? ` · ${escapeHTML(c.citizen_lang_pref)}` : ""}
          ${c.agent_id ? ` · ${escapeHTML(c.agent_id)}` : ""}
        </div>
      </div>
    </div>`).join("");
}

window.refreshAll = refreshAll;
refreshAll();
setInterval(refreshAll, 6000);
