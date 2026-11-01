"""
AITokensHub - Supabase/Postgres storage backend.

Drop-in persistence for common.load_json / common.save_json.
When the DATABASE_URL env var is set, JSON docs live in the `ath_kv_store`
table (same Supabase project as FamBridge, separate table).
When it is unset or unreachable, everything falls back to local data/*.json
files, so the bot and API keep working in both modes.

Run once in the Supabase SQL editor:

    create table if not exists ath_kv_store (
      key text primary key,
      value jsonb not null,
      updated_at timestamptz default now()
    );

Then set DATABASE_URL on Render (service -> Environment).
"""

import json
import os
import sys

TABLE = "ath_kv_store"

_MISSING = object()
# Sentinel: the database was unreachable. This is NOT the same as "key absent"
# (_MISSING). Callers must never treat _DB_ERROR as empty data, otherwise a
# transient outage permanently overwrites real rows with defaults.
_DB_ERROR = object()
_logged_ok = False


def _connect():
    # Lazy import: file-mode keeps working even if psycopg2 is not installed.
    # Fresh short-lived connection per call: safe on Render free + Supabase pooler,
    # no stale-connection surprises across redeploys/restarts.
    import psycopg2

    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)


def db_enabled():
    """True when a Postgres URL is configured."""
    return bool(os.getenv("DATABASE_URL", "").strip())


def _note_ok_once():
    global _logged_ok
    if not _logged_ok:
        _logged_ok = True
        print(f"[db_store] connected: using Supabase {TABLE} for persistence", flush=True)


def db_load(key, default=_MISSING):
    """Return the stored JSON value for key, or `default` when absent/unreachable."""
    if not db_enabled():
        return default
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT value FROM {TABLE} WHERE key = %s", (key,))
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return default
        _note_ok_once()
        v = row[0]
        # psycopg2 usually returns jsonb already parsed; handle raw text too.
        return json.loads(v) if isinstance(v, str) else v
    except Exception as e:  # DB down/misconfigured -> caller falls back to files
        print(f"[db_store] load failed for {key!r}: {e}", file=sys.stderr, flush=True)
        return _DB_ERROR


def db_save(key, data):
    """Upsert the JSON value for key. True on success, False on failure."""
    if not db_enabled():
        return False
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {TABLE} (key, value, updated_at)"
                    " VALUES (%s, %s::jsonb, now())"
                    " ON CONFLICT (key) DO UPDATE"
                    " SET value = EXCLUDED.value, updated_at = now()",
                    (key, json.dumps(data)),
                )
            conn.commit()
        finally:
            conn.close()
        _note_ok_once()
        return True
    except Exception as e:
        print(f"[db_store] save failed for {key!r}: {e}", file=sys.stderr, flush=True)
        return False
