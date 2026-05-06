"""Postgres / Supabase backend.

Mirrors the function signatures of the SQLite db.py module so the rest of
the application doesn't care which backend is in use.
"""
from __future__ import annotations
import json
from contextlib import contextmanager
from typing import Dict, Any
import psycopg
from psycopg.rows import dict_row
from . import config


_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    agent_id TEXT,
    citizen_lang_pref TEXT,
    final_state TEXT,
    summary TEXT,
    caller_id TEXT,
    geo_lat DOUBLE PRECISION,
    geo_lng DOUBLE PRECISION,
    geo_accuracy DOUBLE PRECISION,
    geo_label TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls(call_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    transcript_native TEXT,
    transcript_english TEXT,
    language TEXT,
    dialect TEXT,
    interpretation_json JSONB,
    sentiment_json JSONB,
    state TEXT,
    paraphrase TEXT,
    asr_confidence REAL,
    intent_confidence REAL,
    overall_confidence REAL
);

CREATE TABLE IF NOT EXISTS audit_ledger (
    seq BIGSERIAL PRIMARY KEY,
    call_id TEXT NOT NULL,
    turn_id TEXT,
    timestamp TIMESTAMPTZ NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT,        -- stored as canonical JSON string for hashing
    prev_hash TEXT,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corrections (
    id BIGSERIAL PRIMARY KEY,
    call_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    field TEXT NOT NULL,
    old_value JSONB,
    new_value JSONB,
    corrected_by TEXT
);

CREATE TABLE IF NOT EXISTS confirmations (
    id BIGSERIAL PRIMARY KEY,
    call_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    response TEXT NOT NULL,
    raw_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_turns_call ON turns(call_id, seq);
CREATE INDEX IF NOT EXISTS idx_audit_call ON audit_ledger(call_id, seq);
CREATE INDEX IF NOT EXISTS idx_audit_seq ON audit_ledger(seq);
CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_id);
"""

# Forward-migration steps for older schemas. Each is wrapped in its own
# try/except (psycopg's UndefinedTable/DuplicateColumn) so existing
# deployments roll forward without manual intervention.
_MIGRATIONS = [
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS caller_id TEXT",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS geo_lat DOUBLE PRECISION",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS geo_lng DOUBLE PRECISION",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS geo_accuracy DOUBLE PRECISION",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS geo_label TEXT",
    "CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_id)",
]


# Order matters — children before parents because of FK constraints.
_RESET_SQL = """
DROP TABLE IF EXISTS confirmations CASCADE;
DROP TABLE IF EXISTS corrections CASCADE;
DROP TABLE IF EXISTS audit_ledger CASCADE;
DROP TABLE IF EXISTS turns CASCADE;
DROP TABLE IF EXISTS calls CASCADE;
"""


def _connect():
    return psycopg.connect(
        host=config.SUPABASE_DB_HOST,
        port=config.SUPABASE_DB_PORT,
        dbname=config.SUPABASE_DB_NAME,
        user=config.SUPABASE_DB_USER,
        password=config.SUPABASE_DB_PASSWORD,
        connect_timeout=10,
        sslmode="require",
        row_factory=dict_row,
    )


def init_db():
    with _connect() as c:
        c.execute(_SCHEMA)
        for ddl in _MIGRATIONS:
            try:
                c.execute(ddl)
            except Exception:
                # idempotent — ignore "already exists" / "column already exists"
                c.rollback()
                continue
        c.commit()


def reset_db():
    """Destructive: drop ALL tables and recreate them from _SCHEMA. Used by
    the one-shot reset script when switching environments."""
    with _connect() as c:
        c.execute(_RESET_SQL)
        c.execute(_SCHEMA)
        c.commit()


@contextmanager
def conn():
    c = _connect()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _jsonb(v):
    """psycopg3 wants a Json adapter for JSONB params."""
    from psycopg.types.json import Json
    return Json(v)


# ---------- Writes ----------
def insert_call(call_id, started_at, agent_id, lang_pref,
                caller_id=None, geo=None):
    geo = geo or {}
    with conn() as c:
        c.execute(
            """INSERT INTO calls(call_id, started_at, agent_id, citizen_lang_pref,
                                  caller_id, geo_lat, geo_lng, geo_accuracy, geo_label)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (call_id) DO NOTHING""",
            (call_id, started_at, agent_id, lang_pref,
             caller_id, geo.get("lat"), geo.get("lng"),
             geo.get("accuracy"), geo.get("label")),
        )


def prior_calls_for_caller(caller_id: str, limit: int = 10):
    """Return summary rows for prior calls from the same caller. Used by
    the agent dashboard's repeat-caller card."""
    if not caller_id:
        return []
    with conn() as c:
        rows = c.execute(
            """SELECT call_id, started_at, ended_at, final_state, summary
               FROM calls
               WHERE caller_id = %s
               ORDER BY started_at DESC
               LIMIT %s""",
            (caller_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        for k in ("started_at", "ended_at"):
            if r.get(k) is not None:
                r[k] = r[k].isoformat()
        out.append(r)
    return out


def end_call(call_id, ended_at, final_state, summary):
    with conn() as c:
        c.execute(
            "UPDATE calls SET ended_at=%s, final_state=%s, summary=%s WHERE call_id=%s",
            (ended_at, final_state, summary, call_id),
        )


def insert_turn(call_id, turn):
    with conn() as c:
        cur = c.execute("SELECT COUNT(*) AS n FROM turns WHERE call_id=%s", (call_id,))
        seq = cur.fetchone()["n"] + 1
        ip = turn.get("interpretation", {}) or {}
        c.execute(
            """INSERT INTO turns(turn_id, call_id, seq, timestamp, transcript_native,
            transcript_english, language, dialect, interpretation_json, sentiment_json,
            state, paraphrase, asr_confidence, intent_confidence, overall_confidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (turn_id) DO NOTHING""",
            (
                turn["turn_id"], call_id, seq, turn["timestamp"],
                turn.get("transcript_native", ""),
                turn.get("transcript_english", ""),
                turn.get("detected_language", ""),
                turn.get("detected_dialect", ""),
                _jsonb(ip),
                _jsonb(turn.get("sentiment", {})),
                turn.get("state", "CLARIFY"),
                turn.get("paraphrase_text", ""),
                ip.get("asr_confidence", 0.0),
                ip.get("intent_confidence", 0.0),
                ip.get("overall_confidence", 0.0),
            ),
        )


def audit_last_hash():
    with conn() as c:
        row = c.execute(
            "SELECT hash FROM audit_ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["hash"] if row else ""


def audit_insert(call_id, turn_id, ts, action, actor, payload_str, prev_hash, new_hash):
    with conn() as c:
        # payload_json is TEXT (we hash the string form). Insert as text.
        cur = c.execute(
            """INSERT INTO audit_ledger(call_id, turn_id, timestamp, action, actor,
               payload_json, prev_hash, hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING seq""",
            (call_id, turn_id, ts, action, actor, payload_str, prev_hash, new_hash),
        )
        return cur.fetchone()["seq"]


def audit_list_for_call(call_id):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM audit_ledger WHERE call_id=%s ORDER BY seq", (call_id,)
        ).fetchall()
        out = []
        for r in rows:
            r = dict(r)
            if r.get("timestamp") is not None:
                r["timestamp"] = r["timestamp"].isoformat()
            out.append(r)
        return out


def audit_all():
    with conn() as c:
        rows = c.execute("SELECT * FROM audit_ledger ORDER BY seq").fetchall()
        out = []
        for r in rows:
            r = dict(r)
            if r.get("timestamp") is not None:
                r["timestamp"] = r["timestamp"].isoformat()
            out.append(r)
        return out


def insert_correction(call_id, turn_id, ts, field, old, new, by):
    with conn() as c:
        c.execute(
            """INSERT INTO corrections(call_id, turn_id, timestamp, field,
               old_value, new_value, corrected_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (call_id, turn_id, ts, field, _jsonb(old), _jsonb(new), by),
        )


def insert_confirmation(call_id, turn_id, ts, response, raw_text):
    with conn() as c:
        c.execute(
            """INSERT INTO confirmations(call_id, turn_id, timestamp, response, raw_text)
               VALUES (%s, %s, %s, %s, %s)""",
            (call_id, turn_id, ts, response, raw_text),
        )


# ---------- Reads ----------
def list_calls(limit=50):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM calls ORDER BY started_at DESC LIMIT %s", (limit,)
        ).fetchall()
        # Convert datetimes to ISO strings for JSON serialisation
        out = []
        for r in rows:
            r = dict(r)
            for k in ("started_at", "ended_at"):
                if r.get(k) is not None:
                    r[k] = r[k].isoformat()
            out.append(r)
        return out


def get_call(call_id):
    with conn() as c:
        row = c.execute("SELECT * FROM calls WHERE call_id=%s", (call_id,)).fetchone()
        if not row:
            return None
        call = dict(row)
        for k in ("started_at", "ended_at"):
            if call.get(k) is not None:
                call[k] = call[k].isoformat()
        turns = c.execute(
            "SELECT * FROM turns WHERE call_id=%s ORDER BY seq", (call_id,)
        ).fetchall()
        out_turns = []
        for t in turns:
            t = dict(t)
            if t.get("timestamp") is not None:
                t["timestamp"] = t["timestamp"].isoformat()
            # JSONB columns come back as Python objects already; mirror SQLite
            # by also providing the *_json variants as JSON strings so the
            # rest of the app (which uses json.loads(...)) keeps working.
            t["interpretation_json"] = json.dumps(t.get("interpretation_json") or {})
            t["sentiment_json"] = json.dumps(t.get("sentiment_json") or {})
            out_turns.append(t)
        call["turns"] = out_turns
        return call


def get_corrections_count():
    with conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM corrections").fetchone()["n"]


def get_confirmations_count():
    with conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM confirmations").fetchone()["n"]


def dialect_distribution():
    with conn() as c:
        rows = c.execute(
            "SELECT dialect, COUNT(*) AS n FROM turns WHERE dialect <> '' GROUP BY dialect"
        ).fetchall()
        return {r["dialect"]: r["n"] for r in rows}


def state_distribution():
    with conn() as c:
        rows = c.execute(
            "SELECT state, COUNT(*) AS n FROM turns GROUP BY state"
        ).fetchall()
        return {r["state"]: r["n"] for r in rows}


def analytics_snapshot(recent_n=30):
    """Aggregation for the civic-sensor page."""
    with conn() as c:
        total_calls = c.execute("SELECT COUNT(*) AS n FROM calls").fetchone()["n"]
        total_turns = c.execute("SELECT COUNT(*) AS n FROM turns").fetchone()["n"]
        states = {r["state"]: r["n"] for r in c.execute(
            "SELECT state, COUNT(*) AS n FROM turns GROUP BY state"
        ).fetchall()}
        languages = {r["language"]: r["n"] for r in c.execute(
            "SELECT language, COUNT(*) AS n FROM turns WHERE language <> '' GROUP BY language"
        ).fetchall()}
        dialects = {r["dialect"]: r["n"] for r in c.execute(
            "SELECT dialect, COUNT(*) AS n FROM turns WHERE dialect <> '' GROUP BY dialect"
        ).fetchall()}

        # Use JSONB operators to extract summary fields server-side
        rows = c.execute(
            """SELECT interpretation_json, sentiment_json
               FROM turns ORDER BY seq DESC LIMIT 500"""
        ).fetchall()

    issue_types: Dict[str, int] = {}
    urgency: Dict[str, int] = {}
    locations: Dict[str, int] = {}
    distress_sum = 0.0
    distress_n = 0
    recent_sentiment = []

    for r in rows:
        ip = r.get("interpretation_json") or {}
        st = r.get("sentiment_json") or {}
        if not isinstance(ip, dict):
            try: ip = json.loads(ip)
            except: ip = {}
        if not isinstance(st, dict):
            try: st = json.loads(st)
            except: st = {}

        it = (ip.get("issue_type") or "other").strip() or "other"
        issue_types[it] = issue_types.get(it, 0) + 1
        u = (ip.get("urgency_level") or "normal").strip() or "normal"
        urgency[u] = urgency.get(u, 0) + 1
        loc = (ip.get("entities") or {}).get("location")
        if loc:
            locations[loc] = locations.get(loc, 0) + 1
        if "distress" in st:
            try:
                distress_sum += float(st["distress"]); distress_n += 1
            except Exception:
                pass
        if len(recent_sentiment) < recent_n and st:
            recent_sentiment.append(st)

    verified = states.get("VERIFIED", 0)
    handover = states.get("HANDOVER", 0)
    verified_rate = verified / total_turns if total_turns else 0.0
    handover_rate = handover / total_turns if total_turns else 0.0

    return {
        "total_calls": total_calls,
        "total_turns": total_turns,
        "states": states,
        "languages": languages,
        "dialects": dialects,
        "issue_types": issue_types,
        "urgency": urgency,
        "locations": locations,
        "verified_rate": round(verified_rate, 3),
        "handover_rate": round(handover_rate, 3),
        "avg_distress": round(distress_sum / distress_n, 3) if distress_n else None,
        "recent_sentiment": list(reversed(recent_sentiment)),
    }
