# Pratyaya — Product Design Document

> **Pratyaya** (Sanskrit: *conviction, verified understanding*) — an AI co-pilot
> for Karnataka's 1092 women & child helpline. Sits between the citizen and the
> human officer, ensuring the issue is heard, interpreted, and verified
> **before** any response is committed.

| | |
|---|---|
| **Owner** | Department of Personnel and Administrative Reforms (e-Governance), Government of Karnataka |
| **Helpline** | 1092 — women & child distress |
| **Languages** | Kannada, Hindi, English (extensible to other Indian languages) |
| **Dialects** | Bangalore, Dharwad, Mangaluru, Hyderabad-Karnataka |
| **Posture** | AI-assistive only · never auto-resolves · always verifies · gracefully escalates |

---

## 1. System architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                           CITIZEN                                  │
│  Web microphone (browser) · OR · PSTN inbound (Twilio/Exotel*)    │
└───────────────────────────────┬────────────────────────────────────┘
                                │ audio chunks (16kHz mono opus)
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│  PRATYAYA ORCHESTRATOR (FastAPI · async · WebSocket fan-out)       │
│                                                                    │
│  1. PII redaction at edge   ── pii.py (regex; phone/Aadhaar/email) │
│  2. ASR (parallel)          ── asr.py    + helpline-prompt prime   │
│                                  Whisper Large v3 ⟷ IndicConformer*│
│  3. Dialect classification  ── dialect.py (lexical + LLM)          │
│  4. NLU + intent + sentiment─ nlu.py (Sarvam-style structured ext) │
│  5. Sentiment fusion (6-D)  ── sentiment.py (lexical + prosodic)   │
│  6. State machine           ── state_machine.py                    │
│       VERIFIED · CLARIFY · HANDOVER                                │
│  7. Verification layer      ── verification.py                     │
│       paraphrase · classify yes/no/partial · guidance · bridge     │
│  8. Hash-chained audit      ── audit.py (SHA-256 chain)            │
│  9. Continuous learning     ── learning.py (corrections logged)    │
└───────────────────────────────┬────────────────────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
       ┌────────────┐    ┌────────────┐    ┌────────────┐
       │ TTS (Edge  │    │ WebSocket  │    │ Telegram   │
       │ Neural —   │    │ broadcast  │    │ notifier   │
       │ Sapna /    │    │ to agent + │    │ (free, on  │
       │ Swara /    │    │ citizen    │    │ handover)  │
       │ Neerja)    │    │ tabs       │    │            │
       └─────┬──────┘    └─────┬──────┘    └────────────┘
             │                 │
             ▼                 ▼
       ┌────────────────┐ ┌────────────────┐
       │  CITIZEN UI    │ │  AGENT UI      │
       │  (browser)     │ │  (browser)     │
       └────────────────┘ └────────────────┘

  * IndicConformer + Shrutam2 + Sarvam-1 are the production swap
    points. Each lives in one service file; orchestrator is unchanged.
```

### Storage layer

A pluggable backend dispatcher (`backend/db.py`) routes every operation to
either **SQLite** (local dev) or **Postgres** (Supabase). Schema is
identical between backends. One env var (`DB_BACKEND`) flips the switch.

Tables:
- `calls` — every call (id, started_at, agent, lang_pref, final_state, summary)
- `turns` — every conversational turn (transcript, dialect, interpretation, sentiment, state)
- `audit_ledger` — append-only hash chain (every action, with prev_hash + hash)
- `corrections` — agent / citizen overrides (continuous learning signal)
- `confirmations` — yes/no/partial responses (validated learning signal)

### Privacy & jurisdiction

- **PII redaction at the edge.** Phone numbers, Aadhaar, email, long digit
  blocks are redacted in `pii.py` *before* the transcript reaches the LLM
  or storage.
- **Audit chain integrity.** Every action's hash includes the previous
  row's hash. Tampering with any historical row breaks verification.
  Endpoint: `GET /api/audit/verify`.
- **Indian-jurisdiction model story.** The architecture treats the LLM
  layer as a swap point. Production: AI4Bharat IndicConformer + BharatGen
  Shrutam2 + Sarvam-1 + Wav2Vec2 + open-source Edge TTS, all hostable on
  MeghRaj or State Data Centre.
- **No closed-source critical path** in the production target.

---

## 2. UX flow (citizen journey)

```
START
  │
  ▼
[Pick language]  ─►  Connect call  ─►  AI greets in selected language
                                                  │
                                                  ▼
                                      ┌─── LISTENING ◄────────┐
                                      │   (mic auto-open)      │
                                      │                        │
                                      │   citizen speaks       │
                                      │                        │
                                      ▼                        │
                                  PROCESSING                   │
                                      │                        │
                                      ▼                        │
                                  VERIFYING                    │
                                  (AI paraphrases               │
                                   + asks "is this              │
                                   correct?")                   │
                                      │                        │
                          ┌───────────┼───────────┐             │
                          │           │           │             │
                       HAUDU       PARTIAL       ILLA           │
                          │           │           │             │
                          ▼           ▼           ▼             │
                    [severity check]  CLARIFY    CLARIFY ───────┘
                          │             │
              ┌───────────┴───┐         │
              │               │         │
            severe          mild        │
              │               │         │
              ▼               ▼         │
           HANDOVER       CONFIRMED ────┘ (next concern OR end)
              │               │
              │               ▼
              │           AI gives concrete guidance + ends call
              ▼
       Bridge line spoken in citizen's language
              │
              ▼
       Telegram ping → human officer's phone
              │
              ▼
       Agent dashboard: full context pre-populated
              │
              ▼
       Operator types/speaks English → translated → spoken to citizen
              │
              ▼
       Two-way voice loop with operator-AI bridge
              │
              ▼
       END CALL
```

### State definitions (visible to citizen + agent)

| State | Citizen meaning | Agent meaning |
|---|---|---|
| **LISTENING** | mic open, speak now | system capturing audio |
| **PROCESSING** | system understanding | ASR + NLU + sentiment running |
| **VERIFYING** | AI is paraphrasing back | paraphrase generated, awaiting confirmation |
| **CONFIRMED** | issue understood, AI giving guidance | state=VERIFIED, success guidance fired |
| **CLARIFYING** | AI didn't get it right, retry | state=CLARIFY, asking again |
| **HANDOVER** | connecting to a human officer | bridge line spoken, operator panel active, Telegram pinged |

---

## 3. UI structure (component breakdown)

### A. Citizen Interaction Panel (`/citizen`)

```
┌──────────────────────────────────────────────────┐
│  GOV BANNER                                      │
│  Government of Karnataka · 1092 Helpline         │
├──────────────────────────────────────────────────┤
│  STATE BANNER (full-width)                       │
│  🟢 LISTENING   ·   ಆಲಿಸುತ್ತಿದ್ದೇನೆ              │
├──────────────────────────────────────────────────┤
│                                                  │
│  AI MESSAGE SPOTLIGHT                            │
│  ╔════════════════════════════════════════════╗ │
│  ║  ನಿಮ್ಮ ಗಂಡ ನಿಮ್ಮನ್ನು ಹೊಡೆಯುತ್ತಾರೆ —          ║ │
│  ║  ಸರಿಯೇ?                                    ║ │
│  ║  nimma ganda nimmannu hodeyutaare — sariye? ║ │
│  ╚════════════════════════════════════════════╝ │
│                                                  │
│  ┌────────────────┐                              │
│  │      🎙        │   ← big mic                  │
│  └────────────────┘                              │
│  ▓▓▓▓▓▓░░░░░░░░░░  ← live audio level           │
│                                                  │
│  YOU SAID:                                       │
│  "nanna ganda kudidu bandu hodeyutaane"          │
│                                                  │
│  CONFIRMATION (only when AI is verifying):       │
│  [✔ Haudu]   [~ Partial]   [✖ Illa]              │
│                                                  │
│  ──────── conversation history (foldout) ─────── │
│  ──────── demo scenarios (foldout) ───────────── │
│                                                  │
│  [End call]                                      │
└──────────────────────────────────────────────────┘
```

### B. Agent Dashboard (`/agent`) — three zones

```
┌────────────────────────────────────────────────────────────┐
│  GOV BANNER · Agent Dashboard · State: ⬤ CLARIFY · ⏱ 02:14 │
├────────────┬──────────────────────────┬────────────────────┤
│            │                          │                    │
│  ZONE A    │   ZONE B                 │   ZONE C           │
│  CITIZEN   │   AI UNDERSTANDING       │   AGENT CONTROL    │
│  MIRROR    │                          │                    │
│            │   • Detected language    │   • Take over      │
│  • Live    │   • Dialect              │   • Correct AI     │
│    calls   │   • Confidence (4-bar)   │   • End call       │
│  • Stats   │   • Sentiment 6-D        │   • Export         │
│  • Ledger  │   • Live transcript      │                    │
│            │   • Multilingual summary │   • Operator reply │
│            │   • Editable fields      │     (text + 🎙)    │
│            │                          │                    │
└────────────┴──────────────────────────┴────────────────────┘
```

### C. Civic Sensor (`/analytics`)

Real-time aggregation: total calls, verified rate, handover rate, avg
distress, dialect mix, issue type breakdown, recent calls table,
sentiment heat-map. Updates every 6 seconds.

---

## 4. State machine (formal)

### Inputs
- `asr_conf` ∈ [0,1] — Whisper segment-weighted confidence
- `intent_conf` ∈ [0,1] — NLU's self-rated confidence
- `dialect_conf` ∈ [0,1] — dialect classifier confidence
- `sentiment` — {distress, urgency, anger, fear, confusion, calm}
- `needs_clarification` — NLU flag for genuine ambiguity
- `consecutive_no` — count of consecutive citizen rejections
- `citizen_asked_for_human` — keyword detection

### Outputs
- `state` ∈ {VERIFIED, CLARIFY, HANDOVER}
- `overall_confidence` = ∛(asr · intent · dialect)
- `reasons` (audit-loggable list)

### Decision rules (priority order)

```python
1. citizen_asked_for_human     → HANDOVER  (manual request always wins)
2. consecutive_no ≥ 2          → HANDOVER  (citizen rejected twice)
3. overall < 0.30              → HANDOVER  (cannot interpret reliably)
4. distress ≥ 0.85 ∧ fear ≥ 0.55 → HANDOVER (acute distress)
5. issue_type ∈ SEVERE_SET ∧ verified → HANDOVER (severe-issue auto-route)
6. otherwise                   → CLARIFY  (always seek confirmation)
```

`SEVERE_SET = {domestic_violence, child_safety, missing_person,`
`             sexual_harassment, stalking, cyber_abuse, medical_emergency}`

### Empty-audio guard
If Whisper returns empty, the system bypasses the state machine and
politely re-prompts the citizen — it does **not** auto-handover.
Empty-handover would be a UX failure.

### Post-handover guard
Once `state == HANDOVER`, subsequent citizen audio is transcribed for
the operator dashboard but does **not** re-trigger AI processing.

---

## 5. Telephony flow (X → Y)

### Demo (current)
```
Citizen browser mic (X = web)
        │
        ▼
   Pratyaya backend
        │
        ▼
   Browser audio output (Y = web)
```

### Production (one swap, ~1 day)
```
Citizen mobile  ──[PSTN call]──►  Twilio / Exotel  ──[TwiML <Stream>]──►  Pratyaya
                                                                              │
                                                                              ▼
                                                                       State machine
                                                                              │
                                                            ┌─────────────────┴─────────────────┐
                                                            │                                   │
                                                          AI loop                          HANDOVER
                                                            │                                   │
                                                            ▼                                   ▼
                                                    Edge TTS over PSTN              Twilio <Dial> to officer's
                                                                                    phone (Y) + bridge audio
```

### Triggers for X → Y forwarding
1. Severity-gated (issue ∈ SEVERE_SET, verified)
2. Citizen explicitly requests human ("ಮನುಷ್ಯ", "मानव", "human", "agent")
3. 2 consecutive citizen rejections
4. Overall interpretation confidence < 0.30
5. Acute distress (≥0.85) + fear (≥0.55)
6. Operator clicks **⤴ Hand over**

### Handover protocol
1. Backend computes severity / reason
2. Speak bridging line to citizen in their language: *"ಒಂದು ಕ್ಷಣ ತಡೆಯಿರಿ. ನಮ್ಮ ಮಾನವ ಅಧಿಕಾರಿಗೆ ಸಂಪರ್ಕಿಸುತ್ತಿದ್ದೇನೆ. ನಿಮ್ಮ ಸಮಸ್ಯೆ ಅವರಿಗೆ ಈಗಾಗಲೇ ತಿಳಿಸಲಾಗಿದೆ."*
3. Telegram notification fires to operator's phone with full case context (free, no rate limit) — `notify.py`
4. Agent dashboard turns red, operator-reply panel becomes primary surface
5. Operator joins the call (PSTN) OR types/speaks English → translated → spoken to citizen
6. Audit ledger logs every step with hash chain

---

## 6. Sample interaction script (Kannada DV scenario)

```
T+0:00   System  →  ನಮಸ್ಕಾರ. 1092 ಸಹಾಯವಾಣಿ. ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಹೇಳಿ.
                   namaskaara. 1092 sahaayavaani. nimma samasyeyannu heli.
                   "Hello. 1092 helpline. Please describe your problem."
                   [State: LISTENING] [chime: ding-dong, citizen's turn]

T+0:08   Citizen →  ನನ್ನ ಗಂಡ ರೋಜು ಕುಡಿದು ಬಂದು ಹೊಡೆಯುತ್ತಾನೆ. ಬೆಂಗಳೂರು ಜಯನಗರದಲ್ಲಿ ಇದ್ದೀನಿ.
                   "My husband comes home drunk every day and beats me. I'm in Jayanagar."
                   [VAD detects 1.3s silence — recording stops]

T+0:14   System  →  [chime: processing tone]
                   [State: PROCESSING]
                   ASR · dialect · NLU · sentiment all run in parallel
                     - dialect: Bangalore-standard-Kannada (conf 0.93)
                     - issue_type: domestic_violence (deterministic override fired)
                     - urgency: high
                     - distress 0.88 · fear 0.62 · calm 0.05
                     - overall_conf: 0.87

T+0:18   System  →  ನಿಮ್ಮ ಗಂಡ ಪ್ರತಿದಿನ ಕುಡಿದು ನಿಮಗೆ ಹೊಡೆಯುತ್ತಾರೆ — ಸರಿಯೇ?
                   "Your husband drinks daily and beats you — is this correct?"
                   [State: VERIFYING] [chime: ding-dong, citizen's turn]

T+0:24   Citizen →  ಹೌದು
                   "haudu" (yes)

T+0:25   Pratyaya:
              classify_response("haudu") → "yes"
              issue_type=domestic_violence (severe) → guidance + handover
              generate_guidance() → produces operator-style empathetic message
              audit append: voice_confirmation { yes }
              audit append: handover { reason: severe_issue_verified }

T+0:28   System  →  ನೀವು ಕರೆ ಮಾಡಿದ್ದು ಸರಿ ಮಾಡಿದ್ರಿ. ಈಗ ಬೀಗ ಹಾಕಬಹುದಾದ ಕೋಣೆಗೆ ಹೋಗಿ
                   ಫೋನ್ ಹತ್ತಿರ ಇಟ್ಟುಕೊಳ್ಳಿ. ನಮ್ಮ ಮಹಿಳಾ ಅಧಿಕಾರಿ ಐದು ನಿಮಿಷದಲ್ಲಿ
                   ನಿಮಗೆ ಕಾಲ್ ಮಾಡ್ತಾರೆ — ನೀವು ಲೈನ್‌ನಲ್ಲಿ ಇರಿ.
                   [State: HANDOVER (red)]

T+0:29   Telegram →  🚨 Pratyaya · HUMAN HANDOVER
                    Call ID: call_xyz · Language: kn · Dialect: Bangalore-standard-Kannada
                    Issue: domestic_violence · Urgency: high · Location: Jayanagar
                    Distress: 0.88
                    Citizen said: "ನನ್ನ ಗಂಡ ಪ್ರತಿದಿನ ಕುಡಿದು..."
                    Why escalated: severe_issue_verified
                    ➡ Open in agent dashboard

T+0:32   Operator (English, types or speaks via mic):
              "Don't worry. I'm Sangeetha from the helpline.
               Where exactly in Jayanagar are you? 4th block?"

T+0:35   System translates → ನಿಮಗೆ ಭಯ ಇಲ್ಲ. ನಾನು ಸಹಾಯವಾಣಿಯಿಂದ ಸಂಜೀತಾ.
                            ಜಯನಗರದಲ್ಲಿ ಎಲ್ಲಿ ಇದ್ದೀರಿ? ನಾಲ್ಕನೇ ಬ್ಲಾಕ್?
              Citizen hears it in natural Kannada (Sapna Neural voice)

T+0:42   Citizen → "haudu, naalkane block aagide"
                   [post-handover audio routed to operator dashboard
                    only — no further AI processing]

  ... continues until operator resolves and ends call ...
```

---

## 7. What changed vs prototype (explicit improvements)

| Area | Prototype | Production |
|---|---|---|
| **Visual identity** | Saffron-cyan-pink gradient (marketing-style) | Government-institutional navy + saffron accent + structured panels |
| **State communication** | Implicit | Explicit state banner on every surface (LISTENING / PROCESSING / VERIFYING / CONFIRMED / CLARIFYING / HANDOVER) |
| **Agent dashboard layout** | Three columns of mixed panels | Three explicitly-labeled zones: A. Citizen Mirror · B. AI Understanding · C. Agent Control |
| **Citizen UI** | Multiple panels competing for attention | One dominant element at a time (state banner → AI message → mic → confirmation), history folded |
| **AI responses (verified)** | "Your problem is recorded." | Operator-style empathetic 3-beat structure: acknowledge feeling · concrete safety action · clear next step |
| **Severity-gated handover** | Rigid (any distress = handover) | Issue-type aware (DV / child / missing → escalate; neighbor fight / info → AI handles directly) |
| **Empty-audio path** | Triggered HANDOVER | Politely re-prompts in citizen's language, no escalation |
| **Operator workflow** | View only | English in → translated → spoken Kannada/Hindi to citizen via Edge TTS; voice mic via browser STT |
| **Phone notification** | None | Telegram bot ping with full case context, free, no rate limit |
| **Documentation** | Code comments | Single design doc covering architecture, state machine, telephony flow, interaction script |

---

## 8. Code structure (post-refactor)

```
backend/
├── main.py                  ← FastAPI app + WebSocket + state-machine glue
├── config.py                ← env loader (DB_BACKEND, GROQ_API_KEY, TELEGRAM_*)
├── state_machine.py         ← decide() — VERIFIED / CLARIFY / HANDOVER rules
├── session.py               ← in-memory active calls + WS fan-out
├── models.py                ← Pydantic schemas
├── db.py                    ← dispatcher (sqlite | postgres)
├── db_sqlite.py             ← SQLite backend
├── db_pg.py                 ← Supabase Postgres backend (parity interface)
└── services/
    ├── asr.py               ← Whisper + helpline prompt prime + segment-confidence aggregation
    ├── nlu.py               ← Sarvam-style structured extraction + deterministic safety net
    ├── dialect.py           ← lexical + LLM dialect classifier (4-way Kannada)
    ├── sentiment.py         ← 6-D lexical + prosodic fusion
    ├── verification.py      ← paraphrase · classify_response · generate_guidance · handover_bridge
    ├── pii.py               ← edge-redaction (phone / Aadhaar / email / long digit)
    ├── audit.py             ← SHA-256 hash chain + verifier
    ├── learning.py          ← confirmations & corrections capture
    ├── translit.py          ← Indic→Roman (IAST scheme)
    ├── tts.py               ← Edge Neural voices (Sapna / Swara / Neerja)
    ├── notify.py            ← Telegram bot notifier (free, lazy env)
    ├── groq_client.py       ← async wrapper for Whisper + Llama
    └── __init__.py

frontend/
├── landing.html             ← public-facing overview
├── index.html               ← agent dashboard (3-zone layout)
├── citizen.html             ← citizen call interface (1-element-focus)
├── analytics.html           ← civic-sensor live stats
├── style.css                ← government palette + design tokens
├── theme.js                 ← light/dark switcher (persisted)
├── sidebar.js               ← shared sidebar nav
├── app.js                   ← agent dashboard controller
├── citizen.js               ← citizen voice loop + VAD
└── analytics.js             ← analytics page controller

data/
└── pratyaya.db              ← SQLite (dev / fallback)

DESIGN.md                    ← this document
README.md                    ← deployment + run instructions
.env                         ← API keys (never committed)
.gitignore
requirements.txt
run.bat / run.sh
```

### Key endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/calls/start` | begin a new call session |
| POST | `/api/calls/{id}/turn` | upload an audio chunk (full pipeline) |
| POST | `/api/calls/{id}/voice_confirm` | upload haudu/illa audio (yes/no/partial classifier) |
| POST | `/api/calls/{id}/scripted_turn` | inject typed text (demo bypass) |
| POST | `/api/calls/{id}/confirm` | button-based yes/no/partial (fallback) |
| POST | `/api/calls/{id}/correct` | agent overrides any field |
| POST | `/api/calls/{id}/handover` | manual handover by operator |
| POST | `/api/calls/{id}/operator_message` | operator types English → translated + spoken to citizen |
| POST | `/api/calls/{id}/end` | close call session |
| POST | `/api/calls/{id}/summary` | trigger multilingual summary refresh |
| POST | `/api/calls/{id}/shadow` | toggle shadow-mode (rollout safety) |
| GET | `/api/calls` | list recent calls |
| GET | `/api/calls/{id}` | full call detail |
| GET | `/api/calls/{id}/audit` | per-call audit ledger |
| GET | `/api/audit/verify` | verify global hash chain |
| GET | `/api/analytics` | civic-sensor aggregation |
| GET | `/api/tts?text=…&lang=…` | stream Edge Neural TTS audio |
| WS | `/ws/citizen/{id}` | citizen UI live feed |
| WS | `/ws/dashboard/{id}` | per-call agent dashboard live feed |
| WS | `/ws/dashboard` | global dashboard (call list updates) |

---

## Deployment posture

- **Containerizable** (FastAPI + uvicorn, single image)
- **Stateless application layer** (sessions in-memory; calls + audit persist in DB)
- **Horizontally scalable** (Postgres backend, no shared local state required for HA)
- **Cloud-target**: MeghRaj or Karnataka State Data Centre (no foreign cloud dependency in production target)
- **Backups**: hash-chained audit means tamper detection survives backup/restore
- **Observability**: every action emits an audit row + WebSocket event; metrics endpoint roadmap

---

> *"The citizen is heard. The understanding is verified. The agent always knows
> what the AI thinks before the AI ever speaks."*
