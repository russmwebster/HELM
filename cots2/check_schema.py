
import sqlite3
from pathlib import Path

BASE = Path.home() / 'Projects' / 'helm'
schema = (BASE / 'helm' / 'schema.sql').read_text()
conn = sqlite3.connect(':memory:')
conn.executescript(schema)
cols = [c[1] for c in conn.execute("PRAGMA table_info(watchlist)").fetchall()]
print('watchlist cols:', cols)
new_fields = ['market_cap', 'avg_daily_volume', 'week_52_high', 'week_52_low', 'beta', 'dividend_yield', 'next_earnings']
for f in new_fields:
    print(f'  {f}: {"OK" if f in cols else "MISSING"}')
