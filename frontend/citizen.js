// Pratyaya — Citizen voice loop
// Flow: AI speaks (TTS) -> mic auto-opens -> VAD detects user speech ->
// silence ends recording -> upload -> AI transcribes/interprets/responds ->
// speaks back -> loop.
//
// State machine on the citizen side:
//   idle | listening | processing | speaking | verified | handover

const PHRASE_MIN_MS = 600;        // need at least this much speech before allowing stop
const SILENCE_HANG_MS = 2000;     // stop after this long of silence (post-speech)
const HARD_LIMIT_MS = 25000;      // hard recording cap
const VAD_RMS_THRESHOLD = 0.006;  // amplitude that counts as "speech" — lowered so quiet/AGC mics register
const NO_SPEECH_HARD_FLOOR_MS = 11000;  // if NO speech ever detected at all, give up after this
const MIN_BLOB_BYTES = 150;       // anything smaller than this is empty / metadata-only opus

let callId = null;
let citizenWS = null;
let mediaStream = null;
let mediaRec = null;
let audioCtx = null;
let analyser = null;
let levelRAF = null;
let isRecording = false;
let manualMicMode = false;
let lastAudio = null;          // currently-playing TTS audio element
let langPref = "kn";
let pageState = "idle";
let awaitingConfirmation = false;  // true when we're listening for haudu/illa
let prosodyMax = { rate: 0, pitch: 0, loudness: 0 };
let recordingStartedAt = 0;    // performance.now() when current recording began
let speechWasDetected = false; // set true once VAD sees speech in the active recording

// Make sure the AudioContext is running — Chrome/Edge create it suspended
// until the first user gesture. Without this the mic analyser silently
// returns flat data and VAD never trips.
async function ensureAudioCtx() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    audioCtx = new Ctx({ sampleRate: 48000 });
  }
  if (audioCtx.state === "suspended") {
    try { await audioCtx.resume(); } catch (e) { console.warn("audioCtx.resume failed", e); }
  }
  return audioCtx;
}

function $(s, r=document) { return r.querySelector(s); }
function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]
  ));
}
function toast(msg, kind="") {
  const root = $("#toast-stack"); if (!root) return;
  const el = document.createElement("div");
  el.className = "toast " + (kind || ""); el.textContent = msg;
  root.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateY(8px)"; }, 2400);
  setTimeout(() => el.remove(), 2900);
}
async function api(path, opts={}) {
  if (!opts.headers && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error("api " + r.status);
  return r.json();
}

// Map our internal pageState → the spec's product state machine names
const STATE_MAP = {
  idle:       { code: "IDLE",        label: "READY",       icon: "⬤" },
  listening:  { code: "LISTENING",   label: "LISTENING",   icon: "🎙" },
  processing: { code: "PROCESSING",  label: "PROCESSING",  icon: "⚙" },
  speaking:   { code: "VERIFYING",   label: "VERIFYING",   icon: "🔊" },
  verified:   { code: "CONFIRMED",   label: "CONFIRMED",   icon: "✓" },
  handover:   { code: "HANDOVER",    label: "HANDOVER",    icon: "🤝" },
};

function setStatus(state, text) {
  pageState = state;
  // legacy pill (still present for compatibility)
  const pill = $("#status-pill"); const t = $("#status-text");
  if (pill) {
    pill.classList.remove("idle","listening","processing","speaking","verified","handover");
    pill.classList.add(state);
  }
  if (t) t.textContent = text || "";

  // ★ New government-grade state banner
  const banner = $("#state-banner");
  if (banner) {
    const m = STATE_MAP[state] || STATE_MAP.idle;
    banner.classList.remove("IDLE","LISTENING","PROCESSING","VERIFYING","CONFIRMED","CLARIFYING","HANDOVER");
    banner.classList.add(m.code);
    $("#state-icon").textContent = m.icon;
    $("#state-banner-label").textContent = m.label;
    $("#state-banner-detail").textContent = text || "";
  }
}
function setStateTag(state) {
  const el = $("#state-tag"); if (!el) return;
  el.className = "state-pill " + state;
  el.textContent = state;
}
function setHint(text) {
  const h = $("#hint-text"); if (h) h.textContent = text || "";
}

function langCode(pref) {
  // Default for Auto-detect is Kannada — this is the Karnataka 1092 helpline
  // and Kannada is the most-likely caller language. The backend will still
  // override the voice using the actual script of the text, so this is just
  // a fallback hint when no explicit code arrives from the server.
  return { kn: "kn-IN", hi: "hi-IN", en: "en-IN", auto: "kn-IN" }[pref] || "kn-IN";
}

// Detect the dominant script of a string and map to a BCP-47 code.
// Mirrors backend tts.detect_script so the frontend can repair a missing or
// mismatched speak_lang_code coming from the LLM.
function detectScriptLang(text) {
  if (!text) return "";
  const kn = (text.match(/[ಀ-೿]/g) || []).length;
  const hi = (text.match(/[ऀ-ॿ]/g) || []).length;
  const en = (text.match(/[A-Za-z]/g) || []).length;
  if (kn >= 3 && kn >= hi) return "kn-IN";
  if (hi >= 3 && hi > kn)  return "hi-IN";
  if (kn > hi && kn > 0)   return "kn-IN";
  if (hi > 0)              return "hi-IN";
  if (en > 0)              return "en-IN";
  return "";
}
function localizedHint(key) {
  const t = {
    kn: {
      listening: "ಆಲಿಸುತ್ತಿದ್ದೇನೆ… ಮಾತನಾಡಿ",
      processing: "ಪ್ರಕ್ರಿಯೆ ಆಗುತ್ತಿದೆ…",
      speaking:   "ಪ್ರತ್ಯಯ ಮಾತನಾಡುತ್ತಿದ್ದಾರೆ",
      ready:      "ಮಾತನಾಡಲು ಸಿದ್ಧ",
      verified:   "ನಿಮ್ಮ ಸಮಸ್ಯೆ ದಾಖಲಾಯಿತು. ಧನ್ಯವಾದ.",
      handover:   "ಮಾನವ ಅಧಿಕಾರಿಗೆ ಸಂಪರ್ಕಿಸಲಾಗುತ್ತಿದೆ…",
      didntcatch: "ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ಮತ್ತೊಮ್ಮೆ ಪ್ರಯತ್ನಿಸಿ.",
      confirm:    "ಹೌದು ಅಥವಾ ಇಲ್ಲ ಎಂದು ಹೇಳಿ",
    },
    hi: {
      listening: "सुन रहे हैं… बोलिए",
      processing: "प्रोसेस हो रहा है…",
      speaking:   "प्रत्यय बोल रहे हैं",
      ready:      "बोलने के लिए तैयार",
      verified:   "आपकी समस्या दर्ज हो गई। धन्यवाद।",
      handover:   "मानव अधिकारी से जोड़ा जा रहा है…",
      didntcatch: "स्पष्ट सुनाई नहीं दिया। दोबारा कोशिश करें।",
      confirm:    "हाँ या नहीं कहिए",
    },
    en: {
      listening: "Listening… please speak",
      processing: "Processing…",
      speaking:   "Pratyaya is speaking",
      ready:      "Ready to speak",
      verified:   "Your issue is recorded. Thank you.",
      handover:   "Connecting you to a human officer…",
      didntcatch: "I didn't catch that — please try again.",
      confirm:    "Please say yes or no",
    },
  };
  const base = (langPref || "en").split("-")[0];
  return t[base]?.[key] || t["en"][key] || "";
}

// ============================================================
// CALL LIFECYCLE
// ============================================================
// Stable per-browser caller identifier (no PII — just a UUID stored locally).
// Lets the system recognise a repeat caller across separate calls and prior-
// itise them, without ever capturing a phone number or name.
function getCallerId() {
  try {
    let id = localStorage.getItem("pratyaya:caller_id");
    if (!id) {
      id = "c_" + (crypto.randomUUID
        ? crypto.randomUUID().replace(/-/g, "").slice(0, 16)
        : Math.random().toString(36).slice(2, 14));
      localStorage.setItem("pratyaya:caller_id", id);
    }
    return id;
  } catch { return null; }
}

async function getOptionalGeolocation() {
  // Opt-in only. If the user declines or the browser blocks it we just send
  // null — the helpline still works fine without it.
  if (!navigator.geolocation) return null;
  const want = $("#share-location") && $("#share-location").checked;
  if (!want) return null;
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({
        lat: +pos.coords.latitude.toFixed(5),
        lng: +pos.coords.longitude.toFixed(5),
        accuracy: pos.coords.accuracy,
        label: null,
      }),
      () => resolve(null),
      { enableHighAccuracy: false, timeout: 4000, maximumAge: 60000 },
    );
  });
}

async function startCall() {
  langPref = $("#lang-pref").value;
  resetUI();
  // CRITICAL: this click is the user gesture — kick the audio context to
  // "running" right now so later mic + TTS playback both work. Without
  // this Chrome/Edge keep audioCtx suspended and the VAD analyser returns
  // flat data ⇒ "no capture".
  await ensureAudioCtx();
  // Pre-warm mic permission too. Asking now (during the click) gives
  // browsers the user-gesture they need to remember the grant; otherwise
  // every turn would re-prompt on some browsers.
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach(t => t.stop());
    console.log("[pratyaya] mic permission pre-warmed");
  } catch (e) {
    console.warn("[pratyaya] mic permission pre-warm failed:", e?.name);
    // Don't bail — the user may grant on the next prompt.
  }
  try {
    const callerId = getCallerId();
    const geo = await getOptionalGeolocation();
    const r = await api("/api/calls/start", {
      method: "POST",
      body: JSON.stringify({
        citizen_lang_pref: langPref,
        caller_id: callerId,
        geo,
      }),
    });
    if (r.is_repeat_caller) {
      toast(`Welcome back — we have ${r.prior_calls.length} prior call${r.prior_calls.length === 1 ? "" : "s"} on file. Connecting…`);
    }
    callId = r.call_id;
    $("#not-started").classList.add("hidden");
    $("#in-call").classList.remove("hidden");
    $("#conn-tag").textContent = "connected";
    $("#conn-tag").className = "tag green";
    openCitizenWS();

    const greet =
      langPref === "hi"
        ? "नमस्ते। 1092 हेल्पलाइन में आपका स्वागत है। कृपया अपनी समस्या विस्तार से बताइए — मैं सुन रही हूँ।"
        : langPref === "en"
          ? "Welcome to the 1092 helpline. Please tell me what is happening — I'm listening."
          : "ನಮಸ್ಕಾರ, 1092 ಸಹಾಯವಾಣಿಗೆ ಸ್ವಾಗತ. ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ವಿವರವಾಗಿ ಹೇಳಿ — ನಾನು ಆಲಿಸುತ್ತಿದ್ದೇನೆ.";
    addAITurn(greet);
    awaitingConfirmation = false;
    // Show greeting text immediately; attempt TTS but always open mic after
    setStatus("speaking", localizedHint("speaking"));
    try {
      await speak(greet, langCode(langPref));
    } catch {}
    // Always open mic regardless of whether TTS played
    if (pageState !== "verified" && pageState !== "handover") {
      await beginAutoListen();
    }
  } catch (e) {
    console.error(e);
    toast("Failed to start call", "danger");
  }
}

async function hangup() {
  stopRecording(true);
  try { lastAudio?.pause(); } catch {}
  if (!callId) return resetUI();
  const callIdSnap = callId;
  try { await api(`/api/calls/${callId}/end`, { method: "POST", body: "{}" }); } catch {}
  try { citizenWS?.close(); } catch {}
  callId = null;

  // Show the end-of-call summary BEFORE returning to the start screen so
  // the citizen sees what got recorded and what happens next.
  await showEndOfCallSummary(callIdSnap);

  $("#in-call").classList.add("hidden");
  $("#not-started").classList.remove("hidden");
  $("#conn-tag").textContent = "disconnected";
  $("#conn-tag").className = "tag";
}

async function showEndOfCallSummary(cid) {
  if (!cid) return;
  let data = {};
  try {
    data = await api(`/api/calls/${cid}/citizen_summary`, { method: "GET" });
  } catch {}
  const card = $("#end-summary");
  if (!card) return;

  const issue   = data.issue_type    || "—";
  const urgency = data.urgency_level || "—";
  const state   = data.final_state   || "—";
  const summary = data.summary       || "Your call is recorded. Thank you.";
  const lang    = (langPref || "en").split("-")[0];
  const labels = {
    kn: { title: "ಕರೆಯ ಸಾರಾಂಶ", issue: "ಸಮಸ್ಯೆ", urgency: "ತುರ್ತು",
          state: "ಸ್ಥಿತಿ", followup: "ಮುಂದಿನ ಹಂತ", close: "ಮುಚ್ಚಿ" },
    hi: { title: "कॉल सारांश", issue: "मुद्दा", urgency: "तत्परता",
          state: "स्थिति", followup: "अगला कदम", close: "बंद करें" },
    en: { title: "Call summary", issue: "Issue", urgency: "Urgency",
          state: "Status", followup: "Next step", close: "Close" },
  }[lang] || { title: "Call summary", issue: "Issue", urgency: "Urgency",
               state: "Status", followup: "Next step", close: "Close" };

  const followup =
    state === "HANDOVER"
      ? (lang === "kn" ? "ಮಾನವ ಅಧಿಕಾರಿ ಶೀಘ್ರವೇ ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸುತ್ತಾರೆ."
       : lang === "hi" ? "मानव अधिकारी जल्द आपसे संपर्क करेंगे।"
       : "A human officer will reach out to you shortly.")
      : (lang === "kn" ? "ನಿಮ್ಮ ಸಮಸ್ಯೆ ದಾಖಲಿಸಲಾಗಿದೆ. ಫಾಲೋ-ಅಪ್ ಆಗುತ್ತದೆ."
       : lang === "hi" ? "आपकी समस्या दर्ज हो गई है — फॉलो-अप किया जाएगा।"
       : "Your concern is logged and will be followed up on.");

  card.innerHTML = `
    <div class="end-summary-head">${escapeHTML(labels.title)}</div>
    <div class="end-summary-grid">
      <div class="k">${escapeHTML(labels.issue)}</div>   <div class="v">${escapeHTML(issue)}</div>
      <div class="k">${escapeHTML(labels.urgency)}</div> <div class="v">${escapeHTML(urgency)}</div>
      <div class="k">${escapeHTML(labels.state)}</div>   <div class="v">${escapeHTML(state)}</div>
    </div>
    <div class="end-summary-text">${escapeHTML(summary)}</div>
    <div class="end-summary-followup">${escapeHTML(followup)}</div>
    <button class="btn primary" onclick="document.getElementById('end-summary').classList.add('hidden');">
      ${escapeHTML(labels.close)}
    </button>
  `;
  card.classList.remove("hidden");
}

function resetUI() {
  $("#transcript").innerHTML = "";
  $("#fallback-confirm").classList.add("hidden");
  showConfirmBar(false);
  setStatus("idle", localizedHint("ready"));
  setStateTag("CLARIFY");
  awaitingConfirmation = false;
  const sp = $("#ai-spotlight");
  if (sp) {
    sp.classList.remove("handover","verified","operator");
    sp.classList.add("empty");
    $("#ai-spotlight-icon").textContent = "🔊";
    $("#ai-spotlight-who").textContent = "Pratyaya";
    $("#ai-spotlight-text").textContent = "";
    $("#ai-spotlight-roman").classList.remove("show");
  }
  $("#last-citizen")?.classList.add("hidden");
}

function openCitizenWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  citizenWS = new WebSocket(`${proto}://${location.host}/ws/citizen/${callId}`);
  citizenWS.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      handleServerMessage(msg);
    } catch (e) { console.error(e); }
  };
  citizenWS.onclose = () => {
    $("#conn-tag").textContent = "disconnected";
    $("#conn-tag").className = "tag";
  };
}

// ============================================================
// SERVER → CLIENT messages
// ============================================================
function showConfirmBar(show) {
  const bar = $("#confirm-bar");
  if (!bar) return;
  bar.classList.toggle("hidden", !show);
}

function handleServerMessage(msg) {
  if (msg.type === "paraphrase") {
    // AI paraphrased the citizen's description; now asks for confirmation.
    addAITurn(msg.paraphrase, msg.paraphrase_roman);
    if (msg.confirm_question) {
      addAITurn(msg.confirm_question, msg.confirm_question_roman);
    }
    setStateTag(msg.state || "CLARIFY");
    const fullText = [msg.paraphrase, msg.confirm_question].filter(Boolean).join(" … ");
    awaitingConfirmation = true;
    showConfirmBar(true);   // surface the haudu/illa fallback buttons
    speakThenListen(fullText, msg.speak_lang_code);
  } else if (msg.type === "ai_response") {
    const kind = msg.state === "VERIFIED" ? "verified" :
                 msg.state === "HANDOVER" ? "handover" : "";
    addAITurn(msg.text, msg.text_roman, kind);
    setStateTag(msg.state || "CLARIFY");
    // Yes/no tap-to-respond buttons only appear during an explicit verify.
    awaitingConfirmation = (msg.classification === "verify");
    showConfirmBar(msg.classification === "verify");
    if (msg.classification === "close") {
      // Citizen wrapped up the call ("thanks", "ok bye"). Speak the
      // farewell, then auto-hangup so they aren't stuck on a dead line.
      setStatus("verified", localizedHint("verified"));
      awaitingConfirmation = false;
      showConfirmBar(false);
      speak(msg.text, msg.speak_lang_code).then(() => {
        setTimeout(() => { try { hangup(); } catch {} }, 600);
      });
    } else if (msg.state === "VERIFIED") {
      setStatus("verified", localizedHint("verified"));
      speakThenListen(msg.text, msg.speak_lang_code);
    } else if (msg.state === "HANDOVER") {
      setStatus("handover", localizedHint("handover"));
      speak(msg.text, msg.speak_lang_code).then(() => beginAutoListen());
    } else {
      speakThenListen(msg.text, msg.speak_lang_code);
    }
  } else if (msg.type === "handover") {
    addAITurn(msg.bridge_line, msg.bridge_roman, "handover");
    setStateTag("HANDOVER");
    setStatus("handover", localizedHint("handover"));
    awaitingConfirmation = false;
    showConfirmBar(false);
    // Speak the bridge line ONCE, then keep the mic available so the citizen
    // can talk to the operator (their audio is just transcribed — no AI
    // re-trigger).
    speak(msg.bridge_line, msg.speak_lang_code).then(() => {
      // Open the mic so they can keep talking to the human officer.
      // The /turn endpoint short-circuits server-side once state=HANDOVER.
      beginAutoListen();
    });
  } else if (msg.type === "operator_message") {
    // Live human officer is talking through the AI (web-to-web handover).
    addOperatorTurn(msg.text, msg.text_roman);
    setStatus("speaking", localizedHint("speaking"));
    speak(msg.text, msg.speak_lang_code).then(() => {
      // After the operator's spoken message, open mic for citizen's reply.
      // Operator messages are just descriptions — not yes/no — so we route
      // the citizen's response through the full /turn pipeline (not /voice_confirm).
      awaitingConfirmation = false;
      beginAutoListen();
    });
  }
}

function addOperatorTurn(text, roman) {
  // Reuse addAITurn with the "operator" kind so the spotlight shows it.
  addAITurn(text, roman, "operator");
}

// ============================================================
// Audible cues — short tones at key transitions so the citizen
// knows the line is open / their turn / processing started.
// ============================================================
function playTone(freq = 880, durationMs = 140, volume = 0.07, type = "sine") {
  try {
    if (!audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      audioCtx = new Ctx();
    }
    if (audioCtx.state === "suspended") {
      try { audioCtx.resume(); } catch {}
    }
    const o = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    o.connect(g); g.connect(audioCtx.destination);
    o.type = type; o.frequency.value = freq;
    const t = audioCtx.currentTime;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(volume, t + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t + durationMs / 1000);
    o.start(t);
    o.stop(t + durationMs / 1000 + 0.04);
  } catch (e) { console.warn("[pratyaya] tone failed:", e); }
}
function chimeMicOpen() {
  // Short rising two-note "your turn" cue
  playTone(660, 90, 0.05);
  setTimeout(() => playTone(990, 110, 0.06), 80);
}
function chimeProcessing() {
  // Single soft tone — "I heard you, processing..."
  playTone(440, 80, 0.04);
}

// ============================================================
// TTS playback then auto-listen
// ============================================================
async function speakThenListen(text, lang) {
  await speak(text, lang);
  if (pageState !== "verified" && pageState !== "handover") {
    await beginAutoListen();
  }
}

function speak(text, lang) {
  return new Promise((resolve) => {
    if (!text) return resolve();
    setStatus("speaking", localizedHint("speaking"));
    setHint(localizedHint("speaking"));
    try { lastAudio?.pause(); } catch {}

    // Repair the lang code from the dominant script of the text, then fall
    // back to the user's selected language. This guarantees Kannada text
    // never plays through the English voice (and vice versa) even if the
    // server's speak_lang_code is wrong or missing.
    const scriptLang = detectScriptLang(text);
    const finalLang = scriptLang || lang || langCode(langPref) || "kn-IN";

    const url = `/api/tts?text=${encodeURIComponent(text)}&lang=${encodeURIComponent(finalLang)}`;
    const audio = new Audio(url);
    lastAudio = audio;
    audio.onended = () => resolve();
    audio.onerror = () => {
      toast("Voice playback failed — please read the text on screen", "danger");
      resolve();
    };
    audio.play().catch(() => {
      toast("Browser blocked audio — tap the screen and try again", "danger");
      resolve();
    });
  });
}

// ============================================================
// MIC + VAD
// ============================================================
async function beginAutoListen() {
  if (manualMicMode) return;        // user is driving the mic explicitly
  if (isRecording) return;
  await ensureAudioCtx();
  // Skip the chime when we're listening for a yes/no — a 1-second "haudu"
  // is short, and any self-played tone leaking through the mic confuses
  // Whisper into returning empty text. Visual cue (mic glow + level meter)
  // is enough.
  if (!awaitingConfirmation) {
    chimeMicOpen();
    await new Promise(r => setTimeout(r, 180));
  }
  await startRecording(/*auto*/true);
}

async function startRecording(auto) {
  if (isRecording) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    console.error("[pratyaya] mic API unavailable on this browser");
    toast("Microphone unavailable — using fallback buttons", "danger");
    if (awaitingConfirmation) $("#fallback-confirm").classList.remove("hidden");
    return;
  }
  await ensureAudioCtx();
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    });
  } catch (e) {
    console.error("[pratyaya] getUserMedia failed:", e?.name, e?.message);
    const why = e?.name === "NotAllowedError" ? "Mic permission denied — click the lock icon in the address bar to allow" :
                e?.name === "NotFoundError" ? "No microphone found — check your device" :
                e?.name === "NotReadableError" ? "Mic is busy in another app" :
                "Mic could not start (" + (e?.name || "unknown") + ")";
    toast(why, "danger");
    if (awaitingConfirmation) $("#fallback-confirm").classList.remove("hidden");
    setStatus("idle", localizedHint("ready"));
    return;
  }

  const mime =
    ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4", "audio/mpeg"]
      .find(m => MediaRecorder.isTypeSupported(m)) || "";
  try {
    mediaRec = mime
      ? new MediaRecorder(mediaStream, { mimeType: mime, audioBitsPerSecond: 64000 })
      : new MediaRecorder(mediaStream);
  } catch (e) {
    console.error("[pratyaya] MediaRecorder construction failed:", e);
    toast("Recorder failed to start — try a different browser (Chrome/Edge)", "danger");
    try { mediaStream.getTracks().forEach(t => t.stop()); } catch {}
    return;
  }

  const chunks = [];
  prosodyMax = { rate: 0, pitch: 0, loudness: 0 };
  speechWasDetected = false;
  mediaRec.ondataavailable = e => { if (e.data && e.data.size > 0) chunks.push(e.data); };
  mediaRec.onstop = () => onRecStopped(chunks);
  mediaRec.onerror = (ev) => {
    console.error("[pratyaya] MediaRecorder error:", ev?.error || ev);
  };

  // Use a small timeslice so chunks accumulate even if stop() ever fails.
  try { mediaRec.start(500); } catch (e) {
    try { mediaRec.start(); } catch (e2) {
      console.error("[pratyaya] MediaRecorder.start failed:", e2);
      try { mediaStream.getTracks().forEach(t => t.stop()); } catch {}
      toast("Could not start recording", "danger");
      return;
    }
  }
  isRecording = true;
  recordingStartedAt = performance.now();
  $("#mic-btn").classList.add("recording");
  // Different hint when we're listening for a yes/no answer vs. an open
  // description — tells the citizen exactly what's expected.
  const hintKey = awaitingConfirmation ? "confirm" : "listening";
  setStatus("listening", localizedHint(hintKey));
  setHint(localizedHint(hintKey));
  console.log(`[pratyaya] mic open: mime=${mime || "default"} tracks=${mediaStream.getAudioTracks().length} mode=${awaitingConfirmation ? "confirm" : "describe"}`);

  setupVAD(mediaStream);
}

function stopRecording(silent) {
  if (!isRecording) return;
  isRecording = false;
  $("#mic-btn").classList.remove("recording");
  cancelAnimationFrame(levelRAF);
  $("#level-meter .fill").style.width = "0%";
  try { mediaRec?.stop(); } catch {}
  if (mediaStream) mediaStream.getTracks().forEach(t => t.stop());
  mediaStream = null;
  if (!silent) setStatus("processing", localizedHint("processing"));
}

// Voice Activity Detection: stop recording after enough silence following speech.
// During confirmation (yes/no) we use a much shorter silence window so a
// quick "haudu" cuts off and uploads in ~1.5s instead of ~3s.
async function setupVAD(stream) {
  const silenceHangMs = awaitingConfirmation ? 1200 : SILENCE_HANG_MS;
  const phraseMinMs = awaitingConfirmation ? 250 : PHRASE_MIN_MS;
  await ensureAudioCtx();
  let src;
  try {
    src = audioCtx.createMediaStreamSource(stream);
  } catch (e) {
    console.error("[pratyaya] createMediaStreamSource failed:", e);
    return;
  }
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.2;
  src.connect(analyser);

  const data = new Uint8Array(analyser.fftSize);
  const freq = new Uint8Array(analyser.frequencyBinCount);
  const startedAt = performance.now();
  let speechStartedAt = 0;
  let lastVoiceAt = 0;
  let zeroCrossings = 0;
  let prevSample = 0;
  let peakRms = 0;
  let lastLog = 0;

  function tick() {
    if (!isRecording) return;
    analyser.getByteTimeDomainData(data);
    analyser.getByteFrequencyData(freq);
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sum += v * v;
      if ((prevSample <= 0 && v > 0) || (prevSample >= 0 && v < 0)) zeroCrossings++;
      prevSample = v;
    }
    const rms = Math.sqrt(sum / data.length);
    if (rms > peakRms) peakRms = rms;
    // Auto-scale the meter against the running peak so even a quiet mic
    // shows visible movement instead of a flat line.
    const meterScale = Math.max(0.04, peakRms * 1.5);
    const loud = Math.min(1, rms / meterScale);
    const fillEl = document.querySelector("#level-meter .fill");
    if (fillEl) fillEl.style.width = (loud * 100).toFixed(0) + "%";

    const now = performance.now();
    if (rms > VAD_RMS_THRESHOLD) {
      if (!speechStartedAt) {
        speechStartedAt = now;
        speechWasDetected = true;
        console.log(`[pratyaya] VAD: speech detected at ${(now - startedAt).toFixed(0)}ms (rms=${rms.toFixed(4)})`);
      }
      lastVoiceAt = now;
    }
    // Periodic VAD diagnostics (once a second) so we can see what's happening
    if (now - lastLog > 1000) {
      lastLog = now;
      console.log(`[pratyaya] VAD t+${((now - startedAt)/1000).toFixed(1)}s rms=${rms.toFixed(4)} peak=${peakRms.toFixed(4)} speech=${speechStartedAt ? "yes" : "no"}`);
    }

    let maxIdx = 0, maxV = 0;
    for (let i = 4; i < freq.length / 2; i++) {
      if (freq[i] > maxV) { maxV = freq[i]; maxIdx = i; }
    }
    const dominantHz = (maxIdx / freq.length) * (audioCtx.sampleRate / 2);
    const pitchNorm = Math.min(1, dominantHz / 600);
    const rate = Math.min(1, (zeroCrossings / data.length) / 0.4);
    zeroCrossings = 0;
    prosodyMax.rate = Math.max(prosodyMax.rate, rate);
    prosodyMax.pitch = Math.max(prosodyMax.pitch, pitchNorm);
    prosodyMax.loudness = Math.max(prosodyMax.loudness, loud);

    const elapsed = now - startedAt;
    const sinceVoice = lastVoiceAt ? (now - lastVoiceAt) : 0;
    const speechMs = speechStartedAt ? (now - speechStartedAt) : 0;
    const enoughSpeech = speechMs >= phraseMinMs;
    const silenceLong = lastVoiceAt && sinceVoice >= silenceHangMs;
    // Three reasons to stop:
    //   1. They spoke long enough AND have now been silent for SILENCE_HANG_MS
    //   2. Hard recording cap hit
    //   3. We've been listening for NO_SPEECH_HARD_FLOOR_MS and never got any
    //      speech at all → bail out so the user isn't stuck
    const noSpeechAtAll = !speechStartedAt && elapsed >= NO_SPEECH_HARD_FLOOR_MS;
    if ((enoughSpeech && silenceLong) || elapsed >= HARD_LIMIT_MS || noSpeechAtAll) {
      console.log(`[pratyaya] VAD stop: enoughSpeech=${enoughSpeech} silenceLong=${silenceLong} hardLimit=${elapsed >= HARD_LIMIT_MS} noSpeechAtAll=${noSpeechAtAll} elapsed=${(elapsed/1000).toFixed(1)}s peakRms=${peakRms.toFixed(4)}`);
      stopRecording(false);
      return;
    }
    levelRAF = requestAnimationFrame(tick);
  }
  tick();
}

async function onRecStopped(chunks) {
  if (!chunks.length) {
    console.warn("[pratyaya] MediaRecorder produced 0 chunks");
    toast("Mic captured nothing — please try again", "danger");
    setStatus("idle", localizedHint("ready"));
    setTimeout(() => beginAutoListen(), 600);
    return;
  }
  const blob = new Blob(chunks, { type: chunks[0].type || "audio/webm" });
  const recDur = recordingStartedAt ? (performance.now() - recordingStartedAt) / 1000 : 0;
  console.log(`[pratyaya] recorded blob ${blob.size} bytes (${blob.type}) duration=${recDur.toFixed(1)}s speechDetected=${speechWasDetected}`);

  // Don't dismiss audio just because it's small — even a 1-second utterance
  // in opus can be ~3–6 KB, but a clear "haudu" can be ~1 KB. Only reject
  // if there's essentially nothing in the buffer at all.
  if (blob.size < MIN_BLOB_BYTES) {
    if (!speechWasDetected) {
      toast(`Didn't hear anything — please speak louder, closer to the mic`, "danger");
    } else {
      toast(`Audio too short (${blob.size} bytes) — please try again`, "danger");
    }
    setStatus("idle", localizedHint("didntcatch"));
    setHint(localizedHint("didntcatch"));
    setTimeout(() => beginAutoListen(), 700);
    return;
  }
  chimeProcessing();   // audible cue: "I heard you, processing"

  const fd = new FormData();
  fd.append("audio", blob, "audio.webm");
  fd.append("language_hint", langPref);

  try {
    // Single conversational endpoint — one LLM call per turn with full
    // chat history. The server decides whether the next state is ask /
    // verify / guide / handover; we no longer juggle a yes/no mode flag
    // on the client. This is the architecture that makes Example 2
    // (mid-call correction) and contextual yes/no actually work.
    fd.append("prosody", JSON.stringify({
      speaking_rate_norm: prosodyMax.rate,
      pitch_variance_norm: prosodyMax.pitch,
      loudness_norm: prosodyMax.loudness,
    }));
    const r = await api(`/api/calls/${callId}/converse`, { method: "POST", body: fd });
    console.log("[pratyaya] /converse →", r);
    const got = r.transcript_native || "";
    if (got) {
      addCitizenTurn(got, r.transcript_roman);
    } else if (r.asr_empty) {
      console.warn(`[pratyaya] ASR empty on ${blob.size}b blob, retrying mic`);
      setTimeout(() => beginAutoListen(), 400);
    }
    // Server pushes the AI reply via WebSocket (ai_response or handover).
  } catch (e) {
    console.error(e);
    toast("Network error — try again", "danger");
    setStatus("idle", localizedHint("ready"));
  }
}

// ============================================================
// MANUAL CONTROLS (push-to-talk fallback)
// ============================================================
// Tapping the mic button while idle starts recording.
// Tapping while recording forces an immediate stop+send — useful if VAD
// hasn't tripped yet and the user is sure they're done. The TTS playback
// is also cut here so a long greeting can be skipped past.
async function toggleManualMic() {
  // Stop any in-flight TTS so the mic isn't fighting the speakers.
  try { lastAudio?.pause(); } catch {}
  manualMicMode = true;
  if (isRecording) {
    console.log("[pratyaya] manual stop (force send)");
    stopRecording(false);
  } else {
    console.log("[pratyaya] manual start");
    await ensureAudioCtx();
    await startRecording(false);
    // After a manual cycle completes, allow auto-listen to resume.
    setTimeout(() => { manualMicMode = false; }, 250);
  }
}

async function confirmManually(resp) {
  if (!callId) return;
  // Map the button to a natural-language response in the citizen's
  // language so the LLM gets a real, contextual signal — not a
  // structured token. The conversational layer interprets it against
  // the last question we asked.
  const phrase =
    (langPref || "kn").startsWith("hi")
      ? { yes: "हाँ", partial: "थोड़ा सही है", no: "नहीं" }[resp]
      : (langPref || "kn").startsWith("en")
        ? { yes: "yes", partial: "partially", no: "no" }[resp]
        : { yes: "ಹೌದು", partial: "ಸ್ವಲ್ಪ ಸರಿ", no: "ಇಲ್ಲ" }[resp];
  try {
    addCitizenTurn(phrase);
    const r = await api(`/api/calls/${callId}/converse_text`, {
      method: "POST",
      body: JSON.stringify({ text: phrase }),
    });
    setStateTag(r.state || "CLARIFY");
    $("#fallback-confirm")?.classList.add("hidden");
    showConfirmBar(false);
  } catch { toast("Failed", "danger"); }
}

// Demo bank (dev) — text-only path through the conversational pipeline.
async function useScenario(btnEl) {
  if (!callId) return;
  const text = btnEl.textContent.trim();
  addCitizenTurn(text);
  setStatus("processing", localizedHint("processing"));
  try {
    const r = await api(`/api/calls/${callId}/converse_text`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    setStateTag(r.state || "CLARIFY");
  } catch { toast("Demo failed", "danger"); }
}

// ============================================================
// Conversation rendering
// ============================================================
function addCitizenTurn(text, roman) {
  // Update the "You said" card (visible)
  const card = $("#last-citizen");
  if (card) {
    card.classList.remove("hidden");
    $("#last-citizen-text").textContent = text || "";
    const r = $("#last-citizen-roman");
    if (roman && roman !== text) {
      r.textContent = roman; r.style.display = "block";
    } else {
      r.style.display = "none";
    }
  }
  // Append to history (foldable)
  const el = $("#transcript");
  if (!el) return;
  const div = document.createElement("div");
  div.className = "turn citizen";
  const romHtml = (roman && roman !== text) ? `<div class="roman">${escapeHTML(roman)}</div>` : "";
  div.innerHTML = `<div class="who">You</div><div class="text">${escapeHTML(text)}</div>${romHtml}`;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function addAITurn(text, roman, kind) {
  if (!text) return;
  // Update the spotlight (the BIG center element)
  const sp = $("#ai-spotlight");
  if (sp) {
    sp.classList.remove("empty","handover","verified","operator");
    if (kind === "handover") sp.classList.add("handover");
    else if (kind === "verified") sp.classList.add("verified");
    else if (kind === "operator") sp.classList.add("operator");
    $("#ai-spotlight-icon").textContent =
      kind === "handover" ? "🤝" :
      kind === "operator" ? "🧑‍💼" :
      kind === "verified" ? "✅" : "🔊";
    $("#ai-spotlight-who").textContent =
      kind === "operator" ? "Officer" : "Pratyaya";
    $("#ai-spotlight-text").textContent = text;
    const rom = $("#ai-spotlight-roman");
    if (roman && roman !== text) {
      rom.textContent = roman; rom.classList.add("show");
    } else {
      rom.classList.remove("show");
    }
  }
  // Append to history (foldable)
  const el = $("#transcript");
  if (!el) return;
  const div = document.createElement("div");
  div.className = "turn ai " + (kind || "");
  const romHtml = (roman && roman !== text) ? `<div class="roman">${escapeHTML(roman)}</div>` : "";
  div.innerHTML = `<div class="who">${kind === "operator" ? "Officer" : "Pratyaya"}</div>` +
                  `<div class="text">${escapeHTML(text)}</div>${romHtml}`;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

// ============================================================
// Exports
// ============================================================
window.startCall = startCall;
window.hangup = hangup;
window.toggleManualMic = toggleManualMic;
window.confirmManually = confirmManually;
window.useScenario = useScenario;
