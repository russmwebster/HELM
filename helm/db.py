# helm/db.py
# HELM database layer — connection, initialization, migrations
# Single source of truth for all database access.
# All models import get_conn() from here.

import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime

from helm.config import DB_PATH, SCHEMA_PATH, SCHEMA_VERSION

logger = logging.getLogger(__name__)

# ── Connection ───────────────────────────────────────────────────────────────

def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """
    Return a SQLite connection with:
    - WAL mode for concurrent reads
    - Foreign key enforcement
    - Row factory for dict-like access
    - Sensible timeout
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn

@contextmanager
def transaction(db_path: Path = DB_PATH):
    """
    Context manager for a database transaction.
    Commits on success, rolls back on any exception.

    Usage:
        with transaction() as conn:
            conn.execute(...)
    """
    conn = get_conn(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ── Initialization ───────────────────────────────────────────────────────────

def init_db(db_path: Path = DB_PATH) -> None:
    """
    Initialize the HELM database from schema.sql.
    Safe to call multiple times — uses CREATE IF NOT EXISTS throughout.
    Records schema version in the helm_meta table.
    """
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f'Schema file not found: {SCHEMA_PATH}')

    schema_sql = SCHEMA_PATH.read_text()

    with transaction(db_path) as conn:
        # Apply the full schema (idempotent)
        conn.executescript(schema_sql)

        # Create meta table if it doesn't exist
        conn.execute("""
            CREATE TABLE IF NOT EXISTS helm_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Record schema version and init timestamp
        conn.execute("""
            INSERT OR REPLACE INTO helm_meta (key, value)
            VALUES ('schema_version', ?)
        """, (SCHEMA_VERSION,))

        conn.execute("""
            INSERT OR IGNORE INTO helm_meta (key, value)
            VALUES ('initialized_at', ?)
        """, (datetime.now().isoformat(),))

    logger.info(f'Database initialized at {db_path} (schema v{SCHEMA_VERSION})')

def get_meta(key: str, db_path: Path = DB_PATH) -> str | None:
    """Read a value from the helm_meta table."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            'SELECT value FROM helm_meta WHERE key = ?', (key,)
        ).fetchone()
        return row['value'] if row else None
    finally:
        conn.close()

def check_schema_version(db_path: Path = DB_PATH) -> dict:
    """
    Check whether the database schema matches the current version.
    Returns a dict with 'ok', 'db_version', 'expected_version'.
    """
    try:
        db_version = get_meta('schema_version', db_path)
        ok = db_version == SCHEMA_VERSION
        return {
            'ok': ok,
            'db_version': db_version,
            'expected_version': SCHEMA_VERSION,
        }
    except Exception as e:
        return {
            'ok': False,
            'db_version': None,
            'expected_version': SCHEMA_VERSION,
            'error': str(e),
        }

# ── Utilities ────────────────────────────────────────────────────────────────

def row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row) if row else {}

def rows_to_dicts(rows: list) -> list[dict]:
    """Convert a list of sqlite3.Row objects to plain dicts."""
    return [dict(r) for r in rows]

def table_exists(table: str, db_path: Path = DB_PATH) -> bool:
    """Check whether a table exists in the database."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()

def db_summary(db_path: Path = DB_PATH) -> dict:
    """
    Return a summary of the database state.
    Used by 'helm status' and health checks.
    """
    conn = get_conn(db_path)
    try:
        tables = ['accounts', 'positions', 'legs', 'entry_snapshots',
                  'checks', 'lifecycle_events', 'strategy_settings', 'watchlist']
        counts = {}
        for t in tables:
            try:
                row = conn.execute(f'SELECT COUNT(*) as n FROM {t}').fetchone()
                counts[t] = row['n']
            except Exception:
                counts[t] = None

        schema_version = get_meta('schema_version', db_path)
        initialized_at = get_meta('initialized_at', db_path)

        return {
            'db_path': str(db_path),
            'schema_version': schema_version,
            'initialized_at': initialized_at,
            'counts': counts,
        }
    finally:
        conn.close()
