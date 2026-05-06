"""SQLite backend (local-dev fallback / offline mode).

Mirrors db_pg.py's public interface so `db.py` can route to either backend.
"""
import sqlite3
import json
from pathlib import Path
from contextlib import contextmanager
from typing import Dict
from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    agent_id TEXT,
    citizen_lang_pref TEXT,
    final_state TEXT,
    summary TEXT,
    caller_id TEXT,
    geo_lat REAL,
    geo_lng REAL,
    geo_accuracy REAL,
    geo_label TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    transcript_native TEXT,
    transcript_english TEXT,
    language TEXT,
    dialect TEXT,
    interpretation_json TEXT,
    sentiment_json TEXT,
    state TEXT,
    paraphrase TEXT,
    asr_confidence REAL,
    intent_confidence REAL,
    overall_confidence REAL,
    FOREIGN KEY(call_id) REFERENCES calls(call_id)
);

CREATE TABLE IF NOT EXISTS audit_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    turn_id TEXT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT,
    prev_hash TEXT,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    corrected_by TEXT
);

CREATE TABLE IF NOT EXISTS confirmations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    response TEXT NOT NULL,
    raw_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_turns_call ON turns(call_id, seq);
CREATE INDEX IF NOT EXISTS idx_audit_call ON audit_ledger(call_id, seq);
"""


def init_db():
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.DB_PATH) as con:
        con.executescript(_SCHEMA)
        # Forward-migrate: older databases pre-date caller_id / geo columns.
        # ALTER TABLE ADD COLUMN is idempotent only via try/except in SQLite.
        for ddl in (
            "ALTER TABLE calls ADD COLUMN caller_id TEXT",
            "ALTER TABLE calls ADD COLUMN geo_lat REAL",
            "ALTER TABLE calls ADD COLUMN geo_lng REAL",
            "ALTER TABLE calls ADD COLUMN geo_accuracy REAL",
            "ALTER TABLE calls ADD COLUMN geo_label TEXT",
        ):
            try:
                con.execute(ddl)
            except sqlite3.OperationalError:
                pass
        con.execute("CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_id)")
        con.commit()


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def insert_call(call_id, started_at, agent_id, lang_pref,
                caller_id=None, geo=None):
    geo = geo or {}
    with conn() as c:
        c.execute(
            """INSERT INTO calls(call_id, started_at, agent_id, citizen_lang_pref,
                                  caller_id, geo_lat, geo_lng, geo_accuracy, geo_label)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (call_id, started_at, agent_id, lang_pref,
             caller_id, geo.get("lat"), geo.get("lng"),
             geo.get("accuracy"), geo.get("label")),
        )


def prior_calls_for_caller(caller_id: str, limit: int = 10):
    """Return summary rows for a caller's earlier calls — the agent
    dashboard surfaces this as 'repeat caller' context. Excludes the
    most-recent (current) call by relying on the caller passing the
    current call_id for filtering at the API layer."""
    if not caller_id:
        return []
    with conn() as c:
        rows = c.execute(
            """SELECT call_id, started_at, ended_at, final_state, summary
               FROM calls
               WHERE caller_id = ?
               ORDER BY started_at DESC
               LIMIT ?""",
            (caller_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def end_call(call_id, ended_at, final_state, summary):
    with conn() as c:
        c.execute(
            "UPDATE calls SET ended_at=?, final_state=?, summary=? WHERE call_id=?",
            (ended_at, final_state, summary, call_id),
        )


def insert_turn(call_id, turn):
    with conn() as c:
        cur = c.execute("SELECT COUNT(*) FROM turns WHERE call_id=?", (call_id,))
        seq = cur.fetchone()[0] + 1
        c.execute(
            """INSERT INTO turns(turn_id, call_id, seq, timestamp, transcript_native,
            transcript_english, language, dialect, interpretation_json, sentiment_json,
            state, paraphrase, asr_confidence, intent_confidence, overall_confidence)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                turn["turn_id"], call_id, seq, turn["timestamp"],
                turn.get("transcript_native", ""),
                turn.get("transcript_english", ""),
                turn.get("detected_language", ""),
                turn.get("detected_dialect", ""),
                json.dumps(turn.get("interpretation", {})),
                json.dumps(turn.get("sentiment", {})),
                turn.get("state", "CLARIFY"),
                turn.get("paraphrase_text", ""),
                turn.get("interpretation", {}).get("asr_confidence", 0.0),
                turn.get("interpretation", {}).get("intent_confidence", 0.0),
                turn.get("interpretation", {}).get("overall_confidence", 0.0),
            ),
        )


def list_calls(limit=50):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_call(call_id):
    with conn() as c:
        row = c.execute("SELECT * FROM calls WHERE call_id=?", (call_id,)).fetchone()
        if not row:
            return None
        call = dict(row)
        turns = c.execute(
            "SELECT * FROM turns WHERE call_id=? ORDER BY seq", (call_id,)
        ).fetchall()
        call["turns"] = [dict(t) for t in turns]
        return call


def audit_last_hash():
    with conn() as c:
        row = c.execute(
            "SELECT hash FROM audit_ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["hash"] if row else ""


def audit_insert(call_id, turn_id, ts, action, actor, payload_str, prev_hash, new_hash):
    with conn() as c:
        cur = c.execute(
            """INSERT INTO audit_ledger(call_id, turn_id, timestamp, action, actor,
               payload_json, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?)""",
            (call_id, turn_id, ts, action, actor, payload_str, prev_hash, new_hash),
        )
        return cur.lastrowid


def audit_list_for_call(call_id):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM audit_ledger WHERE call_id=? ORDER BY seq", (call_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def audit_all():
    with conn() as c:
        rows = c.execute("SELECT * FROM audit_ledger ORDER BY seq").fetchall()
        return [dict(r) for r in rows]


def insert_correction(call_id, turn_id, ts, field, old, new, by):
    with conn() as c:
        c.execute(
            """INSERT INTO corrections(call_id, turn_id, timestamp, field, old_value, new_value, corrected_by)
               VALUES (?,?,?,?,?,?,?)""",
            (call_id, turn_id, ts, field, json.dumps(old), json.dumps(new), by),
        )


def insert_confirmation(call_id, turn_id, ts, response, raw_text):
    with conn() as c:
        c.execute(
            """INSERT INTO confirmations(call_id, turn_id, timestamp, response, raw_text)
               VALUES (?,?,?,?,?)""",
            (call_id, turn_id, ts, response, raw_text),
        )


def get_corrections_count():
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]


def get_confirmations_count():
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0]


def dialect_distribution():
    with conn() as c:
        rows = c.execute(
            "SELECT dialect, COUNT(*) as n FROM turns WHERE dialect != '' GROUP BY dialect"
        ).fetchall()
        return {r["dialect"]: r["n"] for r in rows}


def state_distribution():
    with conn() as c:
        rows = c.execute(
            "SELECT state, COUNT(*) as n FROM turns GROUP BY state"
        ).fetchall()
        return {r["state"]: r["n"] for r in rows}


def analytics_snapshot(recent_n=30):
    with conn() as c:
        total_calls = c.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        total_turns = c.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        states = {r["state"]: r["n"] for r in c.execute(
            "SELECT state, COUNT(*) as n FROM turns GROUP BY state"
        ).fetchall()}
        languages = {r["language"]: r["n"] for r in c.execute(
            "SELECT language, COUNT(*) as n FROM turns WHERE language != '' GROUP BY language"
        ).fetchall()}
        dialects = {r["dialect"]: r["n"] for r in c.execute(
            "SELECT dialect, COUNT(*) as n FROM turns WHERE dialect != '' GROUP BY dialect"
        ).fetchall()}

        rows = c.execute(
            "SELECT interpretation_json, sentiment_json FROM turns ORDER BY rowid DESC LIMIT 500"
        ).fetchall()

    issue_types: Dict[str, int] = {}
    urgency: Dict[str, int] = {}
    locations: Dict[str, int] = {}
    distress_sum = 0.0; distress_n = 0
    recent_sentiment = []

    for r in rows:
        try: ip = json.loads(r["interpretation_json"] or "{}")
        except Exception: ip = {}
        try: st = json.loads(r["sentiment_json"] or "{}")
        except Exception: st = {}

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

    verified = states.get("VERIFIED", 0); handover = states.get("HANDOVER", 0)
    return {
        "total_calls": total_calls,
        "total_turns": total_turns,
        "states": states,
        "languages": languages,
        "dialects": dialects,
        "issue_types": issue_types,
        "urgency": urgency,
        "locations": locations,
        "verified_rate": round(verified / total_turns, 3) if total_turns else 0.0,
        "handover_rate": round(handover / total_turns, 3) if total_turns else 0.0,
        "avg_distress": round(distress_sum / distress_n, 3) if distress_n else None,
        "recent_sentiment": list(reversed(recent_sentiment)),
    }
