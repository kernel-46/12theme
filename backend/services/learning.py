"""Continuous-learning capture.

Confirmed verifications become positive labelled pairs.
Agent / citizen corrections become higher-value error signals.
This module is the gate where those signals get persisted; a downstream
training pipeline (out of hackathon scope) would consume the table.

Corrections also get a structured ``mistake_type`` tag (e.g.
``urgency_underestimation``) so we capture not just "field X changed" but
HOW the model was wrong. That distinction matters at training time —
under-estimating urgency on a domestic-violence call is a very different
error from confusing two adjacent issue types.
"""
from datetime import datetime
from .. import db


_URGENCY_RANK = {
    "low": 0, "normal": 1, "medium": 1, "high": 2, "critical": 3, "emergency": 3,
}


def classify_mistake(field: str, old, new) -> str:
    """Return a short mistake-type tag for an agent correction. Used for
    learning-signal weighting. Best-effort — falls back to a generic tag."""
    f = (field or "").strip().lower()
    o = ("" if old is None else str(old)).strip().lower()
    n = ("" if new is None else str(new)).strip().lower()
    if f in ("urgency_level", "urgency"):
        oi = _URGENCY_RANK.get(o, -1)
        ni = _URGENCY_RANK.get(n, -1)
        if oi >= 0 and ni >= 0:
            if ni > oi: return "urgency_underestimation"
            if ni < oi: return "urgency_overestimation"
        return "urgency_changed"
    if f == "issue_type":
        return "wrong_issue_type"
    if f == "location":
        if not o and n: return "missed_location"
        if o and not n: return "spurious_location"
        return "wrong_location"
    if f == "dialect":
        return "wrong_dialect"
    if f in ("persons", "organizations", "time_references", "objects"):
        if not o and n: return f"missed_{f}"
        if o and not n: return f"spurious_{f}"
        return f"wrong_{f}"
    return "other_correction"


def record_confirmation(call_id: str, turn_id: str, response: str, raw_text: str = ""):
    db.insert_confirmation(call_id, turn_id, datetime.utcnow().isoformat() + "Z",
                            response, raw_text or "")


def record_correction(call_id: str, turn_id: str, field: str, old, new, by: str):
    """Persist the correction. The mistake_type tag is computed and folded
    into the new_value JSON so the existing schema doesn't need migrating."""
    mistake = classify_mistake(field, old, new)
    # Wrap new value with the tag so downstream consumers see both.
    enriched = {"value": new, "mistake_type": mistake}
    db.insert_correction(call_id, turn_id, datetime.utcnow().isoformat() + "Z",
                          field, old, enriched, by)
    return mistake


def stats():
    return {
        "confirmations": db.get_confirmations_count(),
        "corrections": db.get_corrections_count(),
        "dialects_seen": db.dialect_distribution(),
        "state_distribution": db.state_distribution(),
    }
