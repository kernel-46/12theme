"""Pratyaya — FastAPI orchestrator.

Endpoints:
  GET  /                          -> landing page
  GET  /agent                     -> agent dashboard
  GET  /citizen                   -> citizen call simulator
  POST /api/calls/start           -> start a call, returns call_id
  POST /api/calls/{id}/turn       -> upload one audio chunk (multipart)
  POST /api/calls/{id}/confirm    -> citizen confirmation (yes/no/partial)
  POST /api/calls/{id}/correct    -> agent correction of any interpretation field
  POST /api/calls/{id}/handover   -> agent-initiated handover
  POST /api/calls/{id}/end        -> end call
  GET  /api/calls                 -> list calls
  GET  /api/calls/{id}            -> call detail (turns + audit)
  GET  /api/audit/{id}            -> audit ledger for a call
  GET  /api/audit/verify/{id}     -> verify hash chain
  GET  /api/stats                 -> learning + dialect stats
  WS   /ws/dashboard              -> global dashboard live feed
  WS   /ws/dashboard/{call_id}    -> per-call dashboard live feed
  WS   /ws/citizen/{call_id}      -> citizen UI live feed
"""
from __future__ import annotations
import os
import uuid
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException, Body, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from . import config, db
from .session import manager
from . import state_machine
from .services import asr, nlu, dialect, sentiment, verification, pii, audit, learning, tts, translit, notify
from .services import groq_client
from .services import conversation


app = FastAPI(title="Pratyaya — 1092 Helpline AI Co-pilot",
              version="1.0.0",
              description="Real-time voice-to-voice assistive layer for Karnataka 1092 helpline.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


async def _telegram_handover_ping(call_id: str, sess, reasons, transcript=""):
    """Best-effort Telegram notification for an officer-on-phone."""
    if not notify.is_enabled():
        return
    info = sess.last_interpretation or {}
    ent = info.get("entities") or {}
    sent = sess.last_sentiment or {}
    payload = {
        "language": sess.last_language,
        "dialect": sess.last_dialect,
        "issue_type": info.get("issue_type"),
        "urgency": info.get("urgency_level"),
        "location": ent.get("location"),
        "summary": info.get("issue_summary") or "",
        "transcript": transcript,
        "distress": sent.get("distress"),
        "reasons": reasons or [],
        "dashboard_url": f"{os.getenv('PUBLIC_BASE_URL','http://localhost:8000')}/agent",
    }
    await notify.notify_handover(call_id, payload)


def _normalize_for_compare(s: str) -> set:
    """Lower-cased word set for fuzzy similarity. Strips short tokens."""
    if not s:
        return set()
    s = s.lower()
    # Treat punctuation as space
    for c in "।,.!?;:\"'()[]{}":
        s = s.replace(c, " ")
    return {w for w in s.split() if len(w) >= 3}


def _is_repeated_topic(current: str, history: list, threshold: float = 0.5) -> bool:
    """Heuristic: True if the citizen's current utterance overlaps strongly
    with a recent prior utterance — they're saying the same thing again,
    which is a signal the AI's clarifications aren't helping."""
    if not current or not history:
        return False
    cur = _normalize_for_compare(current)
    if len(cur) < 3:
        return False
    for prior in history[-3:]:
        prev = _normalize_for_compare(prior)
        if len(prev) < 3:
            continue
        overlap = len(cur & prev)
        smaller = min(len(cur), len(prev))
        if smaller and overlap / smaller >= threshold:
            return True
    return False


def _fallback_response(lang: str) -> dict:
    """Localized 'I didn't catch that' response — used when ASR is empty
    or the paraphrase LLM call comes back blank. Guarantees the citizen
    always hears something, never silence."""
    fb = {
        "kn": ("ಕ್ಷಮಿಸಿ, ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ.",
               "ದಯವಿಟ್ಟು ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ.", "kn-IN"),
        "hi": ("क्षमा करें, स्पष्ट सुनाई नहीं दिया।",
               "क्या आप फिर से बताएँगे?", "hi-IN"),
        "en": ("Sorry, I didn't catch that clearly.",
               "Could you say that again, please?", "en-IN"),
    }
    p, q, code = fb.get((lang or "en").split("-")[0].lower(), fb["en"])
    return {"paraphrase": p, "confirm_question": q, "speak_lang_code": code}


async def _rerun_as_description(call_id: str, sess, raw_text: str, lang: str) -> dict:
    """The citizen replied to a verification question with a substantive
    redescription instead of yes/no — i.e. they corrected our interpretation.
    Re-run the full NLU/sentiment/state pipeline on the new text and emit a
    fresh paraphrase. This is what makes Example 2 (misunderstanding +
    correction) work: we LISTEN to the correction instead of looping in
    "please say yes or no".
    """
    redacted, n_red = pii.redact(raw_text)
    fast = nlu.fast_intent(redacted)

    # The previous paraphrase was wrong — give the LLM the corrected text
    # WITHOUT the prior summary biasing it.
    prior_summary = ""
    sess.paraphrase_attempts = max(1, sess.paraphrase_attempts)

    dial_out, nlu_out, sent_out = await asyncio.gather(
        dialect.classify_dialect(redacted, lang),
        nlu.extract(redacted, lang, sess.last_dialect,
                    prior_turns=sess.recent_citizen_turns,
                    prior_summary=prior_summary),
        sentiment.analyse(redacted, None),
    )
    if fast == "info_request":
        nlu_out["issue_type"] = "information_request"
        nlu_out["urgency_level"] = "low"

    asked_for_human = verification.wants_human(redacted)
    matched_kw = verification.matched_distress_keywords(redacted)
    distress_phrase = bool(matched_kw)
    repeated_topic = _is_repeated_topic(redacted, sess.recent_citizen_turns)

    decision = state_machine.decide(
        asr_conf=0.85,            # we trust this — it came from a real transcript
        intent_conf=float(nlu_out.get("intent_confidence", 0.0)),
        dialect_conf=float(dial_out.get("confidence", 0.5)),
        sentiment=sent_out,
        needs_clarification=bool(nlu_out.get("needs_clarification", False)),
        consecutive_no=sess.consecutive_no,
        citizen_asked_for_human=asked_for_human,
        issue_type=nlu_out.get("issue_type") or "",
        urgency_level=nlu_out.get("urgency_level") or "",
        distress_phrase=distress_phrase,
        repeated_topic=repeated_topic,
        matched_keywords=matched_kw,
    )

    paraphrase = {"paraphrase": "", "confirm_question": "", "speak_lang_code": "kn-IN"}
    bridge = {"bridge_line": "", "speak_lang_code": verification._lang_to_bcp47(lang)}
    if decision["state"] == "HANDOVER":
        bridge["bridge_line"] = verification.localized(verification.HANDOVER_BRIDGE, lang)
    else:
        sess.paraphrase_attempts += 1
        attempt = min(sess.paraphrase_attempts, 3)
        if redacted:
            paraphrase = await verification.paraphrase(
                nlu_out, lang, dial_out["dialect"],
                attempt=attempt, ask_location=False,
            )
        if not paraphrase.get("paraphrase"):
            paraphrase = _fallback_response(lang)

    interpretation = {
        "issue_type": nlu_out.get("issue_type"),
        "issue_summary": nlu_out.get("issue_summary"),
        "urgency_level": nlu_out.get("urgency_level"),
        "factual_claims": nlu_out.get("factual_claims", []),
        "entities": nlu_out.get("entities", {}),
        "translation_en": nlu_out.get("translation_en", ""),
        "intent_confidence": float(nlu_out.get("intent_confidence", 0.0)),
        "asr_confidence": 0.85,
        "dialect_confidence": float(dial_out.get("confidence", 0.5)),
        "overall_confidence": decision["overall_confidence"],
        "needs_clarification": bool(nlu_out.get("needs_clarification", False)),
    }

    sess.last_dialect = dial_out["dialect"]
    sess.last_language = lang
    sess.last_interpretation = interpretation
    sess.last_sentiment = sent_out
    sess.last_state = decision["state"]
    sess.recent_citizen_turns.append(redacted)
    if len(sess.recent_citizen_turns) > 6:
        sess.recent_citizen_turns = sess.recent_citizen_turns[-6:]

    transcript_roman = translit.to_roman(redacted, lang)
    paraphrase_roman = translit.to_roman(paraphrase["paraphrase"], lang)
    confirm_roman = translit.to_roman(paraphrase["confirm_question"], lang)
    turn_id = "turn_" + uuid.uuid4().hex[:10]
    timestamp = datetime.utcnow().isoformat() + "Z"
    turn = {
        "call_id": call_id, "turn_id": turn_id, "timestamp": timestamp,
        "transcript_native": redacted, "transcript_roman": transcript_roman,
        "transcript_english": interpretation["translation_en"],
        "detected_language": lang, "detected_dialect": dial_out["dialect"],
        "interpretation": interpretation, "sentiment": sent_out,
        "state": decision["state"],
        "paraphrase_text": paraphrase["paraphrase"] or bridge["bridge_line"],
        "paraphrase_roman": paraphrase_roman,
        "paraphrase_lang": (paraphrase["speak_lang_code"]
                            if decision["state"] != "HANDOVER" else bridge["speak_lang_code"]),
        "confirm_question": paraphrase["confirm_question"],
        "confirm_question_roman": confirm_roman,
        "bridge_line": bridge["bridge_line"],
        "decision_reasons": decision["reasons"] + ["redescription_after_no"],
        "decision_explain": decision.get("explain"),
        "fast_intent": fast,
        "pii_redacted_count": n_red,
    }

    db.insert_turn(call_id, turn)
    audit.append(call_id, "turn_committed_redescription", "system", {
        "turn_id": turn_id, "state": decision["state"],
        "issue_type": interpretation["issue_type"],
    }, turn_id=turn_id)
    await manager.broadcast_dashboard(call_id, {"type": "turn", **turn})

    if not sess.shadow_mode:
        if decision["state"] == "HANDOVER":
            await manager.send_citizen(call_id, {
                "type": "handover",
                "bridge_line": bridge["bridge_line"],
                "bridge_roman": translit.to_roman(bridge["bridge_line"], lang),
                "speak_lang_code": bridge["speak_lang_code"],
                "reasons": decision["reasons"],
            })
            asyncio.create_task(_telegram_handover_ping(
                call_id, sess, decision["reasons"], transcript=redacted))
        else:
            # IMPORTANT: a paraphrase WS message tells citizen.js to expect
            # a yes/no on the NEXT turn — that's what we want here.
            await manager.send_citizen(call_id, {
                "type": "paraphrase",
                "paraphrase": paraphrase["paraphrase"],
                "paraphrase_roman": paraphrase_roman,
                "confirm_question": paraphrase["confirm_question"],
                "confirm_question_roman": confirm_roman,
                "speak_lang_code": paraphrase["speak_lang_code"],
                "state": decision["state"],
            })

    return {
        "ok": True,
        "transcript": redacted,
        "transcript_roman": transcript_roman,
        "classification": "redescription",
        "new_state": decision["state"],
        "ai_response": paraphrase["paraphrase"] or bridge["bridge_line"],
        "speak_lang_code": (paraphrase["speak_lang_code"]
                            if decision["state"] != "HANDOVER" else bridge["speak_lang_code"]),
    }


@app.on_event("startup")
async def on_start():
    db.init_db()


# ---------- pages ----------
# A citizen visiting the public URL should land directly on the helpline
# call screen — they should never see operator/admin chrome. The marketing
# landing remains available at /about for stakeholders / demo walkthroughs.
@app.get("/")
async def root_page():
    return FileResponse(FRONTEND / "citizen.html")


@app.get("/citizen")
async def citizen_page():
    return FileResponse(FRONTEND / "citizen.html")


@app.get("/about")
async def about_page():
    return FileResponse(FRONTEND / "landing.html")


@app.get("/agent")
async def agent_page():
    return FileResponse(FRONTEND / "index.html")


@app.get("/analytics")
async def analytics_page():
    return FileResponse(FRONTEND / "analytics.html")


# ---------- API ----------
@app.post("/api/calls/start")
async def start_call(payload: dict = Body(default={})):
    call_id = "call_" + uuid.uuid4().hex[:10]
    started_at = datetime.utcnow().isoformat() + "Z"
    agent_id = payload.get("agent_id") or "agent-001"
    lang_pref = payload.get("citizen_lang_pref") or "auto"

    # Cross-call memory: a stable browser-generated ID lets us recognise a
    # repeat caller without holding any phone-number PII. The frontend
    # creates+stores it in localStorage; we never see anything personal.
    caller_id = (payload.get("caller_id") or "").strip() or None
    geo = payload.get("geo") or {}
    if isinstance(geo, dict):
        geo = {
            "lat": geo.get("lat"),
            "lng": geo.get("lng"),
            "accuracy": geo.get("accuracy"),
            "label": (geo.get("label") or "")[:120] or None,
        }
    else:
        geo = {}

    db.insert_call(call_id, started_at, agent_id, lang_pref,
                   caller_id=caller_id, geo=geo)
    await manager.create(call_id, agent_id, lang_pref)

    # Surface prior calls from the SAME caller (excluding this one) so the
    # agent sees "this person has called us before about X" the moment the
    # call lands.
    prior = []
    if caller_id:
        prior = [c for c in db.prior_calls_for_caller(caller_id, limit=10)
                 if c.get("call_id") != call_id]

    audit.append(call_id, "call_started", "system",
                 {"agent_id": agent_id, "lang_pref": lang_pref,
                  "caller_id": caller_id, "geo": geo,
                  "prior_calls_count": len(prior)})
    await manager.broadcast_dashboard(call_id, {
        "type": "call_started",
        "call_id": call_id,
        "started_at": started_at,
        "agent_id": agent_id,
        "lang_pref": lang_pref,
        "caller_id": caller_id,
        "geo": geo,
        "prior_calls": prior,
        "is_repeat_caller": len(prior) > 0,
    })
    return {
        "call_id": call_id,
        "started_at": started_at,
        "is_repeat_caller": len(prior) > 0,
        "prior_calls": prior,
    }


@app.post("/api/calls/{call_id}/turn")
async def post_turn(call_id: str,
                    audio: UploadFile = File(...),
                    language_hint: str = Form("auto"),
                    prosody: Optional[str] = Form(None)):
    sess = manager.get(call_id)
    if not sess:
        raise HTTPException(404, "call not active")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "empty audio")

    # If we already handed over, the AI must NOT run anymore — it would just
    # re-trigger the same handover bridge. Just transcribe the citizen's
    # speech so the operator can see it, and stop.
    if sess.last_state == "HANDOVER":
        asr_out = await asr.transcribe_audio(audio_bytes,
                                              audio.filename or "audio.webm",
                                              language_hint)
        text = (asr_out.get("text") or "").strip()
        roman = translit.to_roman(text, sess.last_language or "en")
        await manager.broadcast_dashboard(call_id, {
            "type": "post_handover_citizen_audio",
            "transcript_native": text,
            "transcript_roman": roman,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })
        return {"ok": True, "post_handover": True,
                "transcript_native": text, "transcript_roman": roman,
                "interpretation": {"asr_confidence": asr_out.get("confidence", 0.7)}}

    prosody_dict = None
    if prosody:
        try:
            prosody_dict = json.loads(prosody)
        except Exception:
            prosody_dict = None

    # ---- 1. ASR ----
    asr_out = await asr.transcribe_audio(audio_bytes, audio.filename or "audio.webm",
                                          language_hint)
    raw_text = (asr_out.get("text") or "").strip()
    asr_conf = float(asr_out.get("confidence", 0.0))
    detected_lang = asr_out.get("language") or language_hint or sess.last_language or "kn"
    if detected_lang in ("english", "kannada", "hindi"):
        detected_lang = {"english": "en", "kannada": "kn", "hindi": "hi"}[detected_lang]
    # Reject anything Whisper guessed that ISN'T one of our three target
    # languages — Gujarati / Marathi / Tamil hallucinations end up here on
    # short or noisy audio. Default to the session language, falling back
    # to Kannada (this is Karnataka 1092).
    if detected_lang not in ("kn", "hi", "en"):
        detected_lang = sess.last_language or "kn"

    # Honor the citizen's explicit language pick. If they selected Kannada in
    # the dropdown but said one English word ("yes"), Whisper will detect
    # English and the LLM will reply in English. That's the "first Kannada,
    # then English" bug. When lang_pref is explicit (not "auto"), it wins.
    if sess.lang_pref and sess.lang_pref != "auto":
        detected_lang = sess.lang_pref

    # Single-line ASR diagnostic so the FastAPI terminal shows exactly what
    # came out of Whisper for every turn. If you ever see "asr_text=''" with
    # a healthy audio_bytes count, the problem is on Whisper's side / the
    # audio quality, not the network.
    asr_err = asr_out.get("error")
    print(
        f"[asr] call={call_id} lang_hint={language_hint} "
        f"audio_bytes={len(audio_bytes)} content_type={audio.content_type} "
        f"detected_lang={detected_lang} conf={asr_conf:.2f} "
        f"text_len={len(raw_text)} text={raw_text[:80]!r}"
        + (f" ERROR={asr_err!r}" if asr_err else ""),
        flush=True,
    )

    # ---- Early exit: Whisper returned nothing. Ask the citizen to repeat —
    # do NOT escalate to handover just because the audio was unintelligible.
    if not raw_text:
        fb = _fallback_response(detected_lang)
        audit.append(call_id, "asr_empty", "system",
                     {"audio_size": len(audio_bytes),
                      "content_type": audio.content_type,
                      "asr_error": asr_err})
        if not sess.shadow_mode:
            await manager.send_citizen(call_id, {
                "type": "ai_response",
                "text": fb["paraphrase"] + " " + fb["confirm_question"],
                "text_roman": translit.to_roman(
                    fb["paraphrase"] + " " + fb["confirm_question"], detected_lang),
                "speak_lang_code": fb["speak_lang_code"],
                "state": "CLARIFY",
                "classification": "retry",      # citizen.js re-opens mic in description mode
                "raw_text": "",
            })
        return {"ok": True, "asr_empty": True, "state": "CLARIFY",
                "transcript_native": "", "transcript_roman": "",
                "audio_bytes": len(audio_bytes),
                "asr_error": asr_err,
                "interpretation": {"asr_confidence": 0.0,
                                   "intent_confidence": 0.0,
                                   "overall_confidence": 0.0}}

    # ---- 2. PII redaction at the edge ----
    redacted, n_red = pii.redact(raw_text)

    # ---- Lightweight intent fast-path. We DON'T short-circuit the pipeline
    # here — Whisper still ran and we want full audit/translit — but we
    # surface the classification on the dashboard and use it to gentle-bias
    # the state machine away from spurious escalation when the citizen is
    # clearly just asking a question.
    fast = nlu.fast_intent(redacted)

    # ---- 3 & 4. Dialect + NLU + Sentiment in parallel ----
    # Multi-turn understanding: feed the prior turns + last summary into NLU
    # so fragmented utterances are interpreted as one incident.
    prior_summary = (sess.last_interpretation or {}).get("issue_summary") or ""
    dialect_task = dialect.classify_dialect(redacted, detected_lang)
    nlu_task = nlu.extract(redacted, detected_lang, sess.last_dialect,
                            prior_turns=sess.recent_citizen_turns,
                            prior_summary=prior_summary)
    sent_task = sentiment.analyse(redacted, prosody_dict)
    dial_out, nlu_out, sent_out = await asyncio.gather(dialect_task, nlu_task, sent_task)

    # If the fast classifier was confident this is a pure info request, do
    # NOT promote it to severe — even if the LLM strays. This is a guardrail
    # against hallucinated escalations on harmless queries.
    if fast == "info_request":
        nlu_out["issue_type"] = "information_request"
        nlu_out["urgency_level"] = "low"

    # ---- 5. State machine ----
    asked_for_human = verification.wants_human(redacted)
    matched_kw = verification.matched_distress_keywords(redacted)
    distress_phrase = bool(matched_kw)
    repeated_topic = _is_repeated_topic(redacted, sess.recent_citizen_turns)

    decision = state_machine.decide(
        asr_conf=asr_conf,
        intent_conf=float(nlu_out.get("intent_confidence", 0.0)),
        dialect_conf=float(dial_out.get("confidence", 0.5)),
        sentiment=sent_out,
        needs_clarification=bool(nlu_out.get("needs_clarification", False)),
        consecutive_no=sess.consecutive_no,
        citizen_asked_for_human=asked_for_human,
        issue_type=nlu_out.get("issue_type") or "",
        urgency_level=nlu_out.get("urgency_level") or "",
        distress_phrase=distress_phrase,
        repeated_topic=repeated_topic,
        matched_keywords=matched_kw,
    )

    # ---- 6. Verification paraphrase — produce one unless we're handing over. ----
    paraphrase = {"paraphrase": "", "confirm_question": "", "speak_lang_code": "kn-IN"}
    bridge = {"bridge_line": "", "speak_lang_code": verification._lang_to_bcp47(detected_lang)}

    if decision["state"] == "HANDOVER":
        bridge["bridge_line"] = verification.localized(verification.HANDOVER_BRIDGE, detected_lang)
    else:
        ask_loc = (
            verification.needs_location({
                "issue_type": nlu_out.get("issue_type"),
                "entities": nlu_out.get("entities", {}),
            })
            and not sess.location_asked
        )
        sess.paraphrase_attempts += 1
        attempt = min(sess.paraphrase_attempts, 3)
        if redacted:
            paraphrase = await verification.paraphrase(
                nlu_out, detected_lang, dial_out["dialect"],
                attempt=attempt, ask_location=ask_loc,
            )
        if not paraphrase.get("paraphrase"):
            paraphrase = _fallback_response(detected_lang)
        if ask_loc:
            sess.location_asked = True

        # The greeting already established the call language; prepending an
        # extra "ಸರಿ, ನಾನು ಕನ್ನಡದಲ್ಲಿ…" line on top of the LLM paraphrase was
        # producing a bilingual utterance whenever the LLM occasionally
        # answered in English (Kannada ack + English paraphrase). Dropped.
        sess.language_acked = True

    # ---- assemble interpretation ----
    interpretation = {
        "issue_type": nlu_out.get("issue_type"),
        "issue_summary": nlu_out.get("issue_summary"),
        "urgency_level": nlu_out.get("urgency_level"),
        "factual_claims": nlu_out.get("factual_claims", []),
        "entities": nlu_out.get("entities", {}),
        "translation_en": nlu_out.get("translation_en", ""),
        "intent_confidence": float(nlu_out.get("intent_confidence", 0.0)),
        "asr_confidence": asr_conf,
        "dialect_confidence": float(dial_out.get("confidence", 0.5)),
        "overall_confidence": decision["overall_confidence"],
        "needs_clarification": bool(nlu_out.get("needs_clarification", False)),
        "clarification_question": nlu_out.get("clarification_question"),
    }

    turn_id = "turn_" + uuid.uuid4().hex[:10]
    timestamp = datetime.utcnow().isoformat() + "Z"
    transcript_roman = translit.to_roman(redacted, detected_lang)
    paraphrase_roman = translit.to_roman(paraphrase["paraphrase"], detected_lang)
    confirm_roman = translit.to_roman(paraphrase["confirm_question"], detected_lang)
    turn = {
        "call_id": call_id,
        "turn_id": turn_id,
        "timestamp": timestamp,
        "transcript_native": redacted,
        "transcript_roman": transcript_roman,
        "transcript_english": interpretation["translation_en"],
        "detected_language": detected_lang,
        "detected_dialect": dial_out["dialect"],
        "interpretation": interpretation,
        "sentiment": sent_out,
        "state": decision["state"],
        "paraphrase_text": paraphrase["paraphrase"] or bridge["bridge_line"],
        "paraphrase_roman": paraphrase_roman,
        "paraphrase_lang": paraphrase["speak_lang_code"] if decision["state"] != "HANDOVER" else bridge["speak_lang_code"],
        "confirm_question": paraphrase["confirm_question"],
        "confirm_question_roman": confirm_roman,
        "bridge_line": bridge["bridge_line"],
        "decision_reasons": decision["reasons"],
        "decision_explain": decision.get("explain"),
        "fast_intent": locals().get("fast"),
        "pii_redacted_count": n_red,
    }

    # update session memory
    sess.last_dialect = dial_out["dialect"]
    sess.last_language = detected_lang
    sess.last_interpretation = interpretation
    sess.last_sentiment = sent_out
    sess.last_state = decision["state"]
    sess.recent_citizen_turns.append(redacted)
    if len(sess.recent_citizen_turns) > 6:
        sess.recent_citizen_turns = sess.recent_citizen_turns[-6:]
    if interpretation.get("issue_summary"):
        sess.recent_summaries.append(interpretation["issue_summary"])
        if len(sess.recent_summaries) > 6:
            sess.recent_summaries = sess.recent_summaries[-6:]

    # persist
    db.insert_turn(call_id, turn)
    audit.append(call_id, "turn_committed", "system", {
        "turn_id": turn_id, "state": decision["state"],
        "overall_confidence": decision["overall_confidence"],
        "issue_type": interpretation["issue_type"],
        "pii_redacted": n_red,
    }, turn_id=turn_id)

    # broadcast to dashboards
    await manager.broadcast_dashboard(call_id, {"type": "turn", **turn})

    # Push the right message to the citizen.
    if not sess.shadow_mode:
        if decision["state"] == "HANDOVER":
            await manager.send_citizen(call_id, {
                "type": "handover",
                "bridge_line": bridge["bridge_line"],
                "bridge_roman": translit.to_roman(bridge["bridge_line"], detected_lang),
                "speak_lang_code": bridge["speak_lang_code"],
                "reasons": decision["reasons"],
            })
            asyncio.create_task(_telegram_handover_ping(
                call_id, sess, decision["reasons"], transcript=redacted))
        else:
            await manager.send_citizen(call_id, {
                "type": "paraphrase",
                "paraphrase": paraphrase["paraphrase"],
                "paraphrase_roman": paraphrase_roman,
                "confirm_question": paraphrase["confirm_question"],
                "confirm_question_roman": confirm_roman,
                "speak_lang_code": paraphrase["speak_lang_code"],
                "state": decision["state"],
            })

    return {"ok": True, "shadow_mode": sess.shadow_mode, **turn}


@app.post("/api/calls/{call_id}/operator_message")
async def operator_message(call_id: str, payload: dict = Body(...)):
    """Operator (human officer) types an English message in the agent dashboard.
    We translate it to the citizen's language, push it to the citizen UI as
    an `operator_message` WS event, and the citizen's browser plays the TTS.
    """
    sess = manager.get(call_id)
    if not sess:
        raise HTTPException(404, "call not active")
    text_en = (payload.get("text") or "").strip()
    if not text_en:
        raise HTTPException(400, "text required")
    target_lang = sess.last_language or "en"
    speak_lang = verification._lang_to_bcp47(target_lang)

    # Translate (skip if target is English).
    translated = text_en
    if target_lang != "en":
        sys_p = (
            "Translate the operator's message into the requested language for a "
            "distress helpline call. Be empathetic, brief, natural. STRICT JSON: "
            '{"translated":"<text in target language script>"}'
        )
        out = await groq_client.chat_json(
            sys_p, f"target_lang={target_lang}\nmessage:\n{text_en}",
            model=config.LLM_FAST_MODEL, temperature=0.1, max_tokens=400,
        )
        translated = (out or {}).get("translated") or text_en

    translated_roman = translit.to_roman(translated, target_lang)
    timestamp = datetime.utcnow().isoformat() + "Z"

    audit.append(call_id, "operator_message", payload.get("by", "operator"), {
        "text_en": text_en, "translated": translated, "lang": target_lang,
    })
    await manager.broadcast_dashboard(call_id, {
        "type": "operator_message",
        "text_en": text_en,
        "translated": translated,
        "translated_roman": translated_roman,
        "speak_lang_code": speak_lang,
        "timestamp": timestamp,
    })
    if not sess.shadow_mode:
        await manager.send_citizen(call_id, {
            "type": "operator_message",
            "text": translated,
            "text_roman": translated_roman,
            "text_en": text_en,
            "speak_lang_code": speak_lang,
        })
    return {
        "ok": True,
        "translated": translated,
        "translated_roman": translated_roman,
        "speak_lang_code": speak_lang,
    }


@app.post("/api/calls/{call_id}/voice_confirm")
async def voice_confirm(call_id: str,
                          audio: UploadFile = File(...),
                          language_hint: str = Form("auto")):
    """The citizen's voice response after the AI asked 'is this correct?'.

    We transcribe with Whisper, classify yes/no/partial/unclear, update state,
    and send back a SHORT spoken response (success / retry / unclear / handover).
    No NLU pipeline — this is a focused yes/no turn.
    """
    sess = manager.get(call_id)
    if not sess:
        raise HTTPException(404, "call not active")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "empty audio")

    # Guard: post-handover, just transcribe and ship to dashboard.
    if sess.last_state == "HANDOVER":
        asr_out = await asr.transcribe_audio(audio_bytes,
                                              audio.filename or "audio.webm",
                                              language_hint)
        text = (asr_out.get("text") or "").strip()
        roman = translit.to_roman(text, sess.last_language or "en")
        await manager.broadcast_dashboard(call_id, {
            "type": "post_handover_citizen_audio",
            "transcript_native": text,
            "transcript_roman": roman,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })
        return {"ok": True, "post_handover": True,
                "transcript": text, "transcript_roman": roman,
                "classification": "post_handover"}

    asr_out = await asr.transcribe_audio(audio_bytes, audio.filename or "audio.webm",
                                          language_hint)
    raw_text = (asr_out.get("text", "") or "").strip()
    detected_lang = asr_out.get("language") or language_hint or sess.last_language or "en"
    if detected_lang in ("english", "kannada", "hindi"):
        detected_lang = {"english": "en", "kannada": "kn", "hindi": "hi"}[detected_lang]
    if detected_lang not in ("kn", "hi", "en"):
        detected_lang = sess.last_language or "kn"

    # Lock language to the language established on the citizen's first
    # description turn. A short "haudu" / "yes" confirmation is too short
    # for Whisper to classify reliably — it routinely flips to English on
    # 1-second clips — so we must NOT let confirmation turns rewrite the
    # session language. All localized prompts ("Sorry, I didn't catch
    # that") MUST come back in the citizen's own language.
    response_lang = sess.last_language or detected_lang or "kn"

    # Empty audio during confirmation — re-ask haudu/illa, don't escalate.
    if not raw_text:
        msg = verification.localized(verification.UNCLEAR, response_lang)
        if not sess.shadow_mode:
            await manager.send_citizen(call_id, {
                "type": "ai_response",
                "text": msg,
                "text_roman": translit.to_roman(msg, response_lang),
                "speak_lang_code": verification._lang_to_bcp47(response_lang),
                "state": "CLARIFY",
                "classification": "unclear",   # keeps awaitingConfirmation=true
                "raw_text": "",
            })
        return {"ok": True, "transcript": "", "transcript_roman": "",
                "classification": "unclear", "new_state": sess.last_state,
                "ai_response": msg, "ai_response_roman": "",
                "speak_lang_code": verification._lang_to_bcp47(response_lang)}

    classification = verification.classify_response(raw_text)
    asked_human = verification.wants_human(raw_text)

    # If the citizen said something substantive *instead of* a clean yes/no
    # ("no, they're suspicious people, theft" / "asal me bachcha gum hai"),
    # we MUST treat it as a redescription and re-run the full /turn pipeline.
    # Otherwise we'd loop them in 'Please say yes or no' forever — which is
    # the bug Example 2 in the user's flow exposes. Heuristic: if the
    # transcript has more than ~3 substantive words and isn't a clean yes,
    # route to the description path with the original text.
    word_count = len([w for w in raw_text.split() if len(w) >= 2])
    is_substantive = word_count >= 3 and classification in ("no", "partial", "unclear")
    if is_substantive and not asked_human:
        return await _rerun_as_description(call_id, sess, raw_text, response_lang)

    timestamp = datetime.utcnow().isoformat() + "Z"
    audit.append(call_id, "voice_confirmation", "citizen", {
        "transcript": raw_text, "classification": classification,
        "asked_human": asked_human,
    })
    learning.record_confirmation(call_id, "", classification, raw_text)

    new_state = sess.last_state
    next_msg_text = ""
    next_msg_roman = ""
    # All AI-spoken responses use the LOCKED session language so we never
    # flip to English just because the citizen said a 1-second "haudu".
    next_speak_lang = verification._lang_to_bcp47(response_lang)

    if asked_human:
        new_state = "HANDOVER"
        next_msg_text = verification.localized(verification.HANDOVER_BRIDGE, response_lang)
    elif classification == "yes":
        # Verified — now decide whether to escalate (severe issue) or
        # provide AI guidance directly.
        sess.consecutive_no = 0
        guidance = await verification.generate_guidance(
            sess.last_interpretation or {},
            response_lang, sess.last_dialect or "")
        next_msg_text = guidance["message"] or verification.localized(verification.SUCCESS, response_lang)
        if guidance["needs_officer"]:
            new_state = "HANDOVER"
        else:
            new_state = "VERIFIED"
    elif classification == "no":
        sess.consecutive_no += 1
        if sess.consecutive_no >= state_machine.HANDOVER_NO_LIMIT:
            new_state = "HANDOVER"
            next_msg_text = verification.localized(verification.HANDOVER_BRIDGE, response_lang)
        else:
            new_state = "CLARIFY"
            next_msg_text = verification.localized(verification.RETRY_NO, response_lang)
    elif classification == "partial":
        new_state = "CLARIFY"
        next_msg_text = verification.localized(verification.RETRY_PARTIAL, response_lang)
    else:  # unclear
        new_state = "CLARIFY"
        next_msg_text = verification.localized(verification.UNCLEAR, response_lang)

    sess.last_state = new_state
    next_msg_roman = translit.to_roman(next_msg_text, response_lang)

    # broadcast: dashboard sees the confirmation event + state change
    await manager.broadcast_dashboard(call_id, {
        "type": "voice_confirmation",
        "raw_text": raw_text,
        "raw_text_roman": translit.to_roman(raw_text, response_lang),
        "classification": classification,
        "new_state": new_state,
        "ai_response": next_msg_text,
        "ai_response_roman": next_msg_roman,
        "timestamp": timestamp,
    })

    # citizen receives the spoken AI response
    if not sess.shadow_mode:
        if new_state == "HANDOVER":
            handover_reasons = [
                "citizen requested human" if asked_human else
                "two consecutive rejections" if classification == "no" else
                "auto-handover",
            ]
            await manager.send_citizen(call_id, {
                "type": "handover",
                "bridge_line": next_msg_text,
                "bridge_roman": next_msg_roman,
                "speak_lang_code": next_speak_lang,
                "reasons": handover_reasons,
            })
            asyncio.create_task(_telegram_handover_ping(
                call_id, sess, handover_reasons, transcript=raw_text))
        else:
            await manager.send_citizen(call_id, {
                "type": "ai_response",
                "text": next_msg_text,
                "text_roman": next_msg_roman,
                "speak_lang_code": next_speak_lang,
                "state": new_state,
                "classification": classification,
                "raw_text": raw_text,
                "raw_text_roman": translit.to_roman(raw_text, detected_lang),
            })

    return {
        "ok": True,
        "transcript": raw_text,
        "transcript_roman": translit.to_roman(raw_text, detected_lang),
        "classification": classification,
        "new_state": new_state,
        "ai_response": next_msg_text,
        "ai_response_roman": next_msg_roman,
        "speak_lang_code": next_speak_lang,
    }


# ----------------------------------------------------------------------
# /converse — unified conversational turn
# ----------------------------------------------------------------------
# This is the path the citizen UI now drives. One endpoint, one LLM call,
# full chat history. The LLM decides whether to ask, verify, guide, or
# hand over — so the system stops looping in 'please say yes or no'.
# ----------------------------------------------------------------------
@app.post("/api/calls/{call_id}/converse")
async def converse_endpoint(call_id: str,
                             audio: UploadFile = File(...),
                             language_hint: str = Form("auto"),
                             prosody: Optional[str] = Form(None)):
    sess = manager.get(call_id)
    if not sess:
        raise HTTPException(404, "call not active")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "empty audio")

    # DEBUG — every turn, log the state we entered with so we can see
    # whether the post-handover early-return is firing.
    print(f"[converse-entry] call={call_id} state_on_entry={sess.last_state!r} "
          f"asked_count={getattr(sess, 'asked_count', 0)} "
          f"chat_len={len(sess.chat)}", flush=True)

    # If we already handed over, just transcribe + ship to dashboard.
    if sess.last_state == "HANDOVER":
        asr_out = await asr.transcribe_audio(audio_bytes,
                                              audio.filename or "audio.webm",
                                              language_hint)
        text = (asr_out.get("text") or "").strip()
        roman = translit.to_roman(text, sess.last_language or "en")
        await manager.broadcast_dashboard(call_id, {
            "type": "post_handover_citizen_audio",
            "transcript_native": text,
            "transcript_roman": roman,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })
        return {"ok": True, "post_handover": True,
                "transcript_native": text, "transcript_roman": roman}

    # ---- 1. ASR ----
    asr_out = await asr.transcribe_audio(audio_bytes,
                                          audio.filename or "audio.webm",
                                          language_hint)
    raw_text = (asr_out.get("text") or "").strip()
    asr_conf = float(asr_out.get("confidence", 0.0))

    detected_lang = asr_out.get("language") or sess.last_language or "kn"
    if detected_lang in ("english", "kannada", "hindi"):
        detected_lang = {"english": "en", "kannada": "kn", "hindi": "hi"}[detected_lang]
    if detected_lang not in ("kn", "hi", "en"):
        detected_lang = sess.last_language or "kn"
    if sess.lang_pref and sess.lang_pref != "auto":
        detected_lang = sess.lang_pref

    # Lock language to first valid turn — short follow-ups must not flip it.
    if not sess.last_language or sess.last_language not in ("kn", "hi", "en"):
        sess.last_language = detected_lang
    response_lang = sess.last_language

    asr_err = asr_out.get("error")
    print(
        f"[converse] call={call_id} lang_hint={language_hint} "
        f"audio_bytes={len(audio_bytes)} detected_lang={detected_lang} "
        f"locked={response_lang} conf={asr_conf:.2f} "
        f"text_len={len(raw_text)} text={raw_text[:80]!r}"
        + (f" ERROR={asr_err!r}" if asr_err else ""),
        flush=True,
    )

    # ---- empty ASR — re-ask in citizen's language, don't hand over. ----
    if not raw_text:
        fb = _fallback_response(response_lang)
        msg = fb["paraphrase"] + " " + fb["confirm_question"]
        if not sess.shadow_mode:
            await manager.send_citizen(call_id, {
                "type": "ai_response",
                "text": msg,
                "text_roman": translit.to_roman(msg, response_lang),
                "speak_lang_code": fb["speak_lang_code"],
                "state": "CLARIFY",
                "classification": "retry",     # citizen.js → mic stays open in description mode
                "raw_text": "",
            })
        audit.append(call_id, "asr_empty", "system",
                     {"audio_size": len(audio_bytes), "asr_error": asr_err})
        return {"ok": True, "asr_empty": True, "state": "CLARIFY",
                "transcript_native": "", "transcript_roman": ""}

    # ---- 2. PII redaction ----
    redacted, n_red = pii.redact(raw_text)

    # ---- 3. parallel: dialect + sentiment ----
    prosody_dict = None
    if prosody:
        try:
            prosody_dict = json.loads(prosody)
        except Exception:
            prosody_dict = None

    dial_task = dialect.classify_dialect(redacted, response_lang)
    sent_task = sentiment.analyse(redacted, prosody_dict)
    dial_out, sent_out = await asyncio.gather(dial_task, sent_task)

    # ---- 4. conversational LLM ----
    convo = await conversation.converse(
        transcript=redacted,
        language=response_lang,
        dialect=dial_out["dialect"],
        chat=sess.chat,
        slots=sess.slots,
        sentiment=sent_out,
        last_ai_question=sess.last_ai_question,
        last_ai_action=sess.last_ai_action,
        asked_count=getattr(sess, "asked_count", 0),
    )

    # Distress fast-path — keyword spotter forces a handover when the LLM
    # is being too soft (ask / guide / close). We INTENTIONALLY do NOT
    # override action="verify": when the LLM is mid-verification, letting
    # the verify→confirm→handover flow run naturally (per few-shot Example B
    # in the system prompt) gives a much better citizen experience.
    matched_kw = verification.matched_distress_keywords(redacted)
    if matched_kw and convo["action"] in ("ask", "guide", "close"):
        convo["action"] = "handover"
        convo["needs_handover"] = True
        convo["handover_reason"] = (convo.get("handover_reason")
                                     or f"distress_keywords:{','.join(matched_kw[:3])}")

    # CRITICAL — whenever the action is handover, the spoken reply MUST be
    # a real bridge line ("I hear you, I'm connecting you to a human
    # officer"). The LLM sometimes returns action=handover but a verify-
    # style reply ("...is that right?"), which leaves the citizen confused
    # and the call locked in HANDOVER state with no further response. Force
    # the canned localized bridge so the spoken line always matches the
    # action — no surprises for the caller mid-distress.
    if convo["action"] == "handover":
        convo["reply"] = verification.localized(verification.HANDOVER_BRIDGE,
                                                 response_lang)
        convo["needs_handover"] = True

    # ---- 5. update session ----
    sess.chat.append({"role": "citizen", "text": redacted})
    sess.chat.append({"role": "ai", "text": convo["reply"]})
    if len(sess.chat) > 16:
        sess.chat = sess.chat[-16:]

    sess.slots.update(convo["slots"] or {})
    sess.last_ai_question = convo["reply"] if convo["action"] in ("ask", "verify") else ""
    sess.last_ai_action = convo["action"]
    sess.last_dialect = dial_out["dialect"]
    sess.last_sentiment = sent_out
    sess.recent_citizen_turns.append(redacted)
    if len(sess.recent_citizen_turns) > 6:
        sess.recent_citizen_turns = sess.recent_citizen_turns[-6:]

    # Map LLM action → state-machine state for the dashboard
    if convo["action"] == "handover":
        new_state = "HANDOVER"
    elif convo["action"] in ("guide", "close"):
        new_state = "VERIFIED"
    else:
        new_state = "CLARIFY"
    sess.last_state = new_state
    # DEBUG — final committed action / state for this turn.
    print(f"[converse-commit] call={call_id} action={convo['action']!r} "
          f"new_state={new_state!r} needs_handover={convo.get('needs_handover')!r} "
          f"matched_kw={matched_kw!r}", flush=True)

    if convo["action"] == "ask":
        sess.asked_count = getattr(sess, "asked_count", 0) + 1
    else:
        sess.asked_count = 0

    # ---- 6. interpretation snapshot for dashboard ----
    interpretation = {
        "issue_type": sess.slots.get("issue_type"),
        "issue_summary": sess.slots.get("issue_type") and sess.slots.get("location")
                         and f"{sess.slots.get('issue_type')} at {sess.slots.get('location')}"
                         or (sess.slots.get("issue_type") or ""),
        "urgency_level": sess.slots.get("urgency"),
        "factual_claims": [],
        "entities": {
            "location": sess.slots.get("location"),
            "persons": sess.slots.get("persons_involved"),
        },
        "translation_en": "",
        "intent_confidence": 0.85 if convo["action"] in ("verify", "guide", "handover") else 0.6,
        "asr_confidence": asr_conf,
        "dialect_confidence": float(dial_out.get("confidence", 0.5)),
        "overall_confidence": (
            0.9 if convo["verified"] else
            0.75 if convo["action"] in ("verify", "guide") else
            0.6
        ),
        "needs_clarification": convo["action"] == "ask",
    }

    sess.last_interpretation = interpretation

    # ---- 7. persist + audit ----
    turn_id = "turn_" + uuid.uuid4().hex[:10]
    timestamp = datetime.utcnow().isoformat() + "Z"
    transcript_roman = translit.to_roman(redacted, response_lang)
    reply_roman = translit.to_roman(convo["reply"], response_lang)
    turn = {
        "call_id": call_id, "turn_id": turn_id, "timestamp": timestamp,
        "transcript_native": redacted,
        "transcript_roman": transcript_roman,
        "transcript_english": "",
        "detected_language": response_lang,
        "detected_dialect": dial_out["dialect"],
        "interpretation": interpretation,
        "sentiment": sent_out,
        "state": new_state,
        "paraphrase_text": convo["reply"],
        "paraphrase_roman": reply_roman,
        "paraphrase_lang": convo["speak_lang_code"],
        "confirm_question": "",
        "confirm_question_roman": "",
        "bridge_line": convo["reply"] if convo["action"] == "handover" else "",
        "decision_reasons": [convo["action"]] +
                            ([convo["handover_reason"]] if convo.get("handover_reason") else []),
        "decision_explain": {"action": convo["action"], "verified": convo["verified"]},
        "fast_intent": (sess.slots.get("issue_type") or ""),
        "pii_redacted_count": n_red,
    }
    db.insert_turn(call_id, turn)
    audit.append(call_id, "converse_turn", "system", {
        "turn_id": turn_id, "action": convo["action"], "state": new_state,
        "issue_type": sess.slots.get("issue_type"),
        "verified": convo["verified"],
        "handover_reason": convo.get("handover_reason", ""),
    }, turn_id=turn_id)
    await manager.broadcast_dashboard(call_id, {"type": "turn", **turn})

    # ---- 8. push to citizen ----
    if not sess.shadow_mode:
        if convo["action"] == "handover":
            await manager.send_citizen(call_id, {
                "type": "handover",
                "bridge_line": convo["reply"],
                "bridge_roman": reply_roman,
                "speak_lang_code": convo["speak_lang_code"],
                "reasons": [convo.get("handover_reason") or "ai_decided"],
            })
            asyncio.create_task(_telegram_handover_ping(
                call_id, sess,
                [convo.get("handover_reason") or "ai_decided"],
                transcript=redacted,
            ))
        else:
            await manager.send_citizen(call_id, {
                "type": "ai_response",
                "text": convo["reply"],
                "text_roman": reply_roman,
                "speak_lang_code": convo["speak_lang_code"],
                "state": new_state,
                "classification": (
                    "close" if convo["action"] == "close" else
                    "verified" if convo["action"] == "guide" else
                    "ask" if convo["action"] == "ask" else
                    "verify"
                ),
                "raw_text": redacted,
                "raw_text_roman": transcript_roman,
            })

    return {
        "ok": True,
        "state": new_state,
        "action": convo["action"],
        "transcript_native": redacted,
        "transcript_roman": transcript_roman,
        "reply": convo["reply"],
        "reply_roman": reply_roman,
        "speak_lang_code": convo["speak_lang_code"],
        "slots": sess.slots,
        "verified": convo["verified"],
        "needs_handover": convo.get("needs_handover", False),
    }


@app.post("/api/calls/{call_id}/converse_text")
async def converse_text(call_id: str, payload: dict = Body(...)):
    """Text-only turn through the conversational pipeline. Used by the
    manual yes/no fallback buttons and the demo scenarios — same code
    path as /converse, just without ASR. Keeps every channel converging
    on the same conversational logic."""
    sess = manager.get(call_id)
    if not sess:
        raise HTTPException(404, "call not active")

    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text required")

    if sess.last_state == "HANDOVER":
        roman = translit.to_roman(text, sess.last_language or "en")
        await manager.broadcast_dashboard(call_id, {
            "type": "post_handover_citizen_audio",
            "transcript_native": text, "transcript_roman": roman,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "scripted": True,
        })
        return {"ok": True, "post_handover": True, "state": "HANDOVER",
                "transcript_native": text, "transcript_roman": roman}

    response_lang = sess.last_language or "kn"

    redacted, n_red = pii.redact(text)
    dial_out, sent_out = await asyncio.gather(
        dialect.classify_dialect(redacted, response_lang),
        sentiment.analyse(redacted, None),
    )

    convo = await conversation.converse(
        transcript=redacted,
        language=response_lang,
        dialect=dial_out["dialect"],
        chat=sess.chat,
        slots=sess.slots,
        sentiment=sent_out,
        last_ai_question=sess.last_ai_question,
        last_ai_action=sess.last_ai_action,
        asked_count=getattr(sess, "asked_count", 0),
    )

    matched_kw = verification.matched_distress_keywords(redacted)
    if matched_kw and convo["action"] != "handover":
        convo["action"] = "handover"
        convo["needs_handover"] = True
        convo["handover_reason"] = (convo.get("handover_reason")
                                     or f"distress_keywords:{','.join(matched_kw[:3])}")
    if convo.get("needs_handover") and not convo.get("reply"):
        convo["reply"] = verification.localized(verification.HANDOVER_BRIDGE, response_lang)

    sess.chat.append({"role": "citizen", "text": redacted})
    sess.chat.append({"role": "ai", "text": convo["reply"]})
    if len(sess.chat) > 16:
        sess.chat = sess.chat[-16:]
    sess.slots.update(convo["slots"] or {})
    sess.last_ai_question = convo["reply"] if convo["action"] in ("ask", "verify") else ""
    sess.last_ai_action = convo["action"]

    new_state = "HANDOVER" if convo["action"] == "handover" else \
                "VERIFIED" if convo["action"] in ("guide", "close") else "CLARIFY"
    sess.last_state = new_state
    if convo["action"] == "ask":
        sess.asked_count = getattr(sess, "asked_count", 0) + 1
    else:
        sess.asked_count = 0

    transcript_roman = translit.to_roman(redacted, response_lang)
    reply_roman = translit.to_roman(convo["reply"], response_lang)

    if not sess.shadow_mode:
        if convo["action"] == "handover":
            await manager.send_citizen(call_id, {
                "type": "handover",
                "bridge_line": convo["reply"],
                "bridge_roman": reply_roman,
                "speak_lang_code": convo["speak_lang_code"],
                "reasons": [convo.get("handover_reason") or "ai_decided"],
            })
        else:
            await manager.send_citizen(call_id, {
                "type": "ai_response",
                "text": convo["reply"],
                "text_roman": reply_roman,
                "speak_lang_code": convo["speak_lang_code"],
                "state": new_state,
                "classification": (
                    "close" if convo["action"] == "close" else
                    "verified" if convo["action"] == "guide" else
                    "ask" if convo["action"] == "ask" else
                    "verify"
                ),
                "raw_text": redacted,
                "raw_text_roman": transcript_roman,
            })

    return {
        "ok": True, "state": new_state, "action": convo["action"],
        "transcript_native": redacted, "transcript_roman": transcript_roman,
        "reply": convo["reply"], "reply_roman": reply_roman,
        "speak_lang_code": convo["speak_lang_code"],
    }


@app.post("/api/calls/{call_id}/scripted_turn")
async def scripted_turn(call_id: str, payload: dict = Body(...)):
    """Inject a typed transcript as if it were ASR output.
    Used by the demo bank when a mic isn't available. Goes through PII redaction,
    NLU, dialect, sentiment, state machine and verification — same path as audio,
    minus Whisper. ASR confidence is set to 0.95 to simulate a clean utterance.
    """
    sess = manager.get(call_id)
    if not sess:
        raise HTTPException(404, "call not active")

    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text required")

    # Once we've handed over, the AI must not re-process — just record the
    # transcript so the operator sees what the citizen is saying.
    if sess.last_state == "HANDOVER":
        roman = translit.to_roman(text, sess.last_language or "en")
        await manager.broadcast_dashboard(call_id, {
            "type": "post_handover_citizen_audio",
            "transcript_native": text,
            "transcript_roman": roman,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "scripted": True,
        })
        return {"ok": True, "post_handover": True, "state": "HANDOVER",
                "transcript_native": text, "transcript_roman": roman,
                "paraphrase_text": "", "bridge_line": "",
                "interpretation": {"asr_confidence": 0.95}}
    detected_lang = payload.get("language_hint", "auto")
    if detected_lang == "auto":
        # crude script-based language guess
        if any("ಀ" <= ch <= "೿" for ch in text):
            detected_lang = "kn"
        elif any("ऀ" <= ch <= "ॿ" for ch in text):
            detected_lang = "hi"
        else:
            detected_lang = "en"

    asr_conf = 0.95
    redacted, n_red = pii.redact(text)

    prior_summary = (sess.last_interpretation or {}).get("issue_summary") or ""
    dial_out, nlu_out, sent_out = await asyncio.gather(
        dialect.classify_dialect(redacted, detected_lang),
        nlu.extract(redacted, detected_lang, sess.last_dialect,
                    prior_turns=sess.recent_citizen_turns,
                    prior_summary=prior_summary),
        sentiment.analyse(redacted, None),
    )

    asked_for_human = verification.wants_human(redacted)
    matched_kw = verification.matched_distress_keywords(redacted)
    distress_phrase = bool(matched_kw)
    repeated_topic = _is_repeated_topic(redacted, sess.recent_citizen_turns)
    decision = state_machine.decide(
        asr_conf=asr_conf,
        intent_conf=float(nlu_out.get("intent_confidence", 0.0)),
        dialect_conf=float(dial_out.get("confidence", 0.5)),
        sentiment=sent_out,
        needs_clarification=bool(nlu_out.get("needs_clarification", False)),
        consecutive_no=sess.consecutive_no,
        citizen_asked_for_human=asked_for_human,
        issue_type=nlu_out.get("issue_type") or "",
        urgency_level=nlu_out.get("urgency_level") or "",
        distress_phrase=distress_phrase,
        repeated_topic=repeated_topic,
        matched_keywords=matched_kw,
    )

    paraphrase = {"paraphrase": "", "confirm_question": "", "speak_lang_code": "kn-IN"}
    bridge = {"bridge_line": "", "speak_lang_code": verification._lang_to_bcp47(detected_lang)}
    if decision["state"] == "HANDOVER":
        bridge["bridge_line"] = verification.localized(verification.HANDOVER_BRIDGE, detected_lang)
    else:
        ask_loc = (
            verification.needs_location({
                "issue_type": nlu_out.get("issue_type"),
                "entities": nlu_out.get("entities", {}),
            })
            and not sess.location_asked
        )
        sess.paraphrase_attempts += 1
        attempt = min(sess.paraphrase_attempts, 3)
        if redacted:
            paraphrase = await verification.paraphrase(
                nlu_out, detected_lang, dial_out["dialect"],
                attempt=attempt, ask_location=ask_loc,
            )
        if not paraphrase.get("paraphrase"):
            paraphrase = _fallback_response(detected_lang)
        if ask_loc:
            sess.location_asked = True

        # Drop the LANGUAGE_ACK prefix — see the /turn handler for rationale.
        sess.language_acked = True

    interpretation = {
        "issue_type": nlu_out.get("issue_type"),
        "issue_summary": nlu_out.get("issue_summary"),
        "urgency_level": nlu_out.get("urgency_level"),
        "factual_claims": nlu_out.get("factual_claims", []),
        "entities": nlu_out.get("entities", {}),
        "translation_en": nlu_out.get("translation_en", ""),
        "intent_confidence": float(nlu_out.get("intent_confidence", 0.0)),
        "asr_confidence": asr_conf,
        "dialect_confidence": float(dial_out.get("confidence", 0.5)),
        "overall_confidence": decision["overall_confidence"],
        "needs_clarification": bool(nlu_out.get("needs_clarification", False)),
        "clarification_question": nlu_out.get("clarification_question"),
    }

    turn_id = "turn_" + uuid.uuid4().hex[:10]
    timestamp = datetime.utcnow().isoformat() + "Z"
    transcript_roman = translit.to_roman(redacted, detected_lang)
    paraphrase_roman = translit.to_roman(paraphrase["paraphrase"], detected_lang)
    confirm_roman = translit.to_roman(paraphrase["confirm_question"], detected_lang)
    turn = {
        "call_id": call_id,
        "turn_id": turn_id,
        "timestamp": timestamp,
        "transcript_native": redacted,
        "transcript_roman": transcript_roman,
        "transcript_english": interpretation["translation_en"],
        "detected_language": detected_lang,
        "detected_dialect": dial_out["dialect"],
        "interpretation": interpretation,
        "sentiment": sent_out,
        "state": decision["state"],
        "paraphrase_text": paraphrase["paraphrase"] or bridge["bridge_line"],
        "paraphrase_roman": paraphrase_roman,
        "paraphrase_lang": paraphrase["speak_lang_code"] if decision["state"] != "HANDOVER" else bridge["speak_lang_code"],
        "confirm_question": paraphrase["confirm_question"],
        "confirm_question_roman": confirm_roman,
        "bridge_line": bridge["bridge_line"],
        "decision_reasons": decision["reasons"],
        "decision_explain": decision.get("explain"),
        "fast_intent": locals().get("fast"),
        "pii_redacted_count": n_red,
        "scripted": True,
    }

    sess.last_dialect = dial_out["dialect"]
    sess.last_language = detected_lang
    sess.last_interpretation = interpretation
    sess.last_sentiment = sent_out
    sess.last_state = decision["state"]
    sess.recent_citizen_turns.append(redacted)
    if len(sess.recent_citizen_turns) > 6:
        sess.recent_citizen_turns = sess.recent_citizen_turns[-6:]
    if interpretation.get("issue_summary"):
        sess.recent_summaries.append(interpretation["issue_summary"])
        if len(sess.recent_summaries) > 6:
            sess.recent_summaries = sess.recent_summaries[-6:]

    db.insert_turn(call_id, turn)
    audit.append(call_id, "turn_committed", "system", {
        "turn_id": turn_id, "state": decision["state"],
        "overall_confidence": decision["overall_confidence"],
        "issue_type": interpretation["issue_type"],
        "scripted": True,
    }, turn_id=turn_id)

    await manager.broadcast_dashboard(call_id, {"type": "turn", **turn})

    if not sess.shadow_mode:
        if decision["state"] == "HANDOVER":
            await manager.send_citizen(call_id, {
                "type": "handover",
                "bridge_line": bridge["bridge_line"],
                "bridge_roman": translit.to_roman(bridge["bridge_line"], detected_lang),
                "speak_lang_code": bridge["speak_lang_code"],
                "reasons": decision["reasons"],
            })
            asyncio.create_task(_telegram_handover_ping(
                call_id, sess, decision["reasons"], transcript=redacted))
        else:
            await manager.send_citizen(call_id, {
                "type": "paraphrase",
                "paraphrase": paraphrase["paraphrase"],
                "paraphrase_roman": paraphrase_roman,
                "confirm_question": paraphrase["confirm_question"],
                "confirm_question_roman": confirm_roman,
                "speak_lang_code": paraphrase["speak_lang_code"],
                "state": decision["state"],
            })

    return {"ok": True, "shadow_mode": sess.shadow_mode, **turn}


@app.post("/api/calls/{call_id}/confirm")
async def confirm(call_id: str, payload: dict = Body(...)):
    sess = manager.get(call_id)
    if not sess:
        raise HTTPException(404, "call not active")
    response = payload.get("response")
    raw_text = payload.get("raw_text", "")
    if response not in ("yes", "no", "partial", "unclear"):
        # try classify from raw text
        response = verification.classify_response(raw_text or "")
    learning.record_confirmation(call_id, payload.get("turn_id", ""), response, raw_text)
    audit.append(call_id, "citizen_confirmation", "citizen",
                 {"response": response, "raw": raw_text},
                 turn_id=payload.get("turn_id"))

    if response == "yes":
        sess.consecutive_no = 0
        # Generate intelligent post-verification guidance (or escalate if severe).
        guidance = await verification.generate_guidance(
            sess.last_interpretation or {},
            sess.last_language or "en", sess.last_dialect or "")
        if guidance["needs_officer"]:
            sess.last_state = "HANDOVER"
            asyncio.create_task(_telegram_handover_ping(
                call_id, sess, ["severe issue verified — auto-escalate"], transcript=""))
        else:
            sess.last_state = "VERIFIED"
        # Speak the guidance to the citizen
        if not sess.shadow_mode:
            await manager.send_citizen(call_id, {
                "type": "ai_response",
                "text": guidance["message"],
                "text_roman": translit.to_roman(guidance["message"], sess.last_language or "en"),
                "speak_lang_code": verification._lang_to_bcp47(sess.last_language or "en"),
                "state": sess.last_state,
                "classification": "yes",
                "raw_text": "yes",
            })
    elif response == "no":
        sess.consecutive_no += 1
        if sess.consecutive_no >= 2:
            sess.last_state = "HANDOVER"
            asyncio.create_task(_telegram_handover_ping(
                call_id, sess, ["two consecutive No's"], transcript=""))
        else:
            sess.last_state = "CLARIFY"
    elif response == "partial":
        sess.last_state = "CLARIFY"

    await manager.broadcast_dashboard(call_id, {
        "type": "confirmation",
        "response": response,
        "raw_text": raw_text,
        "new_state": sess.last_state,
        "turn_id": payload.get("turn_id"),
    })
    return {"ok": True, "response": response, "new_state": sess.last_state}


@app.post("/api/calls/{call_id}/correct")
async def correct(call_id: str, payload: dict = Body(...)):
    field = payload.get("field")
    old = payload.get("old_value")
    new = payload.get("new_value")
    by = payload.get("corrected_by", "agent")
    turn_id = payload.get("turn_id", "")
    if not field:
        raise HTTPException(400, "field required")
    mistake_type = learning.record_correction(call_id, turn_id, field, old, new, by)
    audit.append(call_id, "agent_correction", by,
                 {"field": field, "old": old, "new": new,
                  "mistake_type": mistake_type}, turn_id=turn_id)
    await manager.broadcast_dashboard(call_id, {
        "type": "correction",
        "field": field,
        "old_value": old,
        "new_value": new,
        "corrected_by": by,
        "turn_id": turn_id,
        "mistake_type": mistake_type,
    })
    return {"ok": True, "mistake_type": mistake_type}


@app.post("/api/calls/{call_id}/handover")
async def handover(call_id: str, payload: dict = Body(default={})):
    sess = manager.get(call_id)
    if not sess:
        raise HTTPException(404, "call not active")
    reason = payload.get("reason", "agent_initiated")
    note = payload.get("note", "")

    bridge = await verification.handover_bridge(
        sess.last_language,
        sess.last_dialect,
        sess.last_interpretation.get("issue_summary", ""),
    )
    sess.last_state = "HANDOVER"

    audit.append(call_id, "handover", payload.get("by", "agent"),
                 {"reason": reason, "note": note,
                  "context": sess.last_interpretation,
                  "bridge_line": bridge["bridge_line"]})

    await manager.broadcast_dashboard(call_id, {
        "type": "handover",
        "reason": reason,
        "note": note,
        "bridge_line": bridge["bridge_line"],
    })
    await manager.send_citizen(call_id, {
        "type": "handover",
        "bridge_line": bridge["bridge_line"],
        "speak_lang_code": bridge["speak_lang_code"],
        "reasons": [reason],
    })
    asyncio.create_task(_telegram_handover_ping(
        call_id, sess, [f"agent_initiated · {reason}"], transcript=note or ""))
    return {"ok": True, **bridge}


@app.post("/api/calls/{call_id}/shadow")
async def set_shadow(call_id: str, payload: dict = Body(...)):
    sess = manager.get(call_id)
    if not sess:
        raise HTTPException(404, "call not active")
    on = bool(payload.get("shadow", False))
    sess.shadow_mode = on
    audit.append(call_id, "shadow_mode_changed", payload.get("by", "agent"),
                 {"shadow": on})
    await manager.broadcast_dashboard(call_id, {"type": "shadow_mode", "shadow": on})
    return {"ok": True, "shadow": on}


@app.get("/api/calls/{call_id}/export")
async def export_call(call_id: str):
    """Bundle a call's full record (call + turns + audit) for download/training."""
    call = db.get_call(call_id)
    if not call:
        raise HTTPException(404, "call not found")
    for t in call.get("turns", []):
        try: t["interpretation"] = json.loads(t.get("interpretation_json") or "{}")
        except: t["interpretation"] = {}
        try: t["sentiment"] = json.loads(t.get("sentiment_json") or "{}")
        except: t["sentiment"] = {}
    call["audit"] = audit.list_for_call(call_id)
    return JSONResponse(
        content=call,
        headers={"Content-Disposition": f'attachment; filename="{call_id}.json"'},
    )


@app.get("/api/analytics")
async def analytics():
    """Civic-sensor aggregation: issue types, urgency, languages, dialects,
    locations, state distribution, average distress, and recent sentiment frames."""
    return db.analytics_snapshot(recent_n=30)


@app.get("/api/calls/{call_id}/citizen_summary")
async def citizen_summary(call_id: str):
    """Compact end-of-call card shown to the citizen on hangup. Pulls the
    last interpretation, urgency, and final state — same data the agent
    saw, just trimmed for a non-technical audience. Falls through to a
    safe placeholder if the call isn't in the database yet."""
    call = db.get_call(call_id)
    if not call:
        return {"issue_type": None, "urgency_level": None,
                "final_state": None, "summary": ""}
    last = (call.get("turns") or [])[-1] if call.get("turns") else {}
    try:
        ip = json.loads(last.get("interpretation_json") or "{}")
    except Exception:
        ip = {}
    return {
        "call_id": call_id,
        "issue_type": ip.get("issue_type"),
        "urgency_level": ip.get("urgency_level"),
        "final_state": call.get("final_state") or last.get("state") or "",
        "summary": ip.get("issue_summary") or call.get("summary") or "",
    }


@app.post("/api/calls/{call_id}/end")
async def end_call(call_id: str, payload: dict = Body(default={})):
    sess = manager.get(call_id)
    final_state = payload.get("final_state") or (sess.last_state if sess else "ENDED")
    summary = (sess.last_interpretation.get("issue_summary") if sess else "") or ""
    db.end_call(call_id, datetime.utcnow().isoformat() + "Z", final_state, summary)
    audit.append(call_id, "call_ended", "system",
                 {"final_state": final_state, "summary": summary})
    await manager.broadcast_dashboard(call_id, {
        "type": "call_ended", "final_state": final_state, "summary": summary,
    })
    await manager.close(call_id)
    return {"ok": True, "final_state": final_state}


@app.get("/api/calls")
async def list_calls(limit: int = 50):
    rows = db.list_calls(limit)
    # mark live
    live_ids = set(manager.sessions.keys())
    for r in rows:
        r["live"] = r["call_id"] in live_ids
    return {"calls": rows, "live_count": len(live_ids)}


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str):
    call = db.get_call(call_id)
    if not call:
        raise HTTPException(404, "call not found")
    # parse JSON columns for the UI
    for t in call.get("turns", []):
        try: t["interpretation"] = json.loads(t.get("interpretation_json") or "{}")
        except: t["interpretation"] = {}
        try: t["sentiment"] = json.loads(t.get("sentiment_json") or "{}")
        except: t["sentiment"] = {}
    call["audit"] = audit.list_for_call(call_id)
    call["live"] = call_id in manager.sessions
    return call


@app.get("/api/audit/verify")
async def verify_audit_all():
    return audit.verify_chain(None)


@app.get("/api/calls/{call_id}/audit")
async def get_audit(call_id: str):
    return {"call_id": call_id, "ledger": audit.list_for_call(call_id)}


@app.get("/api/calls/{call_id}/audit/verify")
async def verify_audit(call_id: str):
    return audit.verify_chain(call_id)


@app.post("/api/calls/{call_id}/summary")
async def call_summary(call_id: str, payload: dict = Body(default={})):
    """Translate the latest interpretation summary into Kannada / Hindi / English.
    Used by the agent dashboard's 'Call summary' panel."""
    call = db.get_call(call_id)
    if not call or not call.get("turns"):
        raise HTTPException(404, "no turns in call yet")
    last = call["turns"][-1]
    try:
        ip = json.loads(last.get("interpretation_json") or "{}")
    except Exception:
        ip = {}
    base = ip.get("issue_summary") or last.get("transcript_english") or last.get("transcript_native") or ""
    if not base:
        return {"kn": "", "hi": "", "en": ""}

    sys_p = (
        "Translate the input to (a) Kannada, (b) Hindi, (c) English. "
        "Keep it short and factual — one or two sentences each. "
        'Output STRICT JSON: {"kn":"...","hi":"...","en":"..."}.'
    )
    from .services import groq_client
    out = await groq_client.chat_json(sys_p, base, model=config.LLM_FAST_MODEL,
                                       temperature=0.0, max_tokens=400)
    if "_error" in out:
        return {"kn": "", "hi": "", "en": base}
    return {
        "kn": out.get("kn", ""),
        "hi": out.get("hi", ""),
        "en": out.get("en", base),
        "source_summary": base,
        "issue_type": ip.get("issue_type"),
        "urgency_level": ip.get("urgency_level"),
        "location": (ip.get("entities") or {}).get("location"),
    }


@app.get("/api/stats")
async def stats():
    return learning.stats() | {"live_calls": len(manager.sessions)}


@app.get("/api/health")
async def health():
    return {"ok": True, "groq_configured": bool(config.GROQ_API_KEY),
            "version": "1.0.0"}


@app.get("/api/tts")
async def text_to_speech(text: str = Query(..., min_length=1, max_length=2000),
                          lang: str = Query("en-IN"),
                          gender: str = Query("female"),
                          rate: str = Query("+0%")):
    """Stream MP3 audio of `text` in `lang` using Microsoft Edge's neural voices.
    Used by the citizen UI for paraphrase / confirmation / handover prompts."""
    try:
        audio, voice = await tts.synthesize(text, lang_code=lang,
                                              gender=gender, rate=rate)
    except Exception as e:
        raise HTTPException(500, f"tts_failed: {e}")
    if not audio:
        raise HTTPException(400, "empty audio")
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "X-Pratyaya-Voice": voice,
            "Cache-Control": "public, max-age=300",
        },
    )


# ---------- WebSockets ----------
@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket):
    await ws.accept()
    manager.global_dashboard_sockets.add(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive / ignored
    except WebSocketDisconnect:
        pass
    finally:
        manager.global_dashboard_sockets.discard(ws)


@app.websocket("/ws/dashboard/{call_id}")
async def ws_dashboard_call(ws: WebSocket, call_id: str):
    await ws.accept()
    sess = manager.get(call_id)
    if sess:
        sess.dashboard_sockets.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if sess:
            sess.dashboard_sockets.discard(ws)


@app.websocket("/ws/citizen/{call_id}")
async def ws_citizen(ws: WebSocket, call_id: str):
    await ws.accept()
    sess = manager.get(call_id)
    if not sess:
        await ws.send_json({"type": "error", "error": "call_not_found"})
        await ws.close()
        return
    sess.citizen_socket = ws
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if sess.citizen_socket is ws:
            sess.citizen_socket = None
