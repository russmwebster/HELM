import sys
sys.path.insert(0, '/Users/russmacbookpro/Projects/helm')

# Re-init DB with v1.3 schema
import os
db_path = os.path.expanduser('~/Projects/helm/data/helm.db')
if os.path.exists(db_path):
    os.remove(db_path)

from helm.db import init_db, db_summary
init_db()
print('DB initialized (v1.3)')

# Account
from helm.models.account import Account
acct = Account.create('Fidelity', 'Main Account', id='fidelity_main',
                      buying_power=50000, portfolio_value=150000)
print('Account OK')

# Signal
from helm.models.signal import Signal
sig = Signal.create(
    ticker='NVDA',
    confirmed_bias='MILDLY_BULLISH',
    recommendations=[
        {'strategy': 'CSP', 'fit': 'STRONG', 'fit_score': 0.92,
         'reasoning': 'IV rank 67 favors premium selling. Mildly bullish bias suits short put. Delta -0.28 at 120 strike is 2.1 ATR below spot.',
         'suggested_strike': 120.0, 'suggested_dte': 30, 'atr_strikes_otm': 2.1, 'position_size_contracts': 1},
        {'strategy': 'BULL_PUT_SPREAD', 'fit': 'GOOD', 'fit_score': 0.78,
         'reasoning': 'Defined risk alternative. Same directional thesis with capped downside.',
         'suggested_strike': 120.0, 'suggested_dte': 30, 'atr_strikes_otm': 2.1, 'position_size_contracts': 2},
    ],
    iv_rank=67.0, iv_percentile=72.0, iv_current=0.48, iv_regime='HIGH',
    spot_price=128.50, rsi_14=52.3, atr_14=3.42,
    auto_bias_score=1.2, auto_bias='MILDLY_BULLISH',
    auto_bias_reasoning='Price above 20-EMA and 50-SMA. RSI neutral at 52. MACD slightly positive. Moderate bullish lean.',
    earnings_date='2026-08-20', days_to_earnings=89, earnings_warning=0,
    willing_to_own=1, is_optionable=1
)
assert sig.top_strategy == 'CSP', 'Signal top strategy mismatch'
assert sig.top_fit == 'STRONG', 'Signal top fit mismatch'
print('Signal OK')

# Position linked to signal
from helm.models.position import Position
pos = Position.create('fidelity_main', 'CSP', 'NVDA',
                      signal_id=sig.id, net_premium=3.50, total_contracts=1)
sig.record_position_opened(pos.id)
assert Signal.get(sig.id).position_opened == 1, 'Signal position_opened not updated'
print('Position + Signal linkage OK')

# Verify signal history for ticker
history = Signal.for_ticker('NVDA')
assert len(history) == 1, 'Signal history wrong length'
print('Signal history OK')

# Full DB summary
summary = db_summary()
print('DB summary:', summary['counts'])
print()
print('ALL TESTS PASSED')
