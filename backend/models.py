from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class Sentiment6D(BaseModel):
    distress: float = 0.0
    urgency: float = 0.0
    anger: float = 0.0
    fear: float = 0.0
    confusion: float = 0.0
    calm: float = 1.0


class Entities(BaseModel):
    location: Optional[str] = None
    persons: List[str] = []
    organizations: List[str] = []
    time_references: List[str] = []
    objects: List[str] = []


class Interpretation(BaseModel):
    issue_type: Optional[str] = None
    issue_summary: Optional[str] = None
    urgency_level: str = "normal"  # low|normal|high|critical
    factual_claims: List[str] = []
    entities: Entities = Entities()
    intent_confidence: float = 0.0
    asr_confidence: float = 0.0
    overall_confidence: float = 0.0


class TurnResult(BaseModel):
    call_id: str
    turn_id: str
    timestamp: datetime
    transcript_native: str = ""
    transcript_english: str = ""
    detected_language: str = "kn"
    detected_dialect: str = "Bangalore-standard"
    interpretation: Interpretation = Interpretation()
    sentiment: Sentiment6D = Sentiment6D()
    state: str = "CLARIFY"  # VERIFIED|CLARIFY|HANDOVER
    paraphrase_text: Optional[str] = None
    paraphrase_lang: str = "kn-IN"
    pii_redacted_count: int = 0


class StartCallReq(BaseModel):
    citizen_lang_pref: Optional[str] = "auto"
    agent_id: Optional[str] = "agent-001"


class CorrectionReq(BaseModel):
    call_id: str
    turn_id: str
    field: str
    old_value: Any
    new_value: Any
    corrected_by: str = "agent"


class ConfirmReq(BaseModel):
    call_id: str
    turn_id: str
    response: str  # "yes"|"no"|"partial"
    raw_text: Optional[str] = None


class HandoverReq(BaseModel):
    call_id: str
    reason: str = "agent_initiated"
    note: Optional[str] = None
