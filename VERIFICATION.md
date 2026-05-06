# Pratyaya — System Verification Checklist

## Fixed Issues (2024-12-XX)

### 1. Citizen Page — Greeting TTS Autoplay Block
**Problem:** Browser autoplay policy blocks TTS on first load → greeting is silent → citizen doesn't know to speak.  
**Fix:** Always open mic after greeting regardless of TTS success. Greeting text is shown in spotlight even if audio fails.  
**Test:** Open `/citizen`, click "Start call" → greeting text appears immediately, mic opens within 2 seconds.

### 2. Citizen Page — Mic Not Re-opening After VERIFIED Guidance
**Problem:** After manual confirmation (tap "Haudu"), AI speaks guidance but mic never re-opens → call goes silent.  
**Fix:** Changed `speak()` to `speakThenListen()` for VERIFIED state so mic auto-opens after guidance.  
**Test:** Start call → describe minor issue → tap "Haudu" → AI speaks guidance → mic re-opens automatically.

### 3. Citizen Page — Confirmation Loop Not Re-showing Confirm Bar
**Problem:** After "no" or "partial" response, confirm bar disappears → citizen can't respond again.  
**Fix:** Set `awaitingConfirmation = true` and `showConfirmBar(true)` for no/partial/unclear classifications.  
**Test:** Start call → describe issue → say "illa" → confirm bar stays visible for retry.

### 4. Agent Dashboard — Handover Bar Send Button Not Working
**Problem:** Typing in handover bar input → clicking "Send → speak" → nothing happens (message not sent).  
**Fix:** Patched handover bar send button to sync `ho-input` → `op-input` before calling `sendOperatorMessage()`.  
**Test:** Start call → trigger handover → type in handover bar input → click send → message appears in citizen UI.

### 5. Agent Dashboard — Dialect/Language Tags Blank on History Load
**Problem:** When loading call history, dialect/language tags show as blank because code used `t.dialect` instead of `t.detected_dialect`.  
**Fix:** Added fallback `t.detected_dialect || t.dialect` in `renderCallHistory`.  
**Test:** Start call → speak → refresh agent page → select call → dialect/language tags appear correctly.

### 6. Agent Dashboard — #health-line Null Reference Error
**Problem:** `health()` tries to write to `#health-line` which doesn't exist in `index.html` → silent JS error on every page load.  
**Fix:** Added null-check before writing to `#health-line`.  
**Test:** Open `/agent` → no console errors.

---

## Core Flow Verification

### Scenario 1: Minor Issue (Neighbor Fight)
1. Open `/citizen` and `/agent` side-by-side
2. Citizen: Click "Start call" → AI greets in Kannada → mic opens
3. Citizen: Speak "ನಮ್ಮ ಪಕ್ಕದ ಮನೆಯವರು ಗಲಾಟೆ ಮಾಡ್ತಾ ಇದಾರೆ" (neighbor is making noise)
4. AI: Paraphrases in Kannada → asks "ಹೌದು ಅಥವಾ ಇಲ್ಲ?"
5. Citizen: Say "ಹೌದು" (yes)
6. AI: Provides guidance in Kannada → "ನೀವು ಮಧ್ಯೆ ಪ್ರವೇಶಿಸದೆ ಸುರಕ್ಷಿತವಾಗಿ ಇರಿ..." → mic re-opens
7. Agent dashboard: State pill = VERIFIED (green), no handover bar
8. ✅ Expected: AI handles directly, no human needed

### Scenario 2: Severe Issue (Domestic Violence)
1. Open `/citizen` and `/agent` side-by-side
2. Citizen: Click "Start call" → AI greets → mic opens
3. Citizen: Speak "ನನ್ನ ಗಂಡ ಕುಡಿದು ಬಂದು ಹೊಡೆಯುತ್ತಾನೆ" (my husband drinks and beats me)
4. AI: Paraphrases → asks confirmation
5. Citizen: Say "ಹೌದು"
6. AI: Speaks guidance → "ನೀವು ಕರೆ ಮಾಡಿದ್ದು ಸರಿ ಮಾಡಿದ್ರಿ... ಒಂದು ಬೀಗ ಹಾಕಬಹುದಾದ ಕೋಣೆಗೆ ಹೋಗಿ..." → then escalates
7. Agent dashboard: Red sticky handover bar appears at top with:
   - Issue context (domestic_violence · high urgency · Kannada · location)
   - Big "Push to talk" mic button
   - Text input field
   - Live citizen audio transcript
8. Agent: Click mic → speak in English → citizen hears it in Kannada via TTS
9. ✅ Expected: Immediate handover, agent has full context, can speak/type

### Scenario 3: Language Detection
1. Citizen: Start call with Hindi → "मेरी बेटी कल से ग़ायब है" (my daughter is missing since yesterday)
2. AI: Detects Hindi → paraphrases in Hindi → "आपकी बेटी कल से ग़ायब है, क्या यह सही है?"
3. Citizen: "हाँ"
4. AI: Escalates immediately (missing_person = severe)
5. Agent dashboard: Handover bar shows "lang hi · issue missing_person · urgency high"
6. ✅ Expected: Correct language detection, immediate escalation

### Scenario 4: Distress Keyword Fast-Path
1. Citizen: Speak "ಸಹಾಯ ಮಾಡಿ, ಅವನು ನನ್ನನ್ನು ಕೊಲ್ಲುತ್ತಾನೆ" (help me, he will kill me)
2. AI: Detects danger keyword "ಸಹಾಯ" → skips paraphrase → direct handover
3. Agent dashboard: Handover bar appears immediately with "why: citizen used distress keyword (ಸಹಾಯ)"
4. ✅ Expected: No confirmation loop for active danger, instant escalation

---

## Known Limitations (By Design)

1. **TTS may be blocked on first page load** → Greeting text is shown, mic still opens
2. **Browser STT (operator mic) requires Chrome/Edge** → Fallback: type in English
3. **Groq API rate limits** → Demo works for ~50 calls/hour on free tier
4. **No real telephony integration** → Browser audio only (production: Twilio/Exotel)
5. **SQLite storage** → Single-machine only (production: Postgres on MeghRaj)

---

## Production Readiness Checklist

- [ ] Swap Groq Whisper → AI4Bharat IndicConformer + BharatGen Shrutam2
- [ ] Swap Groq Llama → Sarvam-1 (2B India-native LLM)
- [ ] Swap Edge TTS → Bhashini TTS or AI4Bharat Indic-TTS
- [ ] Swap SQLite → Supabase Postgres (set `DB_BACKEND=postgres` in `.env`)
- [ ] Integrate Twilio/Exotel for real phone calls (TwiML `<Stream>` to existing pipeline)
- [ ] Deploy on MeghRaj or Karnataka State Data Centre
- [ ] Enable Telegram bot notifications (set `TELEGRAM_BOT_TOKEN` + `CHAT_ID` in `.env`)
- [ ] Run shadow mode on live calls until verification accuracy > 85% per dialect
- [ ] Train Wav2Vec2 dialect classifier on IndicVoices dataset
- [ ] Set up continuous learning pipeline (export confirmed/corrected turns for retraining)

---

## How to Run

### Windows
```
run.bat
```

### Linux/macOS
```bash
bash run.sh
```

### Manual
```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Unix

pip install -r requirements.txt
cp .env.example .env
# Edit .env — set GROQ_API_KEY (required)

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Open:
- http://localhost:8000/ → Citizen call interface
- http://localhost:8000/agent → Agent dashboard
- http://localhost:8000/analytics → Civic-sensor dashboard

---

## Demo Script (4 minutes)

1. Open `/citizen` and `/agent` side-by-side
2. Pick Kannada → Start call
3. AI greets in Kannada (Sapna Neural voice) → mic opens automatically
4. Speak: "ನನ್ನ ಗಂಡ ಕುಡಿದು ಬಂದು ಹೊಡೆಯುತ್ತಾನೆ, ಜಯನಗರದಲ್ಲಿ ಇದ್ದೀನಿ"
5. AI paraphrases in Kannada (with Roman transliteration) → asks confirmation
6. Say "ಹೌದು"
7. AI speaks guidance → escalates (domestic_violence = severe)
8. Agent dashboard: Red handover bar appears at top with full context
9. Agent: Click mic → speak "Don't worry, I'm Sangeetha. Where exactly in Jayanagar?"
10. Citizen hears it in Kannada via TTS
11. Citizen replies via voice → transcript appears on agent dashboard
12. Open `/analytics` → shows issue distribution, dialect mix, sentiment heat

---

## Support

For questions or issues, refer to:
- `README.md` — Full product overview
- `DESIGN.md` — Architecture, state machine, telephony plan
- `backend/` — All service files are single-concern, one file per feature
- `frontend/` — Citizen UI (`citizen.html`/`citizen.js`), Agent UI (`index.html`/`app.js`)
