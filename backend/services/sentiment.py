"""Six-dimensional sentiment fusion.

In production: prosodic features (pitch contour, speaking rate, jitter,
breath patterns) are extracted from the audio and fused with lexical
sentiment from the LLM.

Prototype: we use the LLM on the transcript and accept an optional
prosody dict from the client (browser can post raw amplitude / rate
estimates) to bias the result. The dimensions are:
distress, urgency, anger, fear, confusion, calm.
"""
from __future__ import annotations
from typing import Dict, Any, Optional
from . import groq_client
from .. import config

DIMENSIONS = ("distress", "urgency", "anger", "fear", "confusion", "calm")

_SYS = """You are a multilingual emotion analyst for distress helplines.
Read the citizen utterance (Kannada/Hindi/English/code-mixed) and rate
SIX dimensions on 0..1 floats. Output STRICT JSON only:

{"distress":x,"urgency":x,"anger":x,"fear":x,"confusion":x,"calm":x}

Calibration:
- A panicked distress call: distress 0.8+, urgency 0.8+, fear 0.6+, calm 0.05.
- An angry but coherent grievance: anger 0.7+, urgency 0.4-0.6, calm 0.2.
- A confused first-time caller: confusion 0.6+, calm 0.4.
- A neutral information request: calm 0.8+, all others < 0.2.
The six values do NOT need to sum to 1.
"""


def _clamp(x):
    try:
        return max(0.0, min(1.0, float(x)))
    except Exception:
        return 0.0


def _fuse_prosody(lex: Dict[str, float], prosody: Optional[Dict[str, float]]) -> Dict[str, float]:
    if not prosody:
        return lex
    # Simple fusion: prosody bumps distress / urgency / fear, suppresses calm.
    rate = prosody.get("speaking_rate_norm", 0.0)        # 0..1, 1 = very fast
    pitch_var = prosody.get("pitch_variance_norm", 0.0)  # 0..1
    loudness = prosody.get("loudness_norm", 0.0)         # 0..1
    arousal = max(rate, pitch_var, loudness)
    if arousal > 0.6:
        lex["distress"] = _clamp(lex.get("distress", 0) * 0.7 + arousal * 0.5)
        lex["urgency"] = _clamp(lex.get("urgency", 0) * 0.7 + arousal * 0.5)
        lex["calm"] = _clamp(lex.get("calm", 0) * (1 - arousal))
    return lex


async def analyse(transcript: str, prosody: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    if not transcript:
        return {"distress": 0.0, "urgency": 0.0, "anger": 0.0,
                "fear": 0.0, "confusion": 0.0, "calm": 1.0}

    out = await groq_client.chat_json(_SYS, transcript[:1200],
                                       model=config.LLM_FAST_MODEL,
                                       temperature=0.0, max_tokens=120)
    if "_error" in out:
        return {"distress": 0.0, "urgency": 0.0, "anger": 0.0,
                "fear": 0.0, "confusion": 0.0, "calm": 0.5}

    lex = {d: _clamp(out.get(d, 0.0)) for d in DIMENSIONS}
    if "calm" not in out:
        lex["calm"] = _clamp(1.0 - max(lex["distress"], lex["urgency"],
                                        lex["anger"], lex["fear"], lex["confusion"]))
    return _fuse_prosody(lex, prosody)
