"""In-memory active call sessions + WebSocket fan-out.

Two channel types per call:
- citizen_socket: receives paraphrase/confirm prompts, sends audio events
- dashboard_sockets: receive every state change for the agent UI
- global_dashboard_sockets: receive ALL call events (for the call list)
"""
from __future__ import annotations
import asyncio
from typing import Dict, Set, Any, Optional
from fastapi import WebSocket


class CallSession:
    def __init__(self, call_id: str, agent_id: str, lang_pref: str):
        self.call_id = call_id
        self.agent_id = agent_id
        self.lang_pref = lang_pref
        self.consecutive_no = 0  # how many times the citizen has said "no" in a row
        self.last_dialect = "Bangalore-standard-Kannada"
        self.last_language = lang_pref if lang_pref != "auto" else "kn"
        self.last_interpretation: Dict[str, Any] = {}
        self.last_sentiment: Dict[str, float] = {"calm": 1.0}
        self.last_state = "CLARIFY"
        self.started = True
        self.shadow_mode = False  # if True, paraphrase is computed but NOT sent to citizen

        # Multi-turn memory for context-aware understanding and repetition detection.
        # Keep a short rolling window of citizen transcripts + AI summaries.
        self.recent_citizen_turns: list[str] = []
        self.recent_summaries: list[str] = []
        self.location_asked: bool = False  # so we don't ask for location twice
        self.paraphrase_attempts: int = 0   # rotates the smart re-ask phrasing
        # Whether we've already told the citizen which language we detected
        # ("I'm hearing you in Kannada"). Spoken once, on the first paraphrase.
        self.language_acked: bool = False

        # NEW conversational layer: one rolling chat history feeding the LLM
        # every turn so it has full context. Each entry: {role:'citizen'|'ai',
        # text:str}. Capped to the last 16 entries to stay under token budget.
        self.chat: list[Dict[str, str]] = []
        # Slots accumulate as the conversation progresses — the LLM fills
        # them and the dashboard renders them. Persisted across turns.
        self.slots: Dict[str, Any] = {
            "issue_type": None,
            "location": None,
            "persons_involved": None,
            "urgency": None,
            "verified": False,         # citizen explicitly confirmed our understanding
        }
        # Track the last question we asked so a "yes/no" reply can be
        # interpreted IN CONTEXT, not against the original interpretation.
        self.last_ai_question: str = ""
        self.last_ai_action: str = ""   # "ask"|"verify"|"guide"|"handover"

        self.citizen_socket: Optional[WebSocket] = None
        self.dashboard_sockets: Set[WebSocket] = set()
        self._lock = asyncio.Lock()


class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, CallSession] = {}
        self.global_dashboard_sockets: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def create(self, call_id: str, agent_id: str, lang_pref: str) -> CallSession:
        async with self._lock:
            s = CallSession(call_id, agent_id, lang_pref)
            self.sessions[call_id] = s
            return s

    def get(self, call_id: str) -> Optional[CallSession]:
        return self.sessions.get(call_id)

    async def close(self, call_id: str):
        s = self.sessions.pop(call_id, None)
        if not s:
            return
        # gracefully close sockets
        targets = list(s.dashboard_sockets)
        if s.citizen_socket:
            targets.append(s.citizen_socket)
        for ws in targets:
            try:
                await ws.close()
            except Exception:
                pass

    async def broadcast_dashboard(self, call_id: str, message: Dict[str, Any]):
        s = self.get(call_id)
        if not s:
            return
        dead = []
        for ws in list(s.dashboard_sockets):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for d in dead:
            s.dashboard_sockets.discard(d)
        # also fan to global
        for ws in list(self.global_dashboard_sockets):
            try:
                await ws.send_json({"call_id": call_id, **message})
            except Exception:
                pass

    async def send_citizen(self, call_id: str, message: Dict[str, Any]):
        s = self.get(call_id)
        if not s or not s.citizen_socket:
            return
        try:
            await s.citizen_socket.send_json(message)
        except Exception:
            s.citizen_socket = None


manager = SessionManager()
