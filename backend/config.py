import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
SCRAPER_KEY = os.getenv("SCRAPER_KEY", "")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
ASR_MODEL = os.getenv("ASR_MODEL", "whisper-large-v3")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_FAST_MODEL = os.getenv("LLM_FAST_MODEL", "llama-3.1-8b-instant")
DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "pratyaya.db"))

# Storage backend: "sqlite" or "postgres" (Supabase).
DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").lower()
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_DB_HOST = os.getenv("SUPABASE_DB_HOST", "")
SUPABASE_DB_PORT = int(os.getenv("SUPABASE_DB_PORT", "5432"))
SUPABASE_DB_NAME = os.getenv("SUPABASE_DB_NAME", "postgres")
SUPABASE_DB_USER = os.getenv("SUPABASE_DB_USER", "postgres")
SUPABASE_DB_PASSWORD = os.getenv("SUPABASE_DB_PASSWORD", "")

# Confidence thresholds for the three-state machine
T_VERIFIED = 0.78
T_CLARIFY = 0.55
# Below T_CLARIFY -> HANDOVER

# Sentiment thresholds for distress / urgency-driven handover
DISTRESS_HANDOVER = 0.80
URGENCY_HANDOVER = 0.85
