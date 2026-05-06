# Pratyaya

> AI-assistive voice-to-voice layer for the **Karnataka 1092 Women & Child
> Helpline**. Sits between the citizen and the human officer, ensuring every
> issue is heard, interpreted, and **verified** before any response is committed.

**Owner**  Department of Personnel and Administrative Reforms (e-Governance), Government of Karnataka
**Helpline**  1092 — women & child distress
**Posture**  AI-assistive only · never auto-resolves · always verifies · gracefully escalates

---

## What this is

A complete, government-grade voice-to-voice helpline assistant that:

1. **Listens** to a citizen speaking naturally in Kannada, Hindi, English, or code-mixed input — with awareness of Bangalore, Dharwad, Mangaluru and Hyderabad-Karnataka Kannada dialects.
2. **Interprets** the issue with sentiment, urgency, location, and named-entity awareness.
3. **Restates** what it understood — in the citizen's own language — and asks them to confirm.
4. **Verifies** explicitly: *haudu / illa / partial*. No action without confirmation.
5. **Helps** directly with concrete guidance for non-severe issues (info, witness reports, minor grievances).
6. **Escalates** gracefully to a human officer for severe issues (domestic violence, child safety, missing persons, sexual harassment, stalking, cyber abuse, medical emergencies) — with full case context pre-populated on the agent dashboard and a Telegram phone notification to the on-duty officer.

For complete product design, architecture, state machine, and telephony plan,
see [`DESIGN.md`](DESIGN.md).

---

## Why this matters

> *In citizen services, the biggest failure is not lack of response — but the
> wrong response due to wrong understanding.*

A helpline operator may speak only one language well. A caller in distress may
mix dialects, drop words, or speak softly. The cost of misinterpretation here
is not inconvenience — it is harm. **Pratyaya guarantees that the operator
and the citizen mean exactly the same thing before any action is taken.**

---

## Mapped to the evaluation rubric

| Criterion | Weight | How Pratyaya satisfies it |
|---|---|---|
| Voice-to-voice effectiveness | 25 % | Whisper Large v3 + Edge Neural TTS + VAD-based auto-listen loop + audible turn-taking cues + Roman transliteration line under every Kannada/Hindi message |
| Verification & guardrails | 20 % | Explicit 3-state machine (VERIFIED · CLARIFY · HANDOVER) · voice-classified haudu/illa/partial · severity-aware escalation · SHA-256 hash-chained audit ledger · PII redaction at the call edge |
| Dialect & cultural understanding | 15 % | 4-way Kannada dialect classifier (lexical + LLM) · Whisper prompt-priming with dialect markers · paraphrases preserve dialect register · multilingual call summary in Kannada · Hindi · English |
| Sentiment & emotional interpretation | 15 % | 6-dimensional sentiment fusion (lexical + prosodic) — distress · urgency · anger · fear · confusion · calm — drives auto-handover thresholds and surfaces on the agent dashboard as live bars and a trajectory chart |
| Ease of use for agents | 15 % | Three-zone agent dashboard (Citizen Mirror · AI Understanding · Agent Control) · inline-editable interpretation fields · operator types/speaks **English**, citizen hears **Kannada/Hindi** via translation + neural TTS · keyboard shortcuts · multilingual call summary · Telegram phone notification |
| Technical design & extensibility | 10 % | Modular service architecture (one file per concern) · pluggable storage (SQLite ↔ Supabase Postgres) · async parallel pipeline · India-native model swap-points (IndicConformer / Shrutam2 / Sarvam-1 / Wav2Vec2) · open-source critical path · stateless app layer (HA-ready) |

---

## What's included

### Backend (`backend/`)
| File | Purpose |
|---|---|
| `main.py` | FastAPI app · WebSocket fan-out · pipeline orchestration |
| `state_machine.py` | VERIFIED · CLARIFY · HANDOVER decision logic |
| `session.py` | In-memory active-call sessions |
| `db.py` / `db_sqlite.py` / `db_pg.py` | Storage dispatcher with parity interface |
| `services/asr.py` | Whisper transcription with helpline vocabulary prompt |
| `services/nlu.py` | Sarvam-style structured extraction + deterministic safety net for canonical phrases |
| `services/dialect.py` | Lexical + LLM Kannada dialect classifier |
| `services/sentiment.py` | 6-D lexical + prosodic sentiment fusion |
| `services/verification.py` | Paraphrase · classify yes/no/partial · generate guidance · handover bridge |
| `services/pii.py` | Edge redaction (phone / Aadhaar / email / long-digit blocks) |
| `services/audit.py` | SHA-256 hash chain + verifier endpoint |
| `services/translit.py` | Indic → IAST Roman transliteration |
| `services/tts.py` | Microsoft Edge Neural voices (Sapna · Swara · Neerja) |
| `services/notify.py` | Telegram bot officer-on-phone notifier (free) |
| `services/learning.py` | Confirmation & correction capture for continuous learning |

### Frontend (`frontend/`)
| File | Purpose |
|---|---|
| `landing.html` | Public overview |
| `citizen.html` | Citizen call interface — government banner · state banner · AI message spotlight · mic + level meter · "you said" card · voice-fallback confirmation bar |
| `index.html` | Agent dashboard — three labeled zones (A. Citizen Mirror · B. AI Understanding · C. Agent Control) |
| `analytics.html` | Civic-sensor dashboard (issue mix · dialect distribution · sentiment heat · recent calls) |
| `style.css` | Institutional design system — navy + saffron + white, structured panels, no rainbow gradients |
| `citizen.js` | Voice loop · VAD silence detection · audible turn cues · state banner controller |
| `app.js` | Agent dashboard controller · operator reply (text + browser STT) · multilingual summary · audit verifier |
| `analytics.js` | Civic-sensor live aggregation |
| `theme.js`, `sidebar.js` | Shared shell |

### Documentation
- `DESIGN.md` — full product design (architecture · UX flow · state machine · telephony plan · interaction script · improvements list · code structure)
- `README.md` — this file

---

## Run locally

### Prerequisites
- Python 3.11+
- A Groq API key (free tier sufficient for demo) — used for Whisper Large v3 + Llama 3.3 70B
- Optional: Telegram bot token + chat ID for officer-on-phone notifications

### Quick start (Windows)
```
run.bat
```

### Quick start (Linux / macOS)
```bash
bash run.sh
```

The script creates a virtualenv, installs dependencies, and starts the
FastAPI server at <http://localhost:8000>.

### Manual setup
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Unix
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env — set GROQ_API_KEY (required), TELEGRAM_BOT_TOKEN/CHAT_ID (optional)

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Open in your browser:
- <http://localhost:8000/> — public overview
- <http://localhost:8000/citizen> — citizen call interface
- <http://localhost:8000/agent> — agent dashboard
- <http://localhost:8000/analytics> — civic-sensor dashboard

For the live demo, open the **citizen** and **agent** pages side-by-side in
two browser tabs. Pratyaya broadcasts call events between them in real time
via WebSocket.

---

## Storage backend

Pratyaya supports two storage backends with an identical interface:

- **SQLite** (default) — local file at `data/pratyaya.db`. Zero-config, ideal for the demo and offline development.
- **Supabase Postgres** — cloud, multi-machine. Set `DB_BACKEND=postgres` and the `SUPABASE_DB_*` env vars in `.env`. Schema migrates automatically on first start.

The `audit_ledger` hash chain works identically across both backends.

---

## Production swap-points (mapped explicitly)

The architecture treats every external model dependency as a **single
service-file swap**. The orchestrator does not change.

| Service file | Demo-time | Production target |
|---|---|---|
| `services/asr.py` | Groq Whisper Large v3 | AI4Bharat **IndicConformer** + BharatGen **Shrutam2** in parallel |
| `services/nlu.py` | Groq Llama 3.3 70B | **Sarvam-1** (2 B-param India-native LLM) |
| `services/dialect.py` | Lexical + Llama LLM | **Wav2Vec2** fine-tuned on IndicVoices |
| `services/tts.py` | Microsoft Edge Neural | **Bhashini TTS** or **AI4Bharat Indic-TTS** |
| Storage | SQLite | Supabase Postgres / managed Postgres on **MeghRaj** or **Karnataka State Data Centre** |
| Telephony | Browser audio | **Twilio Voice** or **Exotel** with TwiML `<Stream>` to existing pipeline |

Once swapped, the deployment runs entirely on open-source models within
Indian jurisdiction with no closed-source critical path.

---

## Privacy & compliance posture

- **PII redaction at the edge.** Phone numbers, Aadhaar, email, long digit blocks are redacted in `services/pii.py` *before* the transcript is logged or sent to the LLM.
- **Hash-chained audit ledger.** Every action (turn committed, citizen confirmation, agent correction, handover) appends a SHA-256-chained row. Tampering with any historical row breaks the verifier endpoint at `GET /api/audit/verify`.
- **Indian-jurisdiction model story.** Production target swaps Groq-cloud calls for in-country IndicConformer / Sarvam / Bhashini deployments.
- **Continuous-learning capture.** Confirmed verifications and corrections are persisted as labelled training pairs for downstream model improvement.

---

## Architecture & state machine

See [`DESIGN.md`](DESIGN.md) for the full architecture diagram, state machine,
telephony flow, and interaction-script walkthrough.

Brief:

```
Citizen voice → ASR (with helpline-prompt prime) → PII redaction
                  ↓
        ┌────────┴────────┐
        ↓                 ↓
 Dialect classifier   NLU (intent · entity · sentiment · severity)
        ↓                 ↓
        └─────────┬───────┘
                  ↓
         State machine
         (VERIFIED / CLARIFY / HANDOVER)
                  ↓
   ┌──────────────┼──────────────┐
   ↓              ↓              ↓
Verification   Bridge to       Audit chain
paraphrase +   human officer   append (SHA-256)
TTS → citizen  + Telegram ping
                  ↓
         Continuous-learning capture
         (confirmations + corrections)
```

---

## Demo flow (4 minutes)

1. Open `/citizen` and `/agent` side-by-side. Pick **Kannada** → **Start call**.
2. AI greets in Kannada (Sapna Neural). State banner: **LISTENING**. Two-tone "your turn" chime.
3. Speak: *"nanna ganda kudidu bandu hodeyutaane, Jayanagar-alli iddini."*
4. State banner: **PROCESSING** → **VERIFYING**. AI paraphrases in Kannada (with Roman line) and asks confirmation.
5. Say *"haudu"*. AI generates operator-style guidance — *"go to a room you can lock, our woman officer will call in 5 minutes…"* — and escalates because issue is severe.
6. State banner: **HANDOVER**. Telegram ping arrives on operator's phone with full case context.
7. Switch to `/agent`. State pill is red. Operator types in Zone C: *"Don't worry, I'm Sangeetha. Where exactly in Jayanagar?"* — citizen hears it spoken in Kannada via Edge TTS.
8. Citizen replies via voice. Transcript appears on agent dashboard. Operator continues by typing or by clicking the operator-mic to speak (browser STT).
9. Click `/api/audit/verify` to confirm hash chain integrity (✓ intact, N rows).
10. Open `/analytics` to show the civic-sensor view: issue distribution, dialect mix, sentiment heat, recent calls.

---

## Final note

> *"The citizen is heard. The understanding is verified. The agent always
> knows what the AI thinks before the AI ever speaks."*
