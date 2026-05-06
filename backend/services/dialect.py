"""Dialect classification.

In production: wav2vec2 fine-tuned on IndicVoices for Bangalore-standard /
Dharwad / Mangaluru / Hyderabad-Karnataka Kannada.

Prototype: a hybrid lexical heuristic + LLM classifier. Lexical hints make
the result fast and explainable; the LLM resolves ambiguous cases.
"""
from __future__ import annotations
from typing import Dict, Any
from . import groq_client
from .. import config

# Regional lexical anchors. These are illustrative markers commonly cited in
# Kannada dialectology — they are not exhaustive, but they are useful priors.
DIALECT_HINTS = {
    "Dharwad-Kannada": [
        "ಯಾಕ್ರಿ", "ಏನ್ರಿ", "ಬರ್ರಿ", "ರೀ", "ಗೊತ್ತಿಲ್ಲ ರೀ", "yakri", "yenri", "barri",
        "hogona", "naa", "hangaadre", "estu",
    ],
    "Mangaluru-Kannada": [
        "ಆಂ", "ಎಂಚ", "ದಾನೆ", "encha", "dane", "yaane", "marayre",
        "ulai", "ulla", "tulu",
    ],
    "Hyderabad-Karnataka-Kannada": [
        "ನಿಂಗ", "ಬರ್ತ್ಯಾ", "ಆಗ್ತೈತಿ", "ningu", "aagtaiti", "bartya",
        "naanu illi", "bantatte",
    ],
    "Bangalore-standard-Kannada": [
        "ಇದೆ", "ಇಲ್ಲ", "ಮಾಡಿ", "namaskara", "namma", "swalpa", "barthini",
        "guru", "macha",
    ],
}

LANG_TO_DEFAULT_DIALECT = {
    "kn": "Bangalore-standard-Kannada",
    "hi": "Hindi-standard",
    "en": "Indian-English",
}


def lexical_score(text: str) -> Dict[str, float]:
    if not text:
        return {}
    low = text.lower()
    scores = {}
    for dialect, markers in DIALECT_HINTS.items():
        s = 0
        for m in markers:
            if m.lower() in low:
                s += 1
        if s:
            scores[dialect] = s
    return scores


async def classify_dialect(text: str, language: str) -> Dict[str, Any]:
    """Return {dialect, confidence, method}."""
    if not text:
        return {
            "dialect": LANG_TO_DEFAULT_DIALECT.get(language, "Bangalore-standard-Kannada"),
            "confidence": 0.5,
            "method": "default",
        }

    if language not in ("kn", "kannada", None, ""):
        return {
            "dialect": LANG_TO_DEFAULT_DIALECT.get(language, "Indian-English"),
            "confidence": 0.85,
            "method": "language_default",
        }

    # Lexical pass
    lex = lexical_score(text)
    if lex:
        top_dialect = max(lex, key=lex.get)
        top = lex[top_dialect]
        total = sum(lex.values())
        if top >= 2 and top / total > 0.6:
            return {
                "dialect": top_dialect,
                "confidence": min(0.95, 0.6 + 0.1 * top),
                "method": "lexical",
                "lexical_scores": lex,
            }

    # Fallback: LLM classifier
    sys = (
        "You are a Kannada dialect classifier. Given a short transcript, "
        "classify it as exactly one of: "
        "Bangalore-standard-Kannada, Dharwad-Kannada, Mangaluru-Kannada, "
        "Hyderabad-Karnataka-Kannada. Respond ONLY with strict JSON "
        '{"dialect": "<one of the four>", "confidence": <0..1>, "reason": "<brief>"}'
    )
    out = await groq_client.chat_json(sys, text[:500], model=config.LLM_FAST_MODEL,
                                       temperature=0.0, max_tokens=120)
    if "_error" in out or "dialect" not in out:
        return {
            "dialect": LANG_TO_DEFAULT_DIALECT.get(language, "Bangalore-standard-Kannada"),
            "confidence": 0.5,
            "method": "fallback",
        }
    return {
        "dialect": out.get("dialect", "Bangalore-standard-Kannada"),
        "confidence": float(out.get("confidence", 0.6)),
        "method": "llm",
        "reason": out.get("reason", ""),
    }
