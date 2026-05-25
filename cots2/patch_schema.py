
import re, sqlite3
from pathlib import Path

BASE = Path.home() / 'Projects' / 'helm'
schema_path = BASE / 'helm' / 'schema.sql'
schema = schema_path.read_text()

new_watchlist = """-- ============================================================
-- WATCHLIST
-- Your trading universe in one table.
-- Three levels: added -> optionable (screened) -> willing_to_own
-- Fundamentals refreshed by helm screen on each run.
-- ============================================================
CREATE TABLE IF NOT EXISTS watchlist (
    id               TEXT PRIMARY KEY,
    ticker           TEXT NOT NULL UNIQUE,
    company_name     TEXT,
    sector           TEXT,

    -- Optionability (set by helm screen)
    is_optionable    INTEGER NOT NULL DEFAULT 0,
    last_screened_at TEXT,

    -- Qualitative judgment
    willing_to_own   INTEGER NOT NULL DEFAULT 1,
    thesis           TEXT,

    -- Fundamentals (refreshed by helm screen)
    market_cap       REAL,             -- in billions
    avg_daily_volume REAL,             -- avg daily stock volume (shares)
    week_52_high     REAL,
    week_52_low      REAL,
    beta             REAL,
    dividend_yield   REAL,             -- annual yield as decimal (0.02 = 2%)
    next_earnings    TEXT,             -- ISO date of next earnings
    last_fundamentals_at TEXT,         -- when fundamentals were last fetched

    added_at         TEXT NOT NULL DEFAULT (datetime('now')),
    notes            TEXT
);
"""

# Replace the watchlist table definition
schema = re.sub(
    r'-- ={4,}\n-- WATCHLIST.*?;\n',
    new_watchlist,
    schema,
    flags=re.DOTALL
)

# Update version line
schema = schema.replace(
    '-- v1.4: import_pathways',
    '-- v1.4: import_pathways; risk_pct_per_trade\n-- v1.5: watchlist fundamentals'
)

schema_path.write_text(schema)

# Validate
conn = sqlite3.connect(':memory:')
try:
    conn.executescript(schema)
    cols = [c[1] for c in conn.execute('PRAGMA table_info(watchlist)').fetchall()]
    needed = ['market_cap','avg_daily_volume','week_52_high','week_52_low','beta','dividend_yield','next_earnings']
    missing = [f for f in needed if f not in cols]
    if missing:
        print('MISSING:', missing)
    else:
        print('OK - all fields present')
        print('cols:', cols)
except Exception as e:
    print('ERROR:', e)
finally:
    conn.close()
