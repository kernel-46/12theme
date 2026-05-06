"""DB dispatcher.

Routes every public function to either the Postgres (Supabase) backend or
the SQLite backend depending on `config.DB_BACKEND`. Keeps the rest of the
application unaware of which storage is in use.
"""
from . import config

if config.DB_BACKEND == "postgres":
    from .db_pg import (
        init_db, conn,
        insert_call, end_call, insert_turn,
        insert_correction, insert_confirmation,
        list_calls, get_call,
        get_corrections_count, get_confirmations_count,
        dialect_distribution, state_distribution,
        analytics_snapshot,
        audit_last_hash, audit_insert, audit_list_for_call, audit_all,
        prior_calls_for_caller,
    )
else:
    from .db_sqlite import (
        init_db, conn,
        insert_call, end_call, insert_turn,
        insert_correction, insert_confirmation,
        list_calls, get_call,
        get_corrections_count, get_confirmations_count,
        dialect_distribution, state_distribution,
        analytics_snapshot,
        audit_last_hash, audit_insert, audit_list_for_call, audit_all,
        prior_calls_for_caller,
    )
