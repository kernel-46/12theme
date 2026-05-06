"""Intent + entity extraction (Sarvam-1 style).

Maps the redacted transcript into a structured Interpretation with
issue_type, summary, urgency, factual claims, entities, intent_confidence.
Also produces an English translation for the agent's view.

Also exports a lightweight ``fast_intent`` classifier — a rule-based gate
that handles obvious information-request utterances without calling the
LLM. Lets simple "what are your timings?" queries respond in under 200ms.
"""
from __future__ import annotations
import re as _re
from typing import Dict, Any
from . import groq_client
from .. import config


# Triggers that strongly indicate the citizen is asking for INFORMATION
# (helpline numbers, procedure questions, FAQ-type queries) rather than
# reporting an incident. Tuned for high precision — when it fires, the
# heavy pipeline can be skipped.
_INFO_PATTERNS = [
    # English
    r"\b(what|where|when|how|which|who)\b.*\?",
    r"\bwhat (is|are|time|number|helpline)\b",
    r"\bhow (do|can|to) (i|we) (file|register|complain|report)\b",
    r"\b(timing|timings|hours|address|number|helpline number|toll free)\b",
    r"\bcan i (just )?ask\b",
    # Hindi (Devanagari + roman)
    r"(कैसे|क्या|कब|कहाँ|कहां|कौन सा).{0,40}(करूँ|करूं|पता|बताइए|बताएं)",
    r"\b(time|samay|number|nambar|helpline)\b.*(kya|hai)\b",
    # Kannada (Kannada + roman)
    r"(ಯಾವಾಗ|ಎಲ್ಲಿ|ಹೇಗೆ|ಎಷ್ಟು|ಯಾವ).{0,40}(ತಿಳಿ|ಗೊತ್ತು|ತಿಳಿಸಿ|ಹೇಳಿ)",
    r"\b(samaya|gantegalu|number|helpline)\b.*(yenu|enu|hege)\b",
]

_NEG_KEYS = [
    # If any of these appear AS A WHOLE WORD, don't fast-path — there's a
    # likely incident. Whole-word matching keeps "helpline" out of "help".
    "help", "save", "scared", "scary", "hit", "beat", "drunk",
    "danger", "attack", "attacked", "stalking", "stalker",
    "harass", "harassment", "harassed", "follow", "following",
    "missing", "lost",
    "ಸಹಾಯ", "ಭಯ", "ಹೊಡೆ", "ಕುಡಿ",
    "मदद", "बचाओ", "मार", "पीट", "डर", "धमकी",
    "bachao", "madad", "khatra", "khatarnak",
]


def fast_intent(text: str) -> str:
    """Return one of:
       - "info_request"  → simple FAQ-style query, skip heavy NLU
       - "incident"      → looks like an actual report, run full pipeline
       - "unknown"       → caller is too short / ambiguous to classify

    Trades precision for low latency. We bias HEAVILY toward the safe
    side — any negative signal (distress / danger word) pushes into the
    incident bucket, even if the sentence looks like a question."""
    if not text:
        return "unknown"
    t = text.strip().lower()
    if len(t) < 3:
        return "unknown"
    for k in _NEG_KEYS:
        # Whole-word match — "help" matches "help me" but NOT "helpline".
        # For non-Latin scripts the regex \b boundary doesn't apply, so we
        # fall back to substring (Indic word boundaries are different).
        kl = k.lower()
        if kl.isascii():
            if _re.search(r"\b" + _re.escape(kl) + r"\b", t):
                return "incident"
        else:
            if kl in t:
                return "incident"
    for pat in _INFO_PATTERNS:
        if _re.search(pat, t, _re.IGNORECASE):
            return "info_request"
    # Heuristic: very short utterance with a question mark and no incident
    # words is most likely a casual info ask.
    if "?" in t and len(t) <= 60:
        return "info_request"
    return "unknown"

# Issue-type taxonomy used by 1092-class helplines.
ISSUE_TYPES = [
    "harassment",
    "domestic_violence",
    "child_safety",
    "stalking",
    "cyber_abuse",
    "sexual_harassment",
    "workplace_harassment",
    "missing_person",
    "medical_emergency",
    "police_inaction",
    "general_grievance",
    "information_request",
    "other",
]

_SYS = """You are Pratyaya — an India-native NLU layer for a women & child helpline (1092).
You read a redacted citizen transcript (Kannada / Hindi / English / code-mixed) and
output a structured interpretation as STRICT JSON only. No extra prose.

Schema:
{
  "issue_type": one of [%s],
  "issue_summary": short factual one-line summary in English (no embellishment),
  "urgency_level": one of ["low","normal","high","critical"],
  "factual_claims": [up to 5 short claims the citizen actually made, in English],
  "entities": {
    "location": string or null,
    "persons": [string],
    "organizations": [string],
    "time_references": [string],
    "objects": [string]
  },
  "translation_en": faithful English translation of the transcript,
  "intent_confidence": 0..1 float (your confidence in issue_type & summary),
  "needs_clarification": boolean,
  "clarification_question": string or null
}

Issue-type rules — be DECISIVE:
- domestic_violence  → caller's spouse / partner / in-laws beats / hits / abuses
                       caller. Triggers: "ganda hodeyutaane", "pati maarta hai",
                       "husband hits/beats me", "kicks me", "drunk and beats".
- child_safety       → caller's child or any minor is in danger / abused.
- missing_person     → someone (child, family member) is missing.
- sexual_harassment  → unwanted sexual touching, advances, comments aimed at caller.
- stalking           → following, repeated unwanted contact, threats.
- cyber_abuse        → online harassment, blackmail, leaked images, threats via phone/social.
- workplace_harassment → harassment at office / workplace.
- police_inaction    → caller already complained to police, no action.
- general_grievance  → witness reports (a fight in NEIGHBOR's house, an
                       unrelated dispute the caller is observing), complaints
                       about civic services. NOT the caller's own abuse.
- information_request → caller is asking for info, not reporting an incident.
- medical_emergency  → caller or someone with them needs medical help NOW.
- other              → anything that genuinely doesn't fit above.

Severity rule — set urgency_level:
- critical: imminent threat to life or limb (someone is being attacked NOW).
- high: ongoing pattern of violence, child in danger, missing minor.
- normal: complaint about a past incident, witness report, info request.
- low: pure information request.

Other rules:
- DO NOT invent facts. If the citizen did not say a location, location=null.
- needs_clarification=true if the issue type is genuinely unclear.
- Tokens like [REDACTED_PHONE] / [REDACTED_AADHAAR] must be kept verbatim.
""" % ", ".join(f'"{x}"' for x in ISSUE_TYPES)


# Deterministic safety net: certain canonical phrases ALWAYS map to a
# specific issue type, regardless of what the LLM says. Catches the
# common cases the LLM sometimes mis-classifies in mixed-script Kannada.
_HARD_OVERRIDES = [
    # (issue_type, urgency, regex pattern)
    ("domestic_violence", "high",
     r"(ಗಂಡ|ಪತಿ|husband|pati).{0,30}(ಹೊಡೆ|ಒದೆ|ಕುಡಿ|ಮಾರ|maar|hit|beat|kick|drunk)"),
    ("domestic_violence", "high",
     r"(ganda|pati|husband).{0,30}(hodey|ode|kudidu|maara|hit|beat|kick)"),
    ("domestic_violence", "high",
     r"(पति|शौहर).{0,30}(मार|पीट|शराब)"),
    ("missing_person", "high",
     r"(ಮಗಳು|ಮಗ|ಮಗು|बेटी|बेटा|बच्च|daughter|son|child).{0,30}(ಗಾಯಬ|ಮಿಸ್ಸಿಂಗ್|ग़ायब|गायब|missing|disappeared)"),
    ("sexual_harassment", "high",
     r"(harassment|छेड़|छूते|inappropriate|touch).{0,30}(ಸ್ಕೂಲ್|school|office|street)"),
]
import re as _re
def _hard_override(text: str):
    if not text:
        return None
    low = text.lower()
    for issue, urgency, pat in _HARD_OVERRIDES:
        if _re.search(pat, text, _re.I) or _re.search(pat, low, _re.I):
            return issue, urgency
    return None


async def extract(transcript: str, language: str, dialect: str,
                  prior_turns: list = None,
                  prior_summary: str = "") -> Dict[str, Any]:
    if not transcript:
        return {
            "issue_type": None,
            "issue_summary": "",
            "urgency_level": "normal",
            "factual_claims": [],
            "entities": {"location": None, "persons": [], "organizations": [],
                          "time_references": [], "objects": []},
            "translation_en": "",
            "intent_confidence": 0.0,
            "needs_clarification": True,
            "clarification_question": None,
        }

    # Multi-turn understanding: feed the last few citizen utterances and the
    # previous interpretation summary into the prompt so the LLM can combine
    # fragments like "someone is outside" → "he is knocking" → "I don't know
    # him" into a single threat-pattern interpretation instead of three
    # disconnected sentences.
    context_block = ""
    if prior_turns:
        joined = "\n".join(f"- {t}" for t in prior_turns[-3:] if t)
        if joined:
            context_block = f"prior_citizen_turns_in_this_call:\n{joined}\n"
    if prior_summary:
        context_block += f"prior_understanding: {prior_summary}\n"

    user = (
        f"language={language}\ndialect={dialect}\n"
        f"{context_block}"
        f"current_transcript:\n{transcript}\n"
        f"\nINSTRUCTION: combine the current transcript with the prior turns "
        f"if they describe the same incident — do NOT treat them as unrelated.\n"
    )
    out = await groq_client.chat_json(_SYS, user, model=config.LLM_MODEL,
                                       temperature=0.0, max_tokens=700)
    if "_error" in out:
        return {
            "issue_type": "other",
            "issue_summary": "",
            "urgency_level": "normal",
            "factual_claims": [],
            "entities": {"location": None, "persons": [], "organizations": [],
                          "time_references": [], "objects": []},
            "translation_en": "",
            "intent_confidence": 0.0,
            "needs_clarification": True,
            "clarification_question": None,
            "_error": out["_error"],
        }

    # Normalize / defensive fill
    out.setdefault("issue_type", "other")
    out.setdefault("issue_summary", "")
    out.setdefault("urgency_level", "normal")
    out.setdefault("factual_claims", [])
    out.setdefault("translation_en", "")
    out.setdefault("intent_confidence", 0.6)
    out.setdefault("needs_clarification", False)
    out.setdefault("clarification_question", None)
    ent = out.get("entities") or {}
    out["entities"] = {
        "location": ent.get("location"),
        "persons": ent.get("persons", []) or [],
        "organizations": ent.get("organizations", []) or [],
        "time_references": ent.get("time_references", []) or [],
        "objects": ent.get("objects", []) or [],
    }
    try:
        out["intent_confidence"] = float(out["intent_confidence"])
    except Exception:
        out["intent_confidence"] = 0.6

    # Apply deterministic override for canonical phrases the LLM mis-classifies.
    override = _hard_override(transcript)
    if override:
        forced_issue, forced_urgency = override
        if out.get("issue_type") != forced_issue:
            out["issue_type"] = forced_issue
            out["intent_confidence"] = max(out["intent_confidence"], 0.85)
        if forced_urgency in ("high", "critical") and out.get("urgency_level") not in ("high", "critical"):
            out["urgency_level"] = forced_urgency
    return out
