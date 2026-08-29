"""
init_db.py — Automatic schema initialisation for Дело №417.

Reads supabase_schema.sql and executes it against the Supabase PostgreSQL
database on startup, but only when the sentinel table `players` does not yet
exist.  This prevents redundant work on every restart while guaranteeing a
fresh deployment is fully bootstrapped before the bot handles any request.

Connection priority
───────────────────
1. DATABASE_URL  — set automatically by Railway's Supabase integration.
2. SUPABASE_DB_URL — explicit override (same format as DATABASE_URL).
3. Derived from SUPABASE_URL — extracts the project ref from the REST URL
   and builds the standard Supabase direct-connection string.
   Requires SUPABASE_DB_PASSWORD to be set.

Environment variables
─────────────────────
DATABASE_URL        postgresql://postgres:<pw>@db.<ref>.supabase.co:5432/postgres
SUPABASE_DB_URL     same format, alternative name
SUPABASE_URL        https://<ref>.supabase.co   (used to derive host)
SUPABASE_DB_PASSWORD password for the postgres user (only needed for option 3)
"""

import logging
import os
import re

log = logging.getLogger(__name__)

# Path to the schema file, relative to this script.
_SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "supabase_schema.sql")

# Tables that must exist for the bot to work.  We check for `players` as the
# sentinel — it is the last table created in the schema file.
_SENTINEL_TABLE = "players"


def _get_connection_string() -> str:
    """
    Return a psycopg2-compatible connection string, trying several sources.
    Raises RuntimeError if none can be resolved.
    """
    # Option 1 & 2: explicit connection URL already provided.
    for env_var in ("DATABASE_URL", "SUPABASE_DB_URL"):
        url = os.getenv(env_var, "").strip()
        if url:
            log.debug("Using connection string from %s", env_var)
            return url

    # Option 3: derive from SUPABASE_URL + SUPABASE_DB_PASSWORD.
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    db_password  = os.getenv("SUPABASE_DB_PASSWORD", "").strip()

    if supabase_url and db_password:
        # Extract project ref from https://<ref>.supabase.co
        match = re.search(r"https://([^.]+)\.supabase\.co", supabase_url)
        if match:
            project_ref = match.group(1)
            host = f"db.{project_ref}.supabase.co"
            conn_str = (
                f"postgresql://postgres:{db_password}@{host}:5432/postgres"
            )
            log.debug("Derived connection string from SUPABASE_URL (project: %s)", project_ref)
            return conn_str

    raise RuntimeError(
        "Cannot determine database connection string. "
        "Set DATABASE_URL, SUPABASE_DB_URL, or both SUPABASE_URL and SUPABASE_DB_PASSWORD."
    )


def _schema_already_applied(conn) -> bool:
    """Return True if the sentinel table already exists in the public schema."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM   information_schema.tables
                WHERE  table_schema = 'public'
                AND    table_name   = %s
            )
            """,
            (_SENTINEL_TABLE,),
        )
        return cur.fetchone()[0]


def _read_schema() -> str:
    """Read and return the SQL schema file contents."""
    if not os.path.exists(_SCHEMA_FILE):
        raise FileNotFoundError(
            f"Schema file not found: {_SCHEMA_FILE}. "
            "Make sure supabase_schema.sql is present in the project root."
        )
    with open(_SCHEMA_FILE, "r", encoding="utf-8") as fh:
        return fh.read()


def _strip_verification_query(sql: str) -> str:
    """
    Remove the trailing SELECT … UNION ALL … verification block from the
    schema file.  psycopg2 executes the whole string as one command via
    execute(), which does not support multiple result sets from a single
    call.  The CREATE / INSERT / ALTER statements are idempotent (IF NOT
    EXISTS / ON CONFLICT DO NOTHING) so the verification query is not
    needed at runtime.
    """
    # The verification block starts with the first standalone SELECT after
    # all DDL statements.  We drop everything from that point onward.
    marker = "\nSELECT 'tpl_npc'"
    idx = sql.find(marker)
    if idx != -1:
        sql = sql[:idx]
    return sql.strip()


def init_schema() -> None:
    """
    Initialise the Supabase database schema if it has not been applied yet.

    This function is intentionally synchronous — it is called once during
    bot startup, before the async event loop is entered, so there is no
    risk of blocking the Telegram polling loop.
    """
    try:
        import psycopg2  # type: ignore
    except ImportError:
        log.error(
            "psycopg2 is not installed. "
            "Add 'psycopg2-binary' to requirements.txt to enable automatic schema init."
        )
        return

    try:
        conn_str = _get_connection_string()
    except RuntimeError as exc:
        log.warning("Schema init skipped: %s", exc)
        return

    conn = None
    try:
        log.info("Connecting to database for schema check…")
        conn = psycopg2.connect(conn_str)
        conn.autocommit = False

        if _schema_already_applied(conn):
            log.info("Schema already present (table '%s' exists). Skipping init.", _SENTINEL_TABLE)
            return

        log.info("Table '%s' not found — applying schema from %s", _SENTINEL_TABLE, _SCHEMA_FILE)
        sql = _read_schema()
        sql = _strip_verification_query(sql)

        with conn.cursor() as cur:
            cur.execute(sql)

        conn.commit()
        log.info("✅ Schema applied successfully.")

    except Exception as exc:
        log.error("❌ Schema init failed: %s", exc)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
