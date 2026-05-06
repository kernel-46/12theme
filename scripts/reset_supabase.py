"""Drop and recreate the Pratyaya schema in Supabase / Postgres.

DESTRUCTIVE — wipes every call, turn, correction, confirmation, and audit
ledger row. Run this once after switching to the Supabase backend (or any
time you want a clean slate).

Usage:
    python -m scripts.reset_supabase           # asks for confirmation
    python -m scripts.reset_supabase --yes     # skips the prompt

Reads connection details from .env via backend.config:
    SUPABASE_DB_HOST / SUPABASE_DB_PORT / SUPABASE_DB_NAME /
    SUPABASE_DB_USER / SUPABASE_DB_PASSWORD
"""
from __future__ import annotations
import sys
from backend import config
from backend import db_pg


def main(argv: list[str]) -> int:
    if config.DB_BACKEND != "postgres":
        print(f"DB_BACKEND is '{config.DB_BACKEND}', not 'postgres'.")
        print("Set DB_BACKEND=postgres in .env first.")
        return 2
    if not config.SUPABASE_DB_HOST or not config.SUPABASE_DB_PASSWORD:
        print("SUPABASE_DB_HOST or SUPABASE_DB_PASSWORD is empty in .env.")
        return 2

    print(f"Target : {config.SUPABASE_DB_HOST}:{config.SUPABASE_DB_PORT}"
          f"/{config.SUPABASE_DB_NAME} as {config.SUPABASE_DB_USER}")
    if "--yes" not in argv and "-y" not in argv:
        ans = input("This will DROP all Pratyaya tables and data. Type 'yes' to continue: ")
        if ans.strip().lower() != "yes":
            print("Aborted.")
            return 1

    print("Dropping and recreating schema…", flush=True)
    try:
        db_pg.reset_db()
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        print()
        print("Common causes:")
        print("  - Wrong password. Reset it in Supabase:")
        print("    Project Settings -> Database -> Reset database password")
        print("    Then update SUPABASE_DB_PASSWORD in .env (no quotes).")
        print("  - IPv6-only resolution. db.<ref>.supabase.co sometimes")
        print("    resolves to IPv6 only. If your network is IPv4-only,")
        print("    switch to the pooler in .env:")
        print("      SUPABASE_DB_HOST=aws-0-<region>.pooler.supabase.com")
        print("      SUPABASE_DB_PORT=6543")
        print("      SUPABASE_DB_USER=postgres.<project-ref>")
        print("    (Find these in Supabase Project Settings -> Database")
        print("     -> Connection pooling.)")
        return 3

    print("Schema reset OK. Tables: calls · turns · audit_ledger · corrections · confirmations.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
