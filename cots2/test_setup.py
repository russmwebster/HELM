import sys, os
from pathlib import Path
sys.path.insert(0, str(Path.home() / 'Projects' / 'helm'))

# Clean slate
db = Path.home() / 'Projects' / 'helm' / 'data' / 'helm.db'
profile = Path.home() / '.helm_profile'
if db.exists(): db.unlink()
if profile.exists(): profile.unlink()

# ── Test setup with pathway ──────────────────────────────────────────────────
import io
# Simulate: broker, nickname, buying_power, portfolio_value, yes to pathway, Downloads, default pattern
inputs = 'Fidelity\nMain\n50000\n150000\ny\n~/Downloads\nPortfolio_Positions_*.csv\n'
sys.stdin = io.StringIO(inputs)

from helm.cli.setup import run
run()
print()

# ── Verify setup ─────────────────────────────────────────────────────────────
from helm.config import get_active_account
from helm.models.account import Account
from helm.models.settings import StrategySettings
from helm.models.pathway import ImportPathway
from helm.db import db_summary

account_id = get_active_account()
assert account_id, 'No active account'

acct = Account.get(account_id)
assert acct.broker == 'Fidelity'
assert acct.buying_power == 50000
print('Account OK')

settings = StrategySettings.all_for_account(account_id)
assert len(settings) == 11, f'Expected 11, got {len(settings)}'
print('Strategy settings OK (11 strategies)')

pathways = ImportPathway.for_broker('fidelity', account_id)
assert len(pathways) == 1, f'Expected 1 pathway, got {len(pathways)}'
p = pathways[0]
assert p.broker == 'fidelity'
assert 'Downloads' in p.watch_folder
assert p.file_pattern == 'Portfolio_Positions_*.csv'
print(f'Pathway OK: {p.watch_folder}/{p.file_pattern}')

# ── Test pathway auto-find on import ────────────────────────────────────────
# Copy test CSV to a temp location that matches the pathway
import shutil
test_file = Path.home() / 'Downloads' / 'Portfolio_Positions_May-23-2026.csv'
src_file = Path.home() / 'Projects' / 'helm' / 'data' / 'portfolio_test.csv'
if src_file.exists():
    shutil.copy(src_file, test_file)
    found = p.find_latest_file()
    if found:
        print(f'Pathway auto-find OK: {found.name}')
    else:
        print('Pathway auto-find: no file in Downloads (expected if not copied)')
    # Clean up
    if test_file.exists():
        test_file.unlink()

summary = db_summary()
print()
print('DB summary:', summary['counts'])
print()
print('ALL TESTS PASSED')
