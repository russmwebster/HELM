
import sqlite3, os
from pathlib import Path

BASE = Path.home() / 'Projects' / 'helm'

# Validate schema
db = str(BASE / 'helm' / 'helm_validate_v14.db')
try:
    conn = sqlite3.connect(db)
    schema = (BASE / 'helm' / 'schema.sql').read_text()
    conn.executescript(schema)
    conn.commit()
    tables = [t[0] for t in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    
    # Check new additions
    ss_cols = [c[1] for c in conn.execute("PRAGMA table_info(strategy_settings)").fetchall()]
    pp_cols = [c[1] for c in conn.execute("PRAGMA table_info(import_pathways)").fetchall()]
    
    print('TABLES:', tables)
    print('TABLE COUNT:', len(tables))
    print('INDEX COUNT:', len(indexes))
    print('strategy_settings has risk_pct_per_trade:', 'risk_pct_per_trade' in ss_cols)
    print('import_pathways columns:', pp_cols)
    print('OK')
    conn.close()
    os.remove(db)
except Exception as e:
    print('ERROR:', e)
    import traceback; traceback.print_exc()

# Update config.py version
config_path = BASE / 'helm' / 'config.py'
config = config_path.read_text()
config = config.replace("SCHEMA_VERSION = '1.3'", "SCHEMA_VERSION = '1.4'")
config_path.write_text(config)
print('config.py version updated to 1.4')
