"""ASR layer.

In production: AI4Bharat IndicConformer + BharatGen Shrutam2 in parallel,
disagreement -> CLARIFY/HANDOVER signal.

For the hackathon prototype we use Groq's Whisper Large v3 which handles
Kannada / Hindi / English / code-mixing very well. We compute a confidence
proxy from segment avg_logprob and no_speech_prob (Whisper-verbose fields).
"""
from __future__ import annotations
import math
import os
import time
import json
from pathlib import Path
from typing import Dict, Any
from . import groq_client

# Drop a copy of every uploaded audio blob here so we can replay it offline
# when the live transcript comes back empty. Disabled by default; flip
# PRATYAYA_DEBUG_AUDIO=1 in .env to enable.
_DEBUG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "asr_debug"
_DEBUG_AUDIO = os.getenv("PRATYAYA_DEBUG_AUDIO", "1") in ("1", "true", "True")


def _segment_confidence(seg: Dict[str, Any]) -> float:
    # Whisper verbose_json gives avg_logprob (~ -1.0..0) and no_speech_prob.
    avg_lp = seg.get("avg_logprob", -0.5)
    no_speech = seg.get("no_speech_prob", 0.0)
    # logprob -> probability (clamped)
    p = math.exp(max(-3.0, min(0.0, avg_lp)))
    return max(0.0, min(1.0, p * (1.0 - no_speech)))


def _aggregate_confidence(segments) -> float:
    if not segments:
        return 0.5
    confs = [_segment_confidence(s) for s in segments]
    # weighted by segment duration
    total_dur = sum(max(0.001, s.get("end", 0) - s.get("start", 0)) for s in segments)
    if total_dur <= 0:
        return sum(confs) / len(confs)
    weighted = sum(
        c * max(0.001, s.get("end", 0) - s.get("start", 0))
        for c, s in zip(confs, segments)
    )
    return weighted / total_dur


# Helpline-specific prompt — primes Whisper's vocabulary so Kannada / Hindi /
# English code-mixed distress calls are transcribed accurately.
#
# CRITICAL: Whisper's prompt limit is 224 TOKENS. The previous version of this
# prompt was ~800 tokens of mixed-script vocabulary, which Groq either
# truncated badly or fed back as a "previous transcript" so Whisper kept
# returning empty for fresh audio. Keep this short, English-language, and
# under ~60 tokens — that's enough to bias domain without breaking ASR.
HELPLINE_PROMPT = (
    "Karnataka 1092 women and child helpline call in Kannada, Hindi, or English."
)


_TARGET_LANGS = {"kn", "hi", "en"}
_LANG_NAME_TO_CODE = {
    "kannada": "kn", "kan": "kn", "kn": "kn",
    "hindi": "hi", "hin": "hi", "hi": "hi",
    "english": "en", "eng": "en", "en": "en",
}


def _detect_script(text: str) -> str:
    """Dominant script of `text` → 'kn' / 'hi' / 'en' / ''.
    More reliable than Whisper's auto-detect for short utterances."""
    if not text:
        return ""
    kn = sum(1 for ch in text if "ಀ" <= ch <= "೿")
    hi = sum(1 for ch in text if "ऀ" <= ch <= "ॿ")
    en = sum(1 for ch in text if ch.isalpha() and ord(ch) < 128)
    if kn >= 2 and kn >= hi:
        return "kn"
    if hi >= 2 and hi > kn:
        return "hi"
    if kn > 0 and kn >= hi:
        return "kn"
    if hi > 0:
        return "hi"
    if en > 0:
        return "en"
    return ""


def _normalize_lang(raw) -> str:
    """Map Whisper's language field (could be 'kn', 'kannada', 'Kannada')
    onto our 2-letter code, returning '' for anything outside kn/hi/en."""
    if not raw:
        return ""
    return _LANG_NAME_TO_CODE.get(str(raw).strip().lower(), "")


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.webm",
                           language_hint: str = "auto") -> Dict[str, Any]:
    """Transcribe + return native text, detected language, confidence.

    If Whisper comes back empty *with* the priming prompt, retry once
    without it — a long/wrong prompt occasionally suppresses the entire
    transcription and the no-prompt pass usually recovers. If Whisper
    auto-detects a language outside our target set (kn / hi / en) — for
    example Gujarati or Marathi — retry with explicit Kannada, since
    this is Karnataka's 1092 helpline.
    """
    # Optionally dump the audio so we can replay it via scripts/probe_asr.py
    # when the live response is empty.
    saved_audio_path = ""
    if _DEBUG_AUDIO:
        try:
            _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            ext = (filename or "audio.webm").rsplit(".", 1)[-1]
            saved_audio_path = str(_DEBUG_DIR / f"{ts}_{language_hint or 'auto'}.{ext}")
            with open(saved_audio_path, "wb") as f:
                f.write(audio_bytes)
        except Exception as e:
            print(f"[asr-debug] could not save audio: {e}", flush=True)

    # Treat "auto" as Kannada by default — this is the Karnataka 1092
    # helpline. Auto-detect is wrong far more often than it is right on
    # short, dialect-rich, telephony audio: it routinely picks Gujarati,
    # Marathi, or Tamil from a clean Kannada utterance. Pinning the first
    # attempt to "kn" eliminates that whole class of failure.
    first_hint = language_hint
    if first_hint in (None, "", "auto"):
        first_hint = "kn"

    primary = await groq_client.transcribe(audio_bytes, filename, first_hint,
                                            prompt=HELPLINE_PROMPT)

    text = (primary.get("text") or "").strip() if not primary.get("error") else ""
    err = primary.get("error")

    # Loud diagnostic: if Whisper returned empty WITHOUT a hard error, dump
    # the *entire* response so we can see what it actually said.
    if not text and not err:
        try:
            preview = json.dumps(primary, ensure_ascii=False)[:600]
        except Exception:
            preview = repr(primary)[:600]
        print(f"[asr-debug] EMPTY text from Groq (no error). saved={saved_audio_path or 'off'} "
              f"raw={preview}", flush=True)

    # Empty-text recovery: try again without the prompt.
    if (not text) and (not err):
        retry = await groq_client.transcribe(audio_bytes, filename, first_hint, prompt=None)
        rtext = (retry.get("text") or "").strip()
        if rtext and not retry.get("error"):
            primary = retry
            text = rtext

    # Cross-language fallback: if our chosen first_hint produced nothing,
    # walk through the other targets so a Hindi-speaking caller still
    # works on the Kannada-default.
    if not text:
        rest = [lh for lh in ("kn", "hi", "en") if lh != first_hint]
        for lh in rest:
            retry = await groq_client.transcribe(audio_bytes, filename, lh, prompt=None)
            rtext = (retry.get("text") or "").strip()
            if rtext and not retry.get("error"):
                primary = retry
                text = rtext
                break

    # If we DID get text, but the script of the text is none of kn/hi/en
    # (e.g. Gujarati hallucination), reject it and retry with explicit Kannada.
    if text:
        script = _detect_script(text)
        if not script:
            # Truly unscriptable garbage — try kn explicitly.
            retry = await groq_client.transcribe(audio_bytes, filename, "kn", prompt=None)
            rtext = (retry.get("text") or "").strip()
            if rtext and _detect_script(rtext) in _TARGET_LANGS:
                primary = retry
                text = rtext

    if primary.get("error") and not text:
        return {
            "text": "",
            "language": first_hint or "kn",
            "confidence": 0.0,
            "segments": [],
            "error": primary["error"],
        }

    raw_lang = primary.get("language", "")
    # Normalise Whisper's verbose names ("Kannada") to ISO codes, and reject
    # anything outside our target set so downstream code doesn't store a
    # nonsense language tag.
    norm = _normalize_lang(raw_lang)
    if not norm:
        # Use the script of the actual text as a last resort, then fall back
        # to whatever first_hint we used.
        norm = _detect_script(text) or first_hint or "kn"

    segments = primary.get("segments", []) or []
    confidence = _aggregate_confidence(segments) if segments else (0.7 if text else 0.0)

    return {
        "text": text,
        "language": norm,
        "confidence": float(confidence),
        "segments": segments,
    }


async def translate_to_english(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    """Whisper translate endpoint -> English text. Optional second pass."""
    # For the prototype we just rely on the LLM for translation in nlu.py.
    return ""
