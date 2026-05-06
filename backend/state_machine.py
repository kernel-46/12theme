"""Three-state machine: VERIFIED · CLARIFY · HANDOVER.

Verification-first design with a SEVERITY FAST-PATH.

A first-description turn for a non-severe issue returns CLARIFY (system
paraphrases + asks "did I understand?"). VERIFIED is set only by an explicit
citizen Yes.

HANDOVER triggers (graceful — no surprise escalations during a clean call):
1. Citizen explicitly asks for a human ("ಮನುಷ್ಯ", "human", "agent", ...).
2. NLU classified the issue as one we never auto-resolve (harassment,
   stalking, missing person, child safety, domestic_violence, sexual
   harassment, cyber abuse, medical emergency) — straight to a senior
   officer, no back-and-forth confirmation.
3. Two consecutive citizen "no" rejections.
4. Overall interpretation confidence < 0.30 (we couldn't even understand).
5. Distress >= 0.85 AND fear >= 0.55 (acute distress — skip the AI loop).
"""
from typing import Dict, Any


def overall_confidence(asr_c: float, intent_c: float, dialect_c: float) -> float:
    v = max(0.001, asr_c) * max(0.001, intent_c) * max(0.001, dialect_c)
    return v ** (1 / 3)


HANDOVER_CONF_FLOOR = 0.30
HANDOVER_NO_LIMIT = 2
HANDOVER_DISTRESS = 0.85
HANDOVER_FEAR = 0.55

# Issue types the AI must NEVER attempt to handle alone. Kept here (in
# addition to the same set in services.verification) so the state machine
# can short-circuit on the very first turn instead of waiting for the
# citizen to say 'yes' to a paraphrase.
SEVERE_ISSUES = frozenset({
    "domestic_violence",
    "child_safety",
    "missing_person",
    "sexual_harassment",
    "harassment",
    "stalking",
    "cyber_abuse",
    "medical_emergency",
    "trafficking",
    "kidnapping",
    "rape",
    "assault",
})


def _explain(state: str, primary: str, *, confidence: float,
             distress: float, urgency: float, fear: float,
             issue_type: str = "", urgency_level: str = "",
             keywords: list = None) -> Dict[str, Any]:
    """Structured 'why' block — judges and agents both want a transparent
    breakdown of the signals behind every escalation decision. Each value
    can stand alone in the agent UI without further interpretation."""
    return {
        "decision": state,
        "primary_reason": primary,
        "signals": {
            "overall_confidence": round(confidence, 3),
            "distress": round(distress, 3),
            "urgency": round(urgency, 3),
            "fear": round(fear, 3),
            "issue_type": issue_type or None,
            "urgency_level": urgency_level or None,
            "keywords_matched": keywords or [],
        },
    }


def decide(asr_conf: float,
           intent_conf: float,
           dialect_conf: float,
           sentiment: Dict[str, float],
           needs_clarification: bool,
           consecutive_no: int = 0,
           citizen_asked_for_human: bool = False,
           issue_type: str = "",
           urgency_level: str = "",
           distress_phrase: bool = False,
           repeated_topic: bool = False,
           matched_keywords: list = None) -> Dict[str, Any]:
    overall = overall_confidence(asr_conf, intent_conf, dialect_conf)
    distress = sentiment.get("distress", 0.0)
    urgency = sentiment.get("urgency", 0.0)
    fear = sentiment.get("fear", 0.0)
    reasons = []
    issue_norm = (issue_type or "").strip().lower()
    urgency_norm = (urgency_level or "").strip().lower()
    matched_keywords = matched_keywords or []
    common = dict(confidence=overall, distress=distress, urgency=urgency,
                  fear=fear, issue_type=issue_norm, urgency_level=urgency_norm,
                  keywords=matched_keywords)

    def _ho(reason):
        return {
            "state": "HANDOVER",
            "overall_confidence": round(overall, 3),
            "reasons": [reason],
            "explain": _explain("HANDOVER", reason, **common),
        }

    # ---- HANDOVER (in priority order) ----
    if citizen_asked_for_human:
        return _ho("citizen explicitly requested a human agent")
    if distress_phrase:
        return _ho("citizen used distress / danger keyword (help/bachao/save me) — direct handover")
    if issue_norm in SEVERE_ISSUES:
        return _ho(f"severe issue type ({issue_norm}) — direct handover")
    if urgency_norm in {"critical", "emergency", "high"} and (
        distress >= 0.6 or fear >= 0.5
    ):
        return _ho("high-urgency complaint with elevated distress — direct handover")
    if repeated_topic:
        return _ho("citizen repeated the same concern across turns — escalating")
    if consecutive_no >= HANDOVER_NO_LIMIT:
        return _ho(f"citizen rejected interpretation {consecutive_no}× — escalating")
    if overall < HANDOVER_CONF_FLOOR:
        return _ho(f"overall confidence {overall:.2f} < {HANDOVER_CONF_FLOOR} — cannot interpret reliably")
    if distress >= HANDOVER_DISTRESS and fear >= HANDOVER_FEAR:
        return _ho(f"acute distress (distress={distress:.2f}, fear={fear:.2f})")

    # ---- Otherwise CLARIFY (always seek confirmation) ----
    if needs_clarification:
        reasons.append("nlu flagged ambiguity")
    reasons.append(f"awaiting citizen confirmation · conf={overall:.2f}")
    if distress >= 0.7:
        reasons.append(f"⚠ high distress={distress:.2f}")
    if urgency >= 0.7:
        reasons.append(f"⚠ high urgency={urgency:.2f}")

    return {
        "state": "CLARIFY",
        "overall_confidence": round(overall, 3),
        "reasons": reasons,
        "explain": _explain("CLARIFY", reasons[0], **common),
    }
