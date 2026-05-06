// Pratyaya — Agent dashboard controller
// Live-streaming a single active call: turns, sentiment trajectory chart,
// audit ledger, edits, shortcuts, shadow mode, distress alerts, export.

const SENTIMENT_DIMS = ["distress", "urgency", "anger", "fear", "confusion", "calm"];
// Palette aligned with the government v2 theme — warm earth tones, jade calm.
// No blue for "confusion" (was #60a5fa). Replaced with warm plum.
const SENT_COLORS = {
  distress:  "#d44a4a",
  urgency:   "#e07a3a",
  anger:     "#c98c2a",
  fear:      "#a87bb1",
  confusion: "#7d6e57",
  calm:      "#2fa48a",
};
const DISTRESS_ALERT_THRESHOLD = 0.75;

let currentCallId = null;
let globalWS = null;
let callWS = null;
let lastTurn = null;
let sentimentTrend = [];
let sentChart = null;
let sentSeries = { labels: [], data: {} };
let alertedThisCall = false;
let timerInterval = null;
let callStartedAt = null;
let shadowMode = false;

function $(sel, root = document) { return root.querySelector(sel); }
function $$(sel, root = document) { return [...root.querySelectorAll(sel)]; }

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
function fmtPct(x) {
  if (x === null || x === undefined || isNaN(x)) return "—";
  return Math.round(x * 100) + "%";
}
function fmtClock(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2,"0")}:${String(r).padStart(2,"0")}`;
}
function meter(rootSel, value) {
  const root = $(rootSel);
  if (!root) return;
  const fill = root.querySelector(".fill");
  const v = Math.max(0, Math.min(1, value || 0));
  fill.style.width = (v * 100) + "%";
  root.classList.toggle("warn", v >= 0.45 && v < 0.7);
  root.classList.toggle("danger", v < 0.45);
}

function toast(msg, kind = "") {
  const root = $("#toast-stack");
  if (!root) return;
  const el = document.createElement("div");
  el.className = "toast " + (kind || "");
  el.textContent = msg;
  root.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateY(8px)"; }, 2400);
  setTimeout(() => el.remove(), 2800);
}

async function api(path, opts = {}) {
  if (!opts.headers && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error("api " + r.status + " " + path);
  return r.json();
}

// ----- health -----
async function health() {
  try {
    const h = await api("/api/health");
    const el = $("#health-line");
    if (el) el.textContent = h.groq_configured ? "online · v" + h.version : "online · ⚠ no Groq key";
  } catch { const el = $("#health-line"); if (el) el.textContent = "offline"; }
}

// ----- calls list -----
// The Live-calls panel was removed from the dashboard. We keep this function
// as a soft no-op that auto-selects the most recent live call, so the rest
// of the dashboard stays driven by the active call without the visible list.
async function refreshCalls() {
  const liveCountEl = document.getElementById("live-count");
  const listEl = document.getElementById("calls-list");
  try {
    const data = await api("/api/calls");
    if (liveCountEl) liveCountEl.textContent = data.live_count;
    if (listEl) {
      if (!data.calls.length) {
        listEl.innerHTML = `<div class="placeholder">No calls yet. Open the citizen demo and start one.</div>`;
      } else {
        listEl.innerHTML = data.calls.slice(0, 20).map(c => `
          <div class="call-card ${c.call_id === currentCallId ? "active" : ""}" data-id="${c.call_id}" role="listitem" tabindex="0">
            <div class="id">${escapeHTML(c.call_id)}</div>
            <div class="meta">
              ${c.live ? '<span class="tag green"><span class="dot red"></span>LIVE</span>' :
                           '<span class="tag">ended</span>'}
              ${c.final_state ? `<span class="state-pill ${escapeHTML(c.final_state)}">${escapeHTML(c.final_state)}</span>` : ""}
              <span class="tag">${escapeHTML(c.citizen_lang_pref || "auto")}</span>
            </div>
          </div>
        `).join("");
        document.querySelectorAll("#calls-list .call-card").forEach(el => {
          el.addEventListener("click", () => selectCall(el.dataset.id));
          el.addEventListener("keydown", (e) => { if (e.key === "Enter") selectCall(el.dataset.id); });
        });
      }
    }
    // With the list gone, auto-select the most recent live call so the
    // dashboard always shows something useful.
    if (!currentCallId || !(data.calls || []).some(c => c.call_id === currentCallId && c.live)) {
      const live = (data.calls || []).find(c => c.live);
      if (live && live.call_id !== currentCallId) {
        try { selectCall(live.call_id); } catch {}
      }
    }
  } catch (e) { console.error(e); }
}

async function refreshStats() {
  try {
    const s = await api("/api/stats");
    const statsEl = document.getElementById("stats");
    if (statsEl) {
      statsEl.innerHTML = `
        <div class="kv"><span class="k">Live calls</span><span class="v">${s.live_calls ?? 0}</span></div>
        <div class="kv"><span class="k">Confirmations</span><span class="v">${s.confirmations}</span></div>
        <div class="kv"><span class="k">Corrections</span><span class="v">${s.corrections}</span></div>
        <div class="section-title">Dialect distribution</div>
        ${Object.entries(s.dialects_seen || {}).map(([k,v]) =>
          `<div class="kv"><span class="k">${escapeHTML(k)}</span><span class="v">${v}</span></div>`
        ).join("") || '<div class="placeholder">no data yet</div>'}
        <div class="section-title">State distribution</div>
        ${Object.entries(s.state_distribution || {}).map(([k,v]) =>
          `<div class="kv"><span class="k"><span class="state-pill ${k}">${k}</span></span><span class="v">${v}</span></div>`
        ).join("") || '<div class="placeholder">no data yet</div>'}
      `;
    }
    // Top widgets row (sentiment radar / state pie / dialect pie / KPIs).
    updateStatePie(s.state_distribution || {});
    updateDialectPie(s.dialects_seen || {});
    updateKpis(s);
  } catch (e) { console.error(e); }
}

// ===========================================================
// Top-row widgets — sentiment radar, state pie, dialect pie.
// All share the v2 government palette via getCssVar().
// ===========================================================
let widgetSentRadar = null;
let widgetStatePie = null;
let widgetDialectPie = null;

function getCssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function widgetPalette() {
  return {
    accent: getCssVar("--accent") || "#0d6b5e",
    accentDeep: getCssVar("--accent-deep") || "#064037",
    saffron: getCssVar("--saffron") || "#b07a18",
    green: getCssVar("--green") || "#166534",
    amber: getCssVar("--amber") || "#92400e",
    red: getCssVar("--red") || "#9f1239",
    plum: getCssVar("--plum") || "#6b3982",
    text: getCssVar("--text") || "#1f2a28",
    text2: getCssVar("--text-2") || "#36433f",
    muted: getCssVar("--muted") || "#6b6a5e",
    line: getCssVar("--line") || "#d9cfba",
  };
}

function ensureSentRadar() {
  if (widgetSentRadar || !window.Chart) return;
  const ctx = document.getElementById("widget-sent-radar");
  if (!ctx) return;
  const p = widgetPalette();
  widgetSentRadar = new Chart(ctx, {
    type: "radar",
    data: {
      labels: SENTIMENT_DIMS.map(d => d.charAt(0).toUpperCase() + d.slice(1)),
      datasets: [{
        label: "Now",
        data: SENTIMENT_DIMS.map(() => 0),
        backgroundColor: hexA(p.accent, 0.18),
        borderColor: p.accent,
        borderWidth: 2,
        pointBackgroundColor: SENTIMENT_DIMS.map(d => SENT_COLORS[d]),
        pointBorderColor: p.text,
        pointRadius: 4,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 350 },
      plugins: { legend: { display: false }, tooltip: { enabled: true } },
      scales: {
        r: {
          min: 0, max: 1,
          ticks: { stepSize: 0.25, color: p.muted, backdropColor: "transparent", font: { size: 9 } },
          grid: { color: p.line },
          angleLines: { color: p.line },
          pointLabels: { color: p.text2, font: { size: 11, weight: "600" } },
        },
      },
    },
  });
}

function updateSentRadar(sent, label) {
  ensureSentRadar();
  if (!widgetSentRadar) return;
  widgetSentRadar.data.datasets[0].data = SENTIMENT_DIMS.map(d =>
    Math.max(0, Math.min(1, (sent && sent[d]) || 0)));
  widgetSentRadar.update("none");
  const sub = document.getElementById("widget-sent-sub");
  if (sub) sub.textContent = label || "live turn";
}

function ensureStatePie() {
  if (widgetStatePie || !window.Chart) return;
  const ctx = document.getElementById("widget-state-pie");
  if (!ctx) return;
  const p = widgetPalette();
  widgetStatePie = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["VERIFIED", "CLARIFY", "HANDOVER"],
      datasets: [{
        data: [0, 0, 0],
        backgroundColor: [p.green, p.saffron, p.red],
        borderColor: getCssVar("--panel") || "#fff",
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: { position: "bottom",
                  labels: { color: p.text2, boxWidth: 10, padding: 8, font: { size: 11 } } },
      },
    },
  });
}

function updateStatePie(dist) {
  ensureStatePie();
  if (!widgetStatePie) return;
  widgetStatePie.data.datasets[0].data = [
    dist.VERIFIED || 0,
    dist.CLARIFY || 0,
    dist.HANDOVER || 0,
  ];
  widgetStatePie.update("none");
}

function ensureDialectPie() {
  if (widgetDialectPie || !window.Chart) return;
  const ctx = document.getElementById("widget-dialect-pie");
  if (!ctx) return;
  const p = widgetPalette();
  widgetDialectPie = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: [],
      datasets: [{
        data: [],
        backgroundColor: [p.accent, p.saffron, p.plum, p.green, p.amber, p.red, p.accentDeep],
        borderColor: getCssVar("--panel") || "#fff",
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: { position: "bottom",
                  labels: { color: p.text2, boxWidth: 10, padding: 8, font: { size: 11 } } },
      },
    },
  });
}

function updateDialectPie(dist) {
  ensureDialectPie();
  if (!widgetDialectPie) return;
  const entries = Object.entries(dist).slice(0, 7);
  widgetDialectPie.data.labels = entries.map(([k]) => k);
  widgetDialectPie.data.datasets[0].data = entries.map(([, v]) => v);
  widgetDialectPie.update("none");
}

function updateKpis(s) {
  const dist = s.state_distribution || {};
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v ?? 0; };
  set("kpi-live", s.live_calls ?? 0);
  set("kpi-verified", dist.VERIFIED ?? 0);
  set("kpi-clarify", dist.CLARIFY ?? 0);
  set("kpi-handover", dist.HANDOVER ?? 0);
}

function hexA(hex, alpha) {
  // Accepts "#rrggbb" — falls back to plain string for HSL/named colours.
  if (!hex || !hex.startsWith("#") || hex.length !== 7) return hex;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

async function verifyChain() {
  $("#chain-status").innerHTML = "verifying…";
  try {
    const r = await api("/api/audit/verify");
    if (r.ok) {
      $("#chain-status").innerHTML =
        `<span class="hash-good">✓ chain intact (${r.rows} rows)</span>`;
    } else {
      $("#chain-status").innerHTML =
        `<span class="hash-bad">✗ tampered: ${r.issues.length} issue(s)</span>`;
    }
  } catch (e) {
    $("#chain-status").textContent = "verify failed";
  }
}

// ----- selecting / loading a call -----
async function selectCall(callId) {
  currentCallId = callId;
  const idEl = document.getElementById("active-call-id");
  if (idEl) idEl.textContent = callId;
  // Live-calls panel was removed; this list-highlight is a no-op when the
  // list isn't on the page.
  document.querySelectorAll("#calls-list .call-card").forEach(el =>
    el.classList.toggle("active", el.dataset.id === callId));
  alertedThisCall = false;
  resetSentChart();
  startCallTimer();

  try {
    const c = await api(`/api/calls/${callId}`);
    callStartedAt = c.started_at ? new Date(c.started_at).getTime() : Date.now();
    renderCallHistory(c);
    renderLedger(c.audit || []);
    if (!c.live) stopCallTimer();
  } catch (e) {
    toast("Failed to load call", "danger");
  }

  if (callWS) try { callWS.close(); } catch {}
  const proto = location.protocol === "https:" ? "wss" : "ws";
  callWS = new WebSocket(`${proto}://${location.host}/ws/dashboard/${callId}`);
  callWS.onmessage = (ev) => {
    try { handleEvent(JSON.parse(ev.data)); } catch (e) { console.error(e); }
  };
}

function renderCallHistory(c) {
  const tEl = $("#transcript");
  tEl.innerHTML = "";
  const turns = (c.turns || []);
  $("#turn-count").textContent = `${turns.length} turn${turns.length === 1 ? "" : "s"}`;
  if (!turns.length) {
    tEl.innerHTML = `<div class="placeholder">No turns yet — waiting for citizen audio…</div>`;
  }
  resetSentChart();
  turns.forEach(t => {
    addTranscriptBubble({
      timestamp: t.timestamp,
      transcript_native: t.transcript_native,
      transcript_english: t.interpretation?.translation_en || t.transcript_english,
      detected_dialect: t.detected_dialect || t.dialect,
      detected_language: t.detected_language || t.language,
      state: t.state,
      sentiment: t.sentiment,
      interpretation: t.interpretation,
      paraphrase_text: t.paraphrase_text || t.paraphrase,
    });
    pushSentChart(t.timestamp, t.sentiment || {});
  });
  if (turns.length) {
    const last = turns[turns.length - 1];
    applyToInterpretation({
      ...last,
      detected_dialect: last.detected_dialect || last.dialect,
      detected_language: last.detected_language || last.language,
      paraphrase_text: last.paraphrase_text || last.paraphrase,
      interpretation: last.interpretation,
      sentiment: last.sentiment,
    });
  }
}

// ----- live event handling -----
function handleEvent(ev) {
  switch (ev.type) {
    case "call_started":
      toast("Call started: " + ev.call_id, "success");
      renderCallerContext(ev);
      hideHandoverBar();   // brand-new call — clear any prior handover state
      refreshCalls();
      break;
    case "turn":
      lastTurn = ev;
      addTranscriptBubble(ev);
      applyToInterpretation(ev);
      pushSentChart(ev.timestamp, ev.sentiment || {});
      updateSentRadar(ev.sentiment || {}, "live · " + (ev.detected_language || "—"));
      checkDistressAlert(ev.sentiment || {});
      // refresh multilingual summary in the background after every turn
      setTimeout(() => { try { refreshSummary(); } catch {} }, 200);
      addLedgerRow({
        action: "turn_committed", actor: "system",
        timestamp: ev.timestamp,
        payload_json: JSON.stringify({
          turn_id: ev.turn_id, state: ev.state,
          overall_confidence: ev.interpretation?.overall_confidence,
        }),
        hash: ev.turn_id,
      });
      const tc = $("#transcript").querySelectorAll(".bubble").length;
      $("#turn-count").textContent = `${tc} turn${tc === 1 ? "" : "s"}`;
      break;
    case "confirmation":
      toast(`Citizen: ${ev.response.toUpperCase()}`,
            ev.response === "yes" ? "success" : ev.response === "no" ? "danger" : "");
      addLedgerRow({
        action: "citizen_confirmation", actor: "citizen",
        timestamp: new Date().toISOString(),
        payload_json: JSON.stringify({ response: ev.response, raw: ev.raw_text }),
        hash: "—",
      });
      setStateBadge(ev.new_state);
      break;
    case "correction":
      // Surface the structured mistake-type tag so the agent SEES that the
      // system is logging "this was an urgency_underestimation", not just
      // "field changed". That visibility is what makes the learning loop
      // feel real to a reviewer.
      toast(
        ev.mistake_type
          ? `Correction · ${ev.field} → tagged as ${ev.mistake_type.replace(/_/g, " ")}`
          : `Correction: ${ev.field}`,
        ev.mistake_type === "urgency_underestimation" ? "danger" : ""
      );
      break;
    case "handover":
      toast("HANDOVER → human agent", "danger");
      flashAlert();
      setStateBadge("HANDOVER");
      $("#ai-paraphrase").style.display = "none";
      $("#ai-confirm").style.display = "none";
      const b = $("#ai-bridge");
      b.style.display = "block";
      b.innerHTML = `<div class="meta">🤝 bridging line · spoken to citizen</div>` +
                    `<div>${escapeHTML(ev.bridge_line || "Connecting you to a human agent…")}</div>`;
      // Pull the operator-mic + context into a sticky banner at the top
      // so the agent ACTS immediately instead of scrolling for a mic.
      showHandoverBar(ev);
      break;
    case "call_ended":
      toast("Call ended");
      setStateBadge(ev.final_state || "ENDED");
      stopCallTimer();
      hideHandoverBar();
      refreshCalls();
      break;
    case "shadow_mode":
      shadowMode = !!ev.shadow;
      $("#shadow-toggle").checked = shadowMode;
      toast(`Shadow mode ${shadowMode ? "ON" : "OFF"}`);
      break;
    case "operator_message":
      // Echo back so the operator sees what citizen heard
      appendOperatorHistory({
        text_en: ev.text_en, translated: ev.translated,
        translated_roman: ev.translated_roman,
        lang: ev.speak_lang_code,
        ts: (ev.timestamp || "").replace("T", " ").slice(11, 19),
      });
      break;
    case "voice_confirmation":
      toast(`Citizen voice: ${ev.classification.toUpperCase()} — ${ev.raw_text || ""}`,
            ev.classification === "yes" ? "success" :
            ev.classification === "no" ? "danger" : "");
      break;
    case "post_handover_citizen_audio":
      // After handover, citizen voice goes here (no AI). Show in the
      // sticky handover bar so the agent reads it without scrolling.
      updateHandoverCitizenLive(ev.transcript_native, ev.transcript_roman);
      addTranscriptBubble({
        timestamp: ev.timestamp,
        transcript_native: ev.transcript_native,
        transcript_english: "",
        detected_language: lastTurn?.detected_language || "",
        detected_dialect: "post-handover",
        state: "HANDOVER",
        sentiment: lastTurn?.sentiment || {},
        interpretation: { translation_en: "" },
      });
      break;
  }
}

// Update operator panel target language pill from latest turn
function updateOpTargetLang(lang) {
  const el = $("#op-target-lang");
  if (el) el.textContent = lang ? `→ ${lang}` : "—";
}

function addTranscriptBubble(t) {
  const el = $("#transcript");
  if (el.querySelector(".placeholder")) el.innerHTML = "";
  const cls = t.state === "VERIFIED" ? "verified" :
              t.state === "HANDOVER" ? "handover" : "";
  const native = t.transcript_native || "";
  const eng = t.transcript_english || t.interpretation?.translation_en || "";
  const ts = (t.timestamp || "").replace("T", " ").replace("Z", "").slice(11, 19);
  const dialectTag = t.detected_dialect ?
    `<span class="tag violet">${escapeHTML(t.detected_dialect)}</span>` : "";
  const langTag = t.detected_language ?
    `<span class="tag blue">${escapeHTML(t.detected_language)}</span>` : "";
  const stateTag = `<span class="state-pill ${escapeHTML(t.state||"CLARIFY")}">${escapeHTML(t.state||"")}</span>`;
  const div = document.createElement("div");
  div.className = "bubble " + cls;
  div.innerHTML = `
    <div class="meta"><span>${ts}</span>${langTag}${dialectTag}${stateTag}</div>
    <div>${escapeHTML(native) || '<i class="placeholder">(no speech detected)</i>'}</div>
    ${eng && eng !== native ? `<div style="color:var(--muted); font-size:13px; margin-top:4px;">↳ ${escapeHTML(eng)}</div>` : ""}
  `;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function applyToInterpretation(ev) {
  const ip = ev.interpretation || {};
  const ent = ip.entities || {};
  const sent = ev.sentiment || {};
  setStateBadge(ev.state || "CLARIFY");

  meter("#meter-overall", ip.overall_confidence ?? 0);
  meter("#meter-asr",     ip.asr_confidence ?? 0);
  meter("#meter-intent",  ip.intent_confidence ?? 0);
  meter("#meter-dialect", ip.dialect_confidence ?? 0);
  $("#val-overall").textContent = fmtPct(ip.overall_confidence);
  $("#val-asr").textContent = fmtPct(ip.asr_confidence);
  $("#val-intent").textContent = fmtPct(ip.intent_confidence);
  $("#val-dialect").textContent = fmtPct(ip.dialect_confidence);
  $("#state-reasons").textContent = (ev.decision_reasons || []).join(" · ");
  renderConfidenceTier(ip.overall_confidence ?? 0, ev.state || "CLARIFY");
  renderWhyCard(ev.decision_explain, ev.state || "CLARIFY");

  setEditable("issue_type", ip.issue_type || "—");
  setEditable("urgency_level", ip.urgency_level || "—");
  setEditable("dialect", ev.detected_dialect || "—");
  setEditable("location", ent.location || "—");
  setEditable("persons", (ent.persons || []).join(", ") || "—");
  setEditable("organizations", (ent.organizations || []).join(", ") || "—");
  setEditable("time_references", (ent.time_references || []).join(", ") || "—");
  setEditable("objects", (ent.objects || []).join(", ") || "—");
  $("#kv-lang").textContent = ev.detected_language || "—";
  updateOpTargetLang(ev.detected_language || "");
  $("#kv-pii").innerHTML = (ev.pii_redacted_count ?? 0) > 0 ?
    `<span class="tag amber">${ev.pii_redacted_count} redacted</span>` :
    `<span class="tag green">none</span>`;

  $("#summary-box").textContent = ip.issue_summary || "—";

  const ul = $("#claims-list");
  ul.innerHTML = (ip.factual_claims || []).map(c =>
    `<li>${escapeHTML(c)}</li>`
  ).join("") || `<li class="placeholder">none</li>`;

  renderSentiment(sent);
  if (typeof sent.distress === "number") {
    sentimentTrend.push(sent.distress);
    if (sentimentTrend.length > 5) sentimentTrend.shift();
    const dir = sentimentTrend.length >= 2 ?
      (sentimentTrend.at(-1) - sentimentTrend.at(-2)) : 0;
    const trendEl = $("#sent-trend");
    trendEl.textContent =
      dir > 0.05 ? "↑ rising distress" :
      dir < -0.05 ? "↓ easing" : "→ stable";
    trendEl.className = "tag " + (dir > 0.1 ? "red" : dir > 0 ? "amber" : "green");
  }

  const p = $("#ai-paraphrase"); const c = $("#ai-confirm"); const b = $("#ai-bridge");
  $("#ai-empty").style.display = "none";
  if (ev.state === "HANDOVER" && ev.bridge_line) {
    p.style.display = c.style.display = "none";
    b.style.display = "block";
    b.innerHTML = `<div class="meta">🤝 bridging line · spoken to citizen in ${escapeHTML(ev.paraphrase_lang || "")}</div>` +
                  `<div>${escapeHTML(ev.bridge_line)}</div>`;
  } else {
    b.style.display = "none";
    if (ev.paraphrase_text) {
      p.style.display = "block";
      const shadowBadge = shadowMode ? '<span class="tag amber">SHADOW · NOT spoken</span>' : '';
      p.innerHTML = `<div class="meta">🔊 paraphrase · ${shadowMode ? "would speak" : "spoken back"} to citizen (${escapeHTML(ev.paraphrase_lang || "")}) ${shadowBadge}</div>` +
                    `<div>${escapeHTML(ev.paraphrase_text)}</div>`;
    }
    if (ev.confirm_question) {
      c.style.display = "block";
      c.innerHTML = `<div class="meta">❓ confirmation question</div>` +
                    `<div>${escapeHTML(ev.confirm_question)}</div>`;
    }
  }
}

function setStateBadge(state) {
  const el = $("#state-badge");
  el.className = "state-pill " + state;
  el.textContent = state;
}

// =====================================================================
// STICKY HANDOVER BAR — shown the instant the system escalates a call to
// a human. Pulls the operator mic + context to the very top of the page
// so the agent acts in <1 second instead of scrolling for controls.
// Re-uses the existing toggleOperatorMic + sendOperatorMessage handlers
// (which already broadcast to the citizen via /api/calls/{id}/operator_message).
// =====================================================================
let _handoverTimerInt = null;
let _handoverStartedAt = 0;

function showHandoverBar(ev) {
  const bar = document.getElementById("handover-bar");
  if (!bar) return;
  bar.classList.remove("hidden");

  // Populate the context line: language · location · issue · urgency.
  // Pulls from the last interpretation we've seen, since the handover
  // event itself only carries the bridge line and reasons.
  const ip   = (lastTurn && lastTurn.interpretation) || {};
  const ent  = ip.entities || {};
  const lang = (lastTurn && lastTurn.detected_language) || "—";
  const loc  = ent.location || "location unknown";
  const issue   = ip.issue_type || "issue not yet classified";
  const urgency = ip.urgency_level || "—";
  const reasons = (ev.reasons || []).join(" · ") || "—";

  const sub = document.getElementById("ho-sub");
  if (sub) {
    sub.innerHTML =
      `<strong>${escapeHTML(issue)}</strong> · urgency ${escapeHTML(urgency)} · ` +
      `lang ${escapeHTML(lang)} · ${escapeHTML(loc)}` +
      `<br><span style="opacity:.78; font-size:11px;">why: ${escapeHTML(reasons)}</span>`;
  }

  // Mirror op-mic-status into the bar's status line so existing handlers
  // keep working with no rewiring.
  const obs = new MutationObserver(() => {
    const main = document.getElementById("op-mic-status");
    const here = document.getElementById("ho-mic-status");
    if (main && here) here.textContent = main.textContent;
  });
  const main = document.getElementById("op-mic-status");
  if (main) obs.observe(main, { childList: true, characterData: true, subtree: true });

  // Mirror the recording class so the ho-mic-btn pulses while recording.
  const recObs = new MutationObserver(() => {
    const m = document.getElementById("op-mic-btn");
    const h = document.getElementById("ho-mic-btn");
    if (m && h) h.classList.toggle("recording", m.classList.contains("recording"));
  });
  const mbtn = document.getElementById("op-mic-btn");
  if (mbtn) recObs.observe(mbtn, { attributes: true, attributeFilter: ["class"] });

  // Bridge ho-input <-> op-input so whichever the agent types into, the
  // existing send handler picks it up.
  // Bridge ho-input <-> op-input bidirectionally so either input works.
  // Also patch the send button in the handover bar to read ho-input directly.
  const hi = document.getElementById("ho-input");
  const oi = document.getElementById("op-input");
  if (hi && oi) {
    hi.oninput = () => { oi.value = hi.value; };
    hi.onkeydown = (e) => { if (e.key === "Enter") { oi.value = hi.value; sendOperatorMessage(); hi.value = ""; } };
    oi.addEventListener("input", () => { if (!oi.value) hi.value = ""; });
  }
  // Patch the handover bar's send button to sync before sending
  const hoSendBtn = bar.querySelector(".handover-bar-reply .btn.primary");
  if (hoSendBtn && hi && oi) {
    hoSendBtn.onclick = () => { oi.value = hi.value; sendOperatorMessage(); hi.value = ""; };
  }

  // Live timer since handover began.
  _handoverStartedAt = Date.now();
  if (_handoverTimerInt) clearInterval(_handoverTimerInt);
  _handoverTimerInt = setInterval(() => {
    const t = document.getElementById("ho-timer");
    if (!t) return;
    const ms = Date.now() - _handoverStartedAt;
    const s = Math.floor(ms / 1000);
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    t.textContent = `${mm}:${ss}`;
  }, 500);

  // Make sure it's actually visible on screen (in case the agent had
  // scrolled down looking at sentiment graphs when the call escalated).
  bar.scrollIntoView({ behavior: "smooth", block: "start" });

  // Focus the mic button so a single Space press starts recording.
  setTimeout(() => document.getElementById("ho-mic-btn")?.focus(), 350);
}

function hideHandoverBar() {
  const bar = document.getElementById("handover-bar");
  if (!bar) return;
  bar.classList.add("hidden");
  if (_handoverTimerInt) { clearInterval(_handoverTimerInt); _handoverTimerInt = null; }
  const t = document.getElementById("ho-timer"); if (t) t.textContent = "00:00";
  const live = document.getElementById("ho-citizen-text"); if (live) live.textContent = "—";
}

function updateHandoverCitizenLive(text, roman) {
  const el = document.getElementById("ho-citizen-text");
  if (!el) return;
  const safe = (text || "").trim() || "(silence)";
  el.textContent = roman && roman !== safe ? `${safe}  ·  ${roman}` : safe;
}

// Caller context card — repeat-caller detection + opt-in geolocation. The
// citizen never gives us a phone number; we use a stable browser-issued UUID
// so we can recognise patterns ("same caller has reported harassment 3
// times") and auto-prioritise without ever holding PII.
function renderCallerContext(ev) {
  const card = document.getElementById("caller-card");
  if (!card) return;
  const repeat = !!ev.is_repeat_caller;
  const prior  = ev.prior_calls || [];
  const geo    = ev.geo || {};

  const badge = repeat
    ? `<span class="tag red">REPEAT CALLER · ${prior.length} prior</span>`
    : `<span class="tag green">first-time caller</span>`;

  const priorList = prior.slice(0, 5).map(p => {
    const date = (p.started_at || "").slice(0, 16).replace("T", " ");
    const sum  = p.summary || "—";
    const st   = p.final_state || "—";
    return `<li><span class="caller-prior-meta">${escapeHTML(date)} · ${escapeHTML(st)}</span>
            <span class="caller-prior-sum">${escapeHTML(sum)}</span></li>`;
  }).join("");

  const geoBlock = (geo && geo.lat != null && geo.lng != null) ? `
    <div class="caller-geo">
      <span class="caller-geo-label">Approx. location</span>
      <span>${geo.lat.toFixed(4)}, ${geo.lng.toFixed(4)} · ±${(geo.accuracy || 0)|0} m</span>
      <a href="https://www.google.com/maps?q=${geo.lat},${geo.lng}" target="_blank" rel="noopener">Open map ↗</a>
    </div>
  ` : `<div class="caller-geo muted">Location not shared by citizen.</div>`;

  card.classList.toggle("caller-card--repeat", repeat);
  card.innerHTML = `
    <div class="caller-card-head">
      ${badge}
      <span class="caller-id muted">caller · ${escapeHTML((ev.caller_id || "unknown").slice(0, 12))}</span>
    </div>
    ${geoBlock}
    ${prior.length ? `<div class="caller-prior-title">Earlier calls</div>
                       <ul class="caller-prior-list">${priorList}</ul>` : ""}
  `;
  card.style.display = "block";
}

// Structured WHY card — turns the state machine's `decision_explain` block
// into discrete bullets the agent (and any reviewer) can read at a glance.
// This is the explainability layer: the system never escalates without
// showing exactly which signals (keyword / emotion / confidence / severity)
// drove the decision.
function renderWhyCard(explain, state) {
  const card = document.getElementById("why-card");
  const list = document.getElementById("why-list");
  if (!card || !list) return;
  if (!explain) {
    list.innerHTML = "";
    card.classList.remove("why-card--ho", "why-card--clarify");
    return;
  }
  const sig = explain.signals || {};
  const items = [];
  items.push({ k: "Decision", v: explain.decision || state || "—",
               cls: explain.decision === "HANDOVER" ? "why-bad" : "why-ok" });
  if (explain.primary_reason) {
    items.push({ k: "Primary reason", v: explain.primary_reason });
  }
  if ((sig.keywords_matched || []).length) {
    items.push({ k: "Detected keywords",
                 v: sig.keywords_matched.join(", "), cls: "why-bad" });
  }
  if (sig.issue_type) {
    items.push({ k: "Issue type", v: sig.issue_type });
  }
  if (sig.urgency_level) {
    items.push({ k: "Urgency level", v: sig.urgency_level });
  }
  const emotion = [];
  if (sig.distress > 0.4) emotion.push(`distress ${(sig.distress*100|0)}%`);
  if (sig.fear     > 0.4) emotion.push(`fear ${(sig.fear*100|0)}%`);
  if (sig.urgency  > 0.5) emotion.push(`urgency ${(sig.urgency*100|0)}%`);
  if (emotion.length) {
    items.push({ k: "Emotion", v: emotion.join(" · "), cls: "why-bad" });
  }
  if (typeof sig.overall_confidence === "number") {
    const pct = (sig.overall_confidence * 100).toFixed(0) + "%";
    const cls = sig.overall_confidence < 0.5 ? "why-bad"
              : sig.overall_confidence < 0.8 ? "why-warn" : "why-ok";
    items.push({ k: "Confidence", v: pct, cls });
  }
  list.innerHTML = items.map(it =>
    `<li><span class="why-k">${escapeHTML(it.k)}</span>` +
    `<span class="why-v ${it.cls || ""}">${escapeHTML(String(it.v))}</span></li>`
  ).join("");
  card.classList.toggle("why-card--ho",      explain.decision === "HANDOVER");
  card.classList.toggle("why-card--clarify", explain.decision !== "HANDOVER");
}

// Three-tier confidence band — explicit decision label so the agent never
// has to mentally translate a percentage into "what am I supposed to do".
function renderConfidenceTier(score, state) {
  const root = document.getElementById("conf-tier");
  if (!root) return;
  const pct = Math.max(0, Math.min(1, score || 0));
  const pctText = (pct * 100).toFixed(0) + "%";

  let tier, label, action;
  if (state === "HANDOVER") {
    tier = "high"; label = "Handover engaged";
    action = "Human officer is in control of this call.";
  } else if (pct >= 0.80) {
    tier = "high"; label = "High — auto proceed";
    action = "AI verification can continue without intervention.";
  } else if (pct >= 0.50) {
    tier = "med"; label = "Medium — re-verify";
    action = "Listen to the next turn closely. Edit any field before confirming.";
  } else {
    tier = "low"; label = "Low — human takeover";
    action = "Take over the call. The AI cannot interpret this reliably.";
  }
  root.className = "conf-tier conf-tier--" + tier;
  document.getElementById("conf-tier-pct").textContent = pctText;
  document.getElementById("conf-tier-label").textContent = label;
  document.getElementById("conf-tier-action").textContent = action;
}

function setEditable(field, value) {
  const el = document.querySelector(`[data-field="${field}"]`);
  if (!el) return;
  if (document.activeElement === el) return;
  el.textContent = value;
  el.dataset.original = value;
}

function renderSentiment(sent) {
  const root = $("#sentiment-bars");
  root.innerHTML = SENTIMENT_DIMS.map(d => {
    const v = Math.max(0, Math.min(1, sent[d] || 0));
    const danger = (d !== "calm" && v >= 0.7) ? "danger" :
                   (d !== "calm" && v >= 0.4) ? "warn" : "";
    return `
      <div class="bar-row">
        <span class="label">${d}</span>
        <div class="meter ${danger}"><div class="fill" style="width:${(v*100).toFixed(0)}%; background:${SENT_COLORS[d]};"></div></div>
        <span class="val">${fmtPct(v)}</span>
      </div>`;
  }).join("");
}

// ----- sentiment trajectory chart -----
function ensureChart() {
  if (sentChart || !window.Chart) return;
  const ctx = $("#sentiment-chart");
  if (!ctx) return;
  sentChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: SENTIMENT_DIMS.map(d => ({
        label: d,
        data: [],
        borderColor: SENT_COLORS[d],
        backgroundColor: SENT_COLORS[d] + "33",
        tension: 0.35, borderWidth: 2,
        pointRadius: 0, pointHoverRadius: 3,
        fill: false,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 240 },
      scales: {
        x: { ticks: { color: "#94a3b8", font: { size: 10 } }, grid: { display: false } },
        y: { min: 0, max: 1,
             ticks: { color: "#94a3b8", font: { size: 10 }, stepSize: 0.25 },
             grid: { color: "rgba(148,163,184,0.15)" } },
      },
      plugins: {
        legend: { position: "bottom", labels: { color: "#94a3b8", boxWidth: 10, padding: 8, font: { size: 11 } } },
        tooltip: { mode: "index", intersect: false },
      },
    },
  });
}
function pushSentChart(ts, sent) {
  ensureChart();
  if (!sentChart) return;
  const label = (ts || "").replace("T", " ").replace("Z", "").slice(11, 19);
  sentChart.data.labels.push(label);
  if (sentChart.data.labels.length > 20) sentChart.data.labels.shift();
  SENTIMENT_DIMS.forEach((d, i) => {
    sentChart.data.datasets[i].data.push(Math.max(0, Math.min(1, sent[d] || 0)));
    if (sentChart.data.datasets[i].data.length > 20) sentChart.data.datasets[i].data.shift();
  });
  sentChart.update("none");
}
function resetSentChart() {
  ensureChart();
  if (!sentChart) return;
  sentChart.data.labels = [];
  sentChart.data.datasets.forEach(ds => ds.data = []);
  sentChart.update("none");
}

// ----- distress alert -----
function checkDistressAlert(sent) {
  const distress = sent.distress || 0;
  if (distress >= DISTRESS_ALERT_THRESHOLD && !alertedThisCall) {
    alertedThisCall = true;
    flashAlert();
    toast(`⚠ High distress detected (${fmtPct(distress)})`, "danger");
    beep();
  }
  if (distress < 0.5) alertedThisCall = false;
}
function flashAlert() {
  const f = $("#alert-flash");
  if (!f) return;
  f.classList.remove("show");
  void f.offsetWidth;
  f.classList.add("show");
  setTimeout(() => f.classList.remove("show"), 1600);
}
function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.type = "sine"; o.frequency.value = 880;
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.4);
    o.start(); o.stop(ctx.currentTime + 0.42);
  } catch {}
}

// ----- ledger -----
function renderLedger(rows) {
  const el = $("#ledger");
  el.innerHTML = "";
  rows.forEach(r => addLedgerRow(r));
  $("#ledger-count").textContent = rows.length;
}
function addLedgerRow(r) {
  const el = $("#ledger");
  const div = document.createElement("div");
  div.className = "audit-row";
  const ts = (r.timestamp || "").replace("T", " ").slice(11, 19);
  div.innerHTML = `
    <span class="seq">${r.seq ?? ""}</span>
    <span><span class="tag">${escapeHTML(r.action || "")}</span></span>
    <div>
      <div style="font-size:11px; color:var(--muted);">${ts} · ${escapeHTML(r.actor || "")}</div>
      <div class="hash" title="${escapeHTML(r.hash||"")}">${escapeHTML((r.hash||"").slice(0,16))}</div>
    </div>`;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
  $("#ledger-count").textContent = el.children.length;
}

// ----- editable corrections -----
document.addEventListener("focusout", async (e) => {
  if (!e.target.classList?.contains("editable")) return;
  if (!currentCallId || !lastTurn) return;
  const field = e.target.dataset.field;
  const newVal = e.target.textContent.trim();
  const oldVal = e.target.dataset.original ?? "";
  if (!field || newVal === oldVal) return;
  e.target.dataset.original = newVal;
  try {
    await api(`/api/calls/${currentCallId}/correct`, {
      method: "POST",
      body: JSON.stringify({
        field, old_value: oldVal, new_value: newVal,
        turn_id: lastTurn?.turn_id || "",
        corrected_by: "agent-001",
      }),
    });
    toast(`Saved: ${field}`, "success");
  } catch (err) { toast("Save failed", "danger"); }
});

async function manualHandover() {
  if (!currentCallId) return toast("No active call");
  const note = prompt("Optional handover note?", "") || "";
  try {
    await api(`/api/calls/${currentCallId}/handover`, {
      method: "POST",
      body: JSON.stringify({ reason: "agent_initiated", note, by: "agent-001" }),
    });
  } catch { toast("Handover failed", "danger"); }
}
async function endCall() {
  if (!currentCallId) return toast("No active call");
  if (!confirm("End this call?")) return;
  try {
    await api(`/api/calls/${currentCallId}/end`, {
      method: "POST", body: JSON.stringify({}),
    });
  } catch { toast("End failed", "danger"); }
}

async function sendOperatorMessage() {
  if (!currentCallId) return toast("No active call", "danger");
  const input = $("#op-input");
  const text = (input.value || "").trim();
  if (!text) return;
  input.value = "";
  try {
    await api(`/api/calls/${currentCallId}/operator_message`, {
      method: "POST",
      body: JSON.stringify({ text, by: "operator-001" }),
    });
    // Don't render locally — the WS broadcast will deliver the entry once
    // (otherwise it appears twice on the dashboard).
    toast("Sent to citizen", "success");
  } catch (e) { toast("Failed to send", "danger"); }
}

// ---- Operator microphone (browser SpeechRecognition for English STT) ----
let opRecognition = null;
let opMicActive = false;

function ensureOperatorRecognition() {
  if (opRecognition) return opRecognition;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return null;
  const r = new SR();
  r.lang = "en-IN";       // operator types/speaks in English
  r.continuous = false;
  r.interimResults = true;
  r.onresult = (ev) => {
    let txt = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      txt += ev.results[i][0].transcript;
    }
    $("#op-input").value = txt;
  };
  r.onerror = (e) => {
    $("#op-mic-status").textContent = "mic error: " + (e.error || "unknown");
    opMicActive = false;
    $("#op-mic-btn")?.classList.remove("recording");
  };
  r.onend = () => {
    opMicActive = false;
    $("#op-mic-btn")?.classList.remove("recording");
    $("#op-mic-status").textContent = "";
    // If something was captured, auto-send.
    const txt = ($("#op-input").value || "").trim();
    if (txt) sendOperatorMessage();
  };
  opRecognition = r;
  return r;
}

function toggleOperatorMic() {
  const rec = ensureOperatorRecognition();
  if (!rec) {
    toast("Browser doesn't support speech recognition — type instead", "danger");
    return;
  }
  if (opMicActive) {
    try { rec.stop(); } catch {}
    return;
  }
  $("#op-input").value = "";
  $("#op-mic-status").textContent = "🎙 listening… speak in English";
  $("#op-mic-btn").classList.add("recording");
  opMicActive = true;
  try { rec.start(); } catch (e) {
    toast("Mic start failed", "danger");
    opMicActive = false;
    $("#op-mic-btn").classList.remove("recording");
  }
}

function appendOperatorHistory(m) {
  const el = $("#op-history");
  if (!el) return;
  const div = document.createElement("div");
  div.className = "bubble system";
  div.style.marginBottom = "6px";
  const rom = m.translated_roman && m.translated_roman !== m.translated
    ? `<div class="roman">${escapeHTML(m.translated_roman)}</div>` : "";
  div.innerHTML = `
    <div class="meta">🗣 operator → citizen · ${m.ts || ""} · ${escapeHTML(m.lang || "")}</div>
    <div style="font-size:13px; color:var(--muted);">EN: ${escapeHTML(m.text_en)}</div>
    <div style="font-size:14px; margin-top:4px;">${escapeHTML(m.translated || "")}</div>
    ${rom}
  `;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

async function refreshSummary() {
  if (!currentCallId) return toast("No active call");
  $("#summary-status").textContent = "translating…";
  $("#sum-kn").textContent = $("#sum-hi").textContent = $("#sum-en").textContent = "…";
  try {
    const r = await api(`/api/calls/${currentCallId}/summary`, {
      method: "POST", body: JSON.stringify({}),
    });
    $("#sum-kn").textContent = r.kn || "—";
    $("#sum-hi").textContent = r.hi || "—";
    $("#sum-en").textContent = r.en || r.source_summary || "—";
    $("#summary-status").textContent = r.issue_type ? `${r.issue_type} · ${r.urgency_level || ""}` : "ready";
  } catch (e) {
    $("#summary-status").textContent = "failed";
    toast("Summary translation failed", "danger");
  }
}

async function exportCall() {
  if (!currentCallId) return toast("No active call");
  try {
    const c = await api(`/api/calls/${currentCallId}`);
    const blob = new Blob([JSON.stringify(c, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${currentCallId}.json`;
    a.click(); URL.revokeObjectURL(url);
    toast("Exported", "success");
  } catch { toast("Export failed", "danger"); }
}

async function setShadow(on) {
  if (!currentCallId) {
    shadowMode = on;
    return toast(`Shadow mode ${on ? "ON" : "OFF"} (next call)`);
  }
  try {
    await api(`/api/calls/${currentCallId}/shadow`, {
      method: "POST", body: JSON.stringify({ shadow: on }),
    });
    shadowMode = on;
  } catch { toast("Shadow toggle failed", "danger"); }
}

// ----- timer -----
function startCallTimer() {
  stopCallTimer();
  callStartedAt = callStartedAt || Date.now();
  $("#call-timer").classList.remove("hidden");
  const tick = () => {
    $("#timer-text").textContent = fmtClock(Date.now() - callStartedAt);
  };
  tick();
  timerInterval = setInterval(tick, 1000);
}
function stopCallTimer() {
  if (timerInterval) clearInterval(timerInterval);
  timerInterval = null;
}

// ----- global socket -----
function openGlobalWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  globalWS = new WebSocket(`${proto}://${location.host}/ws/dashboard`);
  globalWS.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "call_started" || msg.type === "call_ended") {
        refreshCalls();
      }
      if (msg.type === "call_started" && !currentCallId) {
        selectCall(msg.call_id);
      }
    } catch {}
  };
  globalWS.onclose = () => setTimeout(openGlobalWS, 2000);
}

// ----- shortcuts -----
document.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  const typing = tag === "input" || tag === "textarea" || e.target.isContentEditable;
  if (typing) return;
  if (e.key === "Escape" && currentCallId) { e.preventDefault(); endCall(); }
  else if ((e.key === "h" || e.key === "H") && !e.shiftKey) { e.preventDefault(); manualHandover(); }
  else if ((e.key === "v" || e.key === "V") && !e.shiftKey) { e.preventDefault(); verifyChain(); }
  else if ((e.key === "e" || e.key === "E") && !e.shiftKey) { e.preventDefault(); exportCall(); }
});

// shadow toggle UI
document.addEventListener("DOMContentLoaded", () => {
  $("#shadow-toggle").addEventListener("change", (e) => setShadow(e.target.checked));
});

// init
health();
refreshCalls();
refreshStats();
openGlobalWS();
ensureChart();
setInterval(refreshStats, 8000);
setInterval(refreshCalls, 5000);

window.refreshCalls = refreshCalls;
window.verifyChain = verifyChain;
window.manualHandover = manualHandover;
window.endCall = endCall;
window.exportCall = exportCall;
window.refreshSummary = refreshSummary;
window.sendOperatorMessage = sendOperatorMessage;
window.toggleOperatorMic = toggleOperatorMic;
