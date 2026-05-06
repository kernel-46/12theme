"""Hash-chained audit ledger.

Every meaningful event during a call (turn-committed, correction, confirmation,
handover) is appended to an append-only ledger where each row's hash includes
the previous row's hash. Tampering with any row breaks the chain.

The hash is computed over the JSON STRING form of the payload so the chain
verifies identically across SQLite and Postgres backends.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime
from typing import Dict, Any, Optional
from .. import db


def _hash(prev_hash: str, payload_str: str, ts: str, action: str, actor: str) -> str:
    h = hashlib.sha256()
    h.update((prev_hash or "").encode())
    h.update(b"|")
    h.update(action.encode())
    h.update(b"|")
    h.update(actor.encode())
    h.update(b"|")
    h.update(ts.encode())
    h.update(b"|")
    h.update(payload_str.encode())
    return h.hexdigest()


def append(call_id: str, action: str, actor: str,
           payload: Optional[Dict[str, Any]] = None,
           turn_id: Optional[str] = None) -> Dict[str, Any]:
    ts = datetime.utcnow().isoformat() + "Z"
    payload_str = json.dumps(payload or {}, sort_keys=True, default=str)
    prev_hash = db.audit_last_hash() or ""
    new_hash = _hash(prev_hash, payload_str, ts, action, actor)
    seq = db.audit_insert(call_id, turn_id, ts, action, actor,
                          payload_str, prev_hash, new_hash)
    return {
        "seq": seq, "timestamp": ts,
        "action": action, "actor": actor,
        "hash": new_hash, "prev_hash": prev_hash,
    }


def list_for_call(call_id: str):
    return db.audit_list_for_call(call_id)


def verify_chain(call_id: Optional[str] = None) -> Dict[str, Any]:
    """Re-hash every row in order; report tampering if mismatch.
    The hash chain is GLOBAL (spans all calls). When call_id is given we still
    rebuild from the global chain but only inspect rows belonging to that call.
    """
    rows = db.audit_all()
    expected_prev = ""
    bad = []
    inspected = 0
    for r in rows:
        in_filter = (call_id is None) or (r.get("call_id") == call_id)
        if in_filter:
            inspected += 1
            h = _hash(r.get("prev_hash") or "", r.get("payload_json") or "",
                      r.get("timestamp") or "",
                      r.get("action") or "", r.get("actor") or "")
            if (r.get("prev_hash") or "") != expected_prev:
                bad.append({"seq": r.get("seq"), "issue": "broken_link"})
            if h != r.get("hash"):
                bad.append({"seq": r.get("seq"), "issue": "hash_mismatch"})
        expected_prev = r.get("hash") or ""
    return {"rows": inspected, "ok": len(bad) == 0, "issues": bad}
