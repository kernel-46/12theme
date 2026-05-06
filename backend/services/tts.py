"""Natural multilingual TTS via Microsoft Edge's neural voices.

These voices are FREE (no API key), India-native, and far more natural than
the browser's built-in speechSynthesis — especially for Kannada and Hindi.

Voices we use:
    kn-IN-SapnaNeural    — female Kannada (warm, clear)
    kn-IN-GaganNeural    — male Kannada
    hi-IN-SwaraNeural    — female Hindi
    hi-IN-MadhurNeural   — male Hindi
    en-IN-NeerjaNeural   — female Indian English (recommended for the helpline)
    en-IN-PrabhatNeural  — male Indian English

The voice is chosen by inspecting the DOMINANT SCRIPT of the actual text,
with the requested ``lang_code`` only as a tiebreaker. This is critical: the
LLM-generated ``speak_lang_code`` and the script of the generated text often
disagree (e.g. Devanagari text tagged as ``kn-IN``), and Edge TTS will mangle
or silently drop output if the voice's language doesn't match the script.
"""
from __future__ import annotations
import re
import edge_tts
from typing import Dict, Tuple

# BCP-47 + raw lang code -> default voice
VOICE_MAP: Dict[str, str] = {
    "kn-IN": "kn-IN-SapnaNeural",
    "kn":    "kn-IN-SapnaNeural",
    "kannada": "kn-IN-SapnaNeural",

    "hi-IN": "hi-IN-SwaraNeural",
    "hi":    "hi-IN-SwaraNeural",
    "hindi": "hi-IN-SwaraNeural",

    "en-IN": "en-IN-NeerjaNeural",
    "en":    "en-IN-NeerjaNeural",
    "english": "en-IN-NeerjaNeural",
}

DEFAULT_VOICE = "en-IN-NeerjaNeural"

# Unicode script ranges we care about.
_KN_RE = re.compile(r"[ಀ-೿]")   # Kannada
_DV_RE = re.compile(r"[ऀ-ॿ]")   # Devanagari (Hindi)
_LATIN_RE = re.compile(r"[A-Za-z]")


def detect_script(text: str) -> str:
    """Return 'kn' / 'hi' / 'en' based on which script dominates the text.

    Counts glyph occurrences from each script and picks the largest. Ties
    and empty input fall through to English. We weight Indic scripts mildly
    above Latin so a single Roman-letter token (e.g. "1092") inside a
    Kannada sentence doesn't flip the voice.
    """
    if not text:
        return "en"
    kn = len(_KN_RE.findall(text))
    hi = len(_DV_RE.findall(text))
    en = len(_LATIN_RE.findall(text))
    # Any meaningful Indic content wins over incidental Latin tokens
    # (digits, "1092", brand names, etc.) that creep into Kannada/Hindi text.
    if kn >= 3 and kn >= hi:
        return "kn"
    if hi >= 3 and hi > kn:
        return "hi"
    if kn > hi and kn > 0:
        return "kn"
    if hi > 0:
        return "hi"
    if en > 0:
        return "en"
    return "en"


def resolve_voice(lang_code: str, gender: str = "female", text: str = "") -> str:
    """Pick a voice. Script of ``text`` is authoritative when it disagrees
    with ``lang_code`` — this prevents the wrong voice from mangling output."""
    script = detect_script(text) if text else ""
    requested = ""
    if lang_code:
        requested = (
            VOICE_MAP.get(lang_code.strip())
            or VOICE_MAP.get(lang_code.split("-")[0].lower())
            or ""
        )

    if script:
        script_voice = VOICE_MAP.get(script)
        # If the requested voice's language matches the dominant script, keep it.
        if requested and script_voice and requested.split("-", 1)[0] == script_voice.split("-", 1)[0]:
            base = requested
        else:
            base = script_voice or requested or DEFAULT_VOICE
    else:
        base = requested or DEFAULT_VOICE

    if gender == "male":
        return (
            base.replace("Sapna", "Gagan")
                .replace("Swara", "Madhur")
                .replace("Neerja", "Prabhat")
        )
    return base


async def synthesize(text: str, lang_code: str = "en-IN",
                     gender: str = "female",
                     rate: str = "+0%",
                     pitch: str = "+0Hz") -> Tuple[bytes, str]:
    """Synthesize text -> MP3 bytes. Returns (audio_bytes, voice_used)."""
    text = (text or "").strip()
    voice = resolve_voice(lang_code, gender, text)
    if not text:
        return b"", voice
    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk["data"])
        audio = b"".join(chunks)
    except Exception:
        audio = b""

    # If the chosen voice produced nothing (rare upstream hiccup, or a script
    # the voice can't read), retry once with the script-correct default voice.
    if not audio:
        script = detect_script(text)
        fallback = VOICE_MAP.get(script, DEFAULT_VOICE)
        if gender == "male":
            fallback = (
                fallback.replace("Sapna", "Gagan")
                        .replace("Swara", "Madhur")
                        .replace("Neerja", "Prabhat")
            )
        if fallback != voice:
            try:
                communicate = edge_tts.Communicate(text, fallback, rate=rate, pitch=pitch)
                chunks = []
                async for chunk in communicate.stream():
                    if chunk.get("type") == "audio":
                        chunks.append(chunk["data"])
                audio = b"".join(chunks)
                voice = fallback
            except Exception:
                pass

    return audio, voice
