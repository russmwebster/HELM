import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / 'Projects' / 'helm'))
from helm.db import get_conn, transaction
from helm.config import get_active_account

conn = get_conn()

# Show distinct account_ids in strategy_settings
rows = conn.execute('SELECT account_id, COUNT(*) as n FROM strategy_settings GROUP BY account_id').fetchall()
print('Account IDs in strategy_settings:')
for r in rows:
    print(f'  {r[0]}: {r[1]} strategies')

active = get_active_account()
print(f'Active account: {active}')

# Delete settings for non-active accounts
with transaction() as c:
    c.execute('DELETE FROM strategy_settings WHERE account_id != ?', (active,))

count = conn.execute('SELECT COUNT(*) FROM strategy_settings').fetchone()[0]
print(f'After cleanup: {count} strategy settings')
conn.close()
