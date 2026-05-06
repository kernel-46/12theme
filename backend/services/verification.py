"""Verification loop.

Generates a paraphrase of the interpretation back to the citizen in their
own language/dialect, plus a single closed-form confirmation question.
Also classifies the citizen's verbal yes/no/partial response — including
dialect-specific affirmations like 'haudu' / 'haan' / 'illa' / 'nahin'.
"""
from __future__ import annotations
from typing import Dict, Any
from . import groq_client
from .. import config

# Quick affirm/deny lexicon (case-insensitive).
AFFIRM = {
    # Kannada
    "haudu", "houdu", "ಹೌದು", "haan", "ಹಾ", "sari", "sariye", "ಸರಿ", "ಸರಿಯೇ",
    # Hindi
    "ji", "ji haan", "haan ji", "हाँ", "जी", "जी हाँ", "sahi", "thik hai", "theek hai",
    # English / Kanglish
    "yes", "yeah", "yep", "correct", "right", "ok", "okay", "true", "exactly",
}
DENY = {
    # Kannada
    "illa", "illave", "ಇಲ್ಲ", "alla", "ಅಲ್ಲ",
    # Hindi
    "nahi", "nahin", "नहीं", "ghalat", "galat",
    # English
    "no", "nope", "wrong", "incorrect", "not", "false",
}
PARTIAL = {
    "swalpa", "ಸ್ವಲ್ಪ", "kuch", "थोड़ा", "thoda", "partially",
    "partial", "almost", "mostly",
}


def classify_response(text: str) -> str:
    if not text:
        return "unclear"
    low = text.strip().lower()
    # exact match first
    tokens = set(low.replace("?", "").replace(",", "").split())
    if any(p in low for p in PARTIAL):
        return "partial"
    if any(a in low or a in tokens for a in AFFIRM):
        # if both affirm and deny appear (e.g. 'haudu illa'), prefer last
        if any(d in low or d in tokens for d in DENY):
            # whichever appears later wins
            last_a = max((low.rfind(a) for a in AFFIRM if a in low), default=-1)
            last_d = max((low.rfind(d) for d in DENY if d in low), default=-1)
            return "yes" if last_a > last_d else "no"
        return "yes"
    if any(d in low or d in tokens for d in DENY):
        return "no"
    return "unclear"


_PARAPHRASE_SYS = """You are Pratyaya, a calm helpline AI co-pilot.
Given a structured interpretation of a citizen's complaint, write a SHORT
paraphrase BACK TO THE CITIZEN in their own language and dialect, followed by
a single closed-form confirmation question. Output STRICT JSON only:

{"paraphrase":"<one or two sentences in citizen's script>",
 "confirm_question":"<one closed-form yes/no question in citizen's script>",
 "speak_lang_code":"<BCP-47 like kn-IN / hi-IN / en-IN>"}

Rules:
- Use the citizen's DIALECT register where natural ('haudu' / 'haan' style).
- Restate ONLY what the citizen actually said — do not add new facts.
- Be brief. The citizen is in distress. <= 30 words total.
- The confirm_question must be answerable with yes/no/partial.

Smart re-ask — DO NOT use the same closed-form phrasing every time.
The 'attempt' field tells you which phrasing variant to use:
  attempt=1 → "Did I understand this correctly?"
  attempt=2 → "Should I send help immediately?" (only if the issue is severe
              enough that immediate help is appropriate; otherwise:
              "Are you reporting an emergency, or just informing us?")
  attempt=3 → A direct confirmation: "Shall I connect you to an officer now?"

Location handling:
- If the issue is location-sensitive (fight, missing person, medical, danger
  near caller) and location is null/empty, INCLUDE one short follow-up
  question asking WHERE they are right now, INSIDE the confirm_question
  field — e.g. "ನೀವು ಈಗ ಎಲ್ಲಿ ಇದ್ದೀರಿ?" / "आप अभी कहाँ हैं?" / "Where are you
  right now?". This prevents leaving the agent without a location.
"""


def _detect_script(text: str) -> str:
    """Return 'kn' / 'hi' / 'en' / '' based on dominant script in `text`."""
    if not text:
        return ""
    kn = sum(1 for ch in text if "ಀ" <= ch <= "೿")
    hi = sum(1 for ch in text if "ऀ" <= ch <= "ॿ")
    en = sum(1 for ch in text if ch.isalpha() and ord(ch) < 128)
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
    return ""


async def paraphrase(interpretation: Dict[str, Any], language: str, dialect: str,
                     attempt: int = 1, ask_location: bool = False) -> Dict[str, str]:
    target = (language or "en").lower().split("-")[0]
    target_name = {"kn": "Kannada (ಕನ್ನಡ script)",
                   "hi": "Hindi (देवनागरी script)",
                   "en": "English"}.get(target, "English")
    user = (
        f"REQUIRED OUTPUT LANGUAGE: {target_name}. ALL fields must be in "
        f"{target_name}. Do not mix English unless the target IS English.\n\n"
        f"language={language}\ndialect={dialect}\n"
        f"attempt={attempt}\n"
        f"ask_location={'yes' if ask_location else 'no'}\n"
        f"issue_type={interpretation.get('issue_type')}\n"
        f"summary={interpretation.get('issue_summary')}\n"
        f"location={interpretation.get('entities', {}).get('location')}\n"
        f"persons={interpretation.get('entities', {}).get('persons')}\n"
        f"urgency={interpretation.get('urgency_level')}\n"
        f"facts={interpretation.get('factual_claims')}\n"
    )
    out = await groq_client.chat_json(_PARAPHRASE_SYS, user, model=config.LLM_MODEL,
                                       temperature=0.2, max_tokens=220)
    if "_error" in out:
        return {
            "paraphrase": "",
            "confirm_question": "",
            "speak_lang_code": _lang_to_bcp47(language),
        }

    p = out.get("paraphrase", "") or ""
    q = out.get("confirm_question", "") or ""

    # Hard language-enforcement: if the LLM came back in the wrong script,
    # retry once with a louder instruction. This is the fix for the
    # "first Kannada then English" case where Llama silently switched
    # to English half-way through the paraphrase.
    if target in ("kn", "hi"):
        got = _detect_script(p + " " + q)
        if got and got != target:
            retry_user = (
                f"YOU JUST RESPONDED IN {got.upper()} SCRIPT. THIS IS WRONG. "
                f"REWRITE THE ENTIRE OUTPUT IN {target_name} ONLY. "
                f"Same JSON shape, same meaning, but every word in {target_name}.\n\n"
                + user
            )
            retry = await groq_client.chat_json(_PARAPHRASE_SYS, retry_user,
                                                 model=config.LLM_MODEL,
                                                 temperature=0.0, max_tokens=220)
            if "_error" not in retry:
                rp = retry.get("paraphrase", "") or ""
                rq = retry.get("confirm_question", "") or ""
                # Only replace if the retry is actually in the right script
                if _detect_script(rp + " " + rq) == target:
                    p, q = rp, rq

    return {
        "paraphrase": p,
        "confirm_question": q,
        "speak_lang_code": out.get("speak_lang_code", _lang_to_bcp47(language)),
    }


# Issue types where the system genuinely needs a location to act on.
LOCATION_SENSITIVE_ISSUES = frozenset({
    "domestic_violence", "child_safety", "missing_person",
    "sexual_harassment", "harassment", "stalking",
    "medical_emergency", "general_grievance",  # neighbour fight, etc.
})


def needs_location(interpretation: Dict[str, Any]) -> bool:
    """True iff the issue type benefits from a location AND we don't have one."""
    if not interpretation:
        return False
    issue = (interpretation.get("issue_type") or "").lower()
    loc = (interpretation.get("entities") or {}).get("location")
    return issue in LOCATION_SENSITIVE_ISSUES and not loc


_HANDOVER_SYS = """You are Pratyaya. Write a SHORT, warm bridging line to be
SPOKEN to the citizen as we hand the call to a human agent. Use the citizen's
language and dialect register. The line must reassure them, ask them to hold
briefly, and confirm that the agent already has their issue. STRICT JSON:

{"bridge_line":"<<=25 words in citizen's script>",
 "speak_lang_code":"<BCP-47>"}
"""


async def handover_bridge(language: str, dialect: str, issue_summary: str) -> Dict[str, str]:
    user = f"language={language}\ndialect={dialect}\nissue={issue_summary}\n"
    out = await groq_client.chat_json(_HANDOVER_SYS, user, model=config.LLM_FAST_MODEL,
                                       temperature=0.1, max_tokens=160)
    if "_error" in out:
        return {"bridge_line": "", "speak_lang_code": _lang_to_bcp47(language)}
    return {
        "bridge_line": out.get("bridge_line", ""),
        "speak_lang_code": out.get("speak_lang_code", _lang_to_bcp47(language)),
    }


def _lang_to_bcp47(lang: str) -> str:
    return {
        "kn": "kn-IN", "kannada": "kn-IN",
        "hi": "hi-IN", "hindi": "hi-IN",
        "en": "en-IN", "english": "en-IN",
    }.get((lang or "").lower(), "en-IN")


# Spoken once on the first paraphrase so the citizen instantly knows we
# understood their language correctly. Trust-builder, in their own script.
LANGUAGE_ACK = {
    "kn": "ಸರಿ, ನಾನು ಕನ್ನಡದಲ್ಲಿ ನಿಮ್ಮನ್ನು ಕೇಳುತ್ತಿದ್ದೇನೆ.",
    "hi": "ठीक है, मैं आपकी बात हिंदी में सुन रही हूँ।",
    "en": "Okay, I'm listening to you in English.",
}


def language_ack(lang: str) -> str:
    base = (lang or "en").lower().split("-")[0]
    return LANGUAGE_ACK.get(base, LANGUAGE_ACK["en"])


# Localized canned responses used in the voice confirmation flow.
# Keeps round-trip latency low — these don't need an LLM call.
SUCCESS = {
    "kn": "ಧನ್ಯವಾದಗಳು. ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ದಾಖಲಿಸಲಾಗಿದೆ. ನಮ್ಮ ತಂಡ ಶೀಘ್ರವೇ ನಿಮಗೆ ಸಂಪರ್ಕಿಸುತ್ತದೆ.",
    "hi": "धन्यवाद। आपकी समस्या दर्ज कर ली गई है। हमारी टीम जल्द ही आपसे संपर्क करेगी।",
    "en": "Thank you. Your issue has been recorded. Our team will reach out to you shortly.",
}

RETRY_NO = {
    "kn": "ಕ್ಷಮಿಸಿ. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಮತ್ತೊಮ್ಮೆ ಸ್ಪಷ್ಟವಾಗಿ ಹೇಳಬಲ್ಲಿರಾ?",
    "hi": "क्षमा करें। कृपया अपनी समस्या एक बार फिर स्पष्ट रूप से बताइए।",
    "en": "I'm sorry. Could you please describe your issue once more, clearly?",
}

RETRY_PARTIAL = {
    "kn": "ಯಾವ ಭಾಗ ಸರಿ ಮತ್ತು ಯಾವ ಭಾಗ ತಪ್ಪು — ದಯವಿಟ್ಟು ತಿಳಿಸಿ.",
    "hi": "कृपया बताइए — कौन सा हिस्सा सही है और कौन सा गलत?",
    "en": "Please tell me — which part is correct, and which part is wrong?",
}

UNCLEAR = {
    "kn": "ಕ್ಷಮಿಸಿ, ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು 'ಹೌದು' ಅಥವಾ 'ಇಲ್ಲ' ಎಂದು ಹೇಳಿ.",
    "hi": "क्षमा करें, स्पष्ट सुनाई नहीं दिया। कृपया 'हाँ' या 'नहीं' कहिए।",
    "en": "Sorry, I didn't catch that. Please say 'yes' or 'no'.",
}

HANDOVER_BRIDGE = {
    "kn": "ನಾನು ಕೇಳಿಸಿಕೊಂಡೆ. ನೀವು ಒಬ್ಬರೇ ಇಲ್ಲ — ದಯವಿಟ್ಟು ಫೋನ್ ಕಟ್ ಮಾಡಬೇಡಿ. ನಮ್ಮ ಮಹಿಳಾ ಅಧಿಕಾರಿಗೆ ಈಗಲೇ ಸಂಪರ್ಕಿಸುತ್ತಿದ್ದೇನೆ. ನಿಮ್ಮ ಸಮಸ್ಯೆ ಅವರಿಗೆ ಈಗಾಗಲೇ ತಿಳಿದಿದೆ.",
    "hi": "मैं समझ गई। आप अकेली नहीं हैं — कृपया फ़ोन मत काटिए। मैं अभी आपको हमारी महिला अधिकारी से जोड़ रही हूँ। आपकी समस्या उन्हें पहले से पता है।",
    "en": "I hear you. You're not alone — please stay on the line. I'm connecting you to our woman officer right now. They already know what's happening.",
}


def localized(table: dict, lang: str) -> str:
    base = (lang or "en").lower().split("-")[0]
    return table.get(base, table["en"])


# Words/phrases that mean "transfer me to a human".
HUMAN_KEYWORDS = {
    "kn": ["ಮನುಷ್ಯ", "ಅಧಿಕಾರಿ", "ಪೋಲೀಸ್ ಅಲ್ಲ", "real person", "human"],
    "hi": ["मानव", "इंसान", "अधिकारी", "agent", "human"],
    "en": ["human", "agent", "real person", "officer", "live person", "operator"],
}


def wants_human(text: str) -> bool:
    """True if the citizen explicitly asked for a human agent."""
    if not text:
        return False
    low = text.lower()
    for kws in HUMAN_KEYWORDS.values():
        for k in kws:
            if k.lower() in low:
                return True
    return False


# Distress / danger-keyword fast-path. A citizen who is actively crying for
# help should never be left waiting for a confirmation paraphrase. These
# tokens cover Kannada / Hindi / English plus Roman transliteration.
DANGER_KEYWORDS = (
    # Kannada
    "ಸಹಾಯ", "ಸಹಾಯ ಮಾಡಿ", "ಬಚಾವ್", "ಉಳಿಸಿ", "ಭಯ", "ಭಯವಾಗ್ತಿದೆ",
    "ಕೊಲ್ಲ", "ಕೊಲೆ", "ರಕ್ಷಿಸಿ", "ಕೈಯ್ಯಲ್ಲಿ ಚಾಕು",
    # Hindi
    "बचाओ", "बचाइए", "मदद", "मदद कीजिए", "मार डालेगा", "जान बचाओ",
    "धमकी", "डर लग रहा", "पकड़ लिया",
    # English / mixed
    "help me", "save me", "please help", "danger", "attack", "attacking",
    "kill", "killing", "stab", "stabbing", "bleeding", "drowning",
    "they will kill", "he will kill", "he is hitting", "he's beating",
    "she is being", "i am scared", "i'm scared", "im scared",
    "i'm in danger", "in danger", "won't let me leave", "wont let me leave",
    # Roman Indian
    "bachao", "madad", "madat", "bachaiye", "khatarnak", "khatra",
    "jaan bachao", "uljhane mein", "panic", "emergency",
)


def is_distress_phrase(text: str) -> bool:
    """True if the text contains a clear cry-for-help signal."""
    return bool(matched_distress_keywords(text))


def matched_distress_keywords(text: str) -> list:
    """Return the actual matched danger keywords, in order. Used by the
    explainability layer so the agent sees WHY the system escalated."""
    if not text:
        return []
    low = text.lower()
    out = []
    seen = set()
    for k in DANGER_KEYWORDS:
        kl = k.lower()
        if kl in low and kl not in seen:
            seen.add(kl)
            out.append(k)
            if len(out) >= 4:
                break
    return out


# Issue types the AI should NEVER attempt to resolve alone — these
# always escalate to a human officer once verified.
SEVERE_ISSUES = {
    "domestic_violence",
    "child_safety",
    "missing_person",
    "sexual_harassment",
    "stalking",
    "cyber_abuse",
    "medical_emergency",
}


_GUIDANCE_SYS = """You are Pratyaya — the AI assistant for Karnataka's 1092 women & child helpline.
Speak like a calm, kind, EXPERIENCED FEMALE HELPLINE OPERATOR — never bureaucratic,
never robotic. The citizen just confirmed your interpretation. Reply like a real
person who genuinely cares.

ALWAYS structure the reply as 3 short beats:
  (1) ACKNOWLEDGE their feeling / situation
  (2) ONE concrete IMMEDIATE action they can take RIGHT NOW
  (3) Tell them what happens next (officer callback OR follow-up logged)

For SEVERE issues (domestic_violence, child_safety, missing_person,
sexual_harassment, stalking, cyber_abuse, medical_emergency):
  - needs_officer = true
  - Tell them a SENIOR FEMALE OFFICER will call back within 5 minutes
  - Give them an IMMEDIATE SAFETY action: "go to a room you can lock",
    "stay near the phone", "don't confront them"
  - Reassure: they did the right thing by calling
  - Mention by name: 1098 (child), 181 (women) ONLY if directly relevant

For NON-SEVERE issues (information_request, general_grievance, witness reports,
police_inaction, workplace_harassment that is not active assault):
  - needs_officer = false
  - Give SPECIFIC actionable advice
  - Tell them which number to call if it escalates (100 police, 108 ambulance,
    1098 child line, 181 women) ONLY when relevant
  - Tell them the call is logged for follow-up

GOOD examples (match this tone, NOT just translate):

[Kannada · domestic_violence]
"ನೀವು ಕರೆ ಮಾಡಿದ್ದು ಸರಿ ಮಾಡಿದ್ರಿ, ನಿಮಗೆ ಧೈರ್ಯ ಇದೆ. ಈಗಲೇ ಒಂದು ಬೀಗ ಹಾಕಬಹುದಾದ ಕೋಣೆಗೆ ಹೋಗಿ ಮತ್ತು ಫೋನ್ ಹತ್ತಿರ ಇಟ್ಟುಕೊಳ್ಳಿ. ನಮ್ಮ ಮಹಿಳಾ ಅಧಿಕಾರಿ ಐದು ನಿಮಿಷದಲ್ಲಿ ನಿಮಗೆ ಕಾಲ್ ಮಾಡ್ತಾರೆ — ನೀವು ಲೈನ್‌ನಲ್ಲಿ ಇರಿ."

[Hindi · domestic_violence]
"आपने हिम्मत करके फ़ोन किया, बहुत अच्छा किया। अभी एक बंद होने वाले कमरे में जाइए और फ़ोन पास रखिए। हमारी महिला अधिकारी पाँच मिनट में आपको कॉल करेंगी — आप लाइन पर बनी रहिए।"

[English · domestic_violence]
"You did the right thing by calling. Right now, please move to a room you can lock and keep your phone close. Our senior woman officer will call you back within five minutes — please stay on the line."

[Kannada · neighbor fight (non-severe)]
"ಕೇಳಿ. ನೀವು ಮಧ್ಯೆ ಪ್ರವೇಶಿಸದೆ ಸುರಕ್ಷಿತವಾಗಿ ಇರಿ. ಗಲಾಟೆ ತೀವ್ರವಾದರೆ ಪೊಲೀಸ್ 100ಕ್ಕೆ ಫೋನ್ ಮಾಡಿ. ನಿಮ್ಮ ರಿಪೋರ್ಟ್ ನಮ್ಮಲ್ಲಿ ದಾಖಲಾಗಿದೆ, ಫಾಲೋ-ಅಪ್ ಮಾಡ್ತೇವೆ."

[English · information request]
"Got it. You can reach our women's helpline 181 anytime, and the police line is 100. Your call is logged here so we can follow up if anything changes."

BAD examples (do NOT do this):
- "Your problem is recorded" ← too short, no help, no warmth
- "An officer will contact you" ← no time frame, no immediate action
- "Please call 100" ← no empathy first
- "Thank you for contacting 1092" ← bureaucratic boilerplate
- Long-winded paragraphs ← too much for someone in distress

Hard rules:
- 2–3 short sentences MAX. Warm, conversational.
- Use the citizen's SCRIPT (Kannada → Kannada chars; Hindi → Devanagari).
- Match dialect register naturally (Bangalore default for Kannada).
- DO NOT invent specific officer names, addresses, or made-up phone numbers.
- DO NOT use "ji" / "saheb" inflections unless dialect calls for it.

STRICT JSON:
{"message": "<text in citizen's script>",
 "needs_officer": true|false,
 "rationale": "<one short internal reason>"}
"""


async def generate_guidance(interpretation: dict, language: str, dialect: str) -> dict:
    from . import groq_client
    from .. import config
    issue_type = (interpretation.get("issue_type") or "").lower()
    summary = interpretation.get("issue_summary") or ""
    urgency = interpretation.get("urgency_level") or "normal"
    location = (interpretation.get("entities") or {}).get("location") or ""

    # Local fast-path for clearly-severe cases — guarantees handover even if
    # the LLM is unsure.
    severe = issue_type in SEVERE_ISSUES

    user = (
        f"language={language}\ndialect={dialect}\n"
        f"issue_type={issue_type}\nurgency={urgency}\n"
        f"location={location}\nsummary={summary}\n"
    )
    out = await groq_client.chat_json(_GUIDANCE_SYS, user,
                                       model=config.LLM_MODEL,
                                       temperature=0.3, max_tokens=350)
    if "_error" in out:
        # Fallback: localized canned response
        if severe:
            return {
                "message": localized(HANDOVER_BRIDGE, language),
                "needs_officer": True,
                "rationale": "fallback_severe",
            }
        return {
            "message": localized(SUCCESS, language),
            "needs_officer": False,
            "rationale": "fallback_default",
        }
    return {
        "message": out.get("message", ""),
        "needs_officer": bool(out.get("needs_officer", severe)) or severe,
        "rationale": out.get("rationale", ""),
    }
