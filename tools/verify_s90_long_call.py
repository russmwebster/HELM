#!/usr/bin/env python3
"""Verify the s90 long-call operationalization.

Behavioural where it can be (journal_state, capture_entry_thesis, the paper
booker's direction), structural only where a behavioural test would need a real
broker chain -- and each structural check says so in its own label.

The harness is written to FAIL against the pre-change tree. Run it before
applying and it should report failures; run it after and it should report none.
That differential is the evidence, not the pass on its own.

    python3 tools/verify_s90_long_call.py
"""

import os
import sys
import ast
import types
import sqlite3
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OK = 0
FAIL = 0


def chk(cond, label):
    global OK, FAIL
    if cond:
        OK += 1
        print('  ok   ' + label)
    else:
        FAIL += 1
        print('  FAIL ' + label)
    return bool(cond)


def fresh_db():
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE checks (position_id TEXT, checked_at TEXT, '
              'pnl_pct REAL, thesis_broken INTEGER, data_quality TEXT)')
    c.execute('CREATE TABLE signals (ticker TEXT, generated_at TEXT, '
              'spot_price REAL, sma_50 REAL, sma_200 REAL, '
              'auto_bias_score REAL, adx REAL)')
    c.execute('CREATE TABLE entry_thesis (position_id TEXT PRIMARY KEY, '
              'captured_at TEXT, source TEXT, signals_generated_at TEXT, '
              'bias_score REAL, spot_price REAL, sma_50 REAL, sma_200 REAL, '
              'adx REAL, notes TEXT)')
    return c


def stub_scan_cmd():
    """Make the live-pull fallback return nothing, without touching a network."""
    m = types.ModuleType('helm.cli.scan_cmd')
    m.fetch_technicals = lambda t: {}
    sys.modules['helm.cli.scan_cmd'] = m


print('verify s90 -- long-call path')
stub_scan_cmd()
from helm import long_exit as lx

# -- HELM-117: journal_state must not read non-GOOD marks --------------------

print('\nHELM-117  journal_state quality filter')
c = fresh_db()
now = datetime.now()
rows = [
    ('P1', (now - timedelta(days=3)).isoformat(), 10.0, 0, 'GOOD'),
    ('P1', (now - timedelta(days=2)).isoformat(), 90.0, 1, 'PARTIAL'),
    ('P1', (now - timedelta(days=1)).isoformat(), 20.0, 0, 'GOOD'),
]
c.executemany('INSERT INTO checks VALUES (?,?,?,?,?)', rows)
st = lx.journal_state(c, 'P1')
chk(abs((st.get('hwm_pct') or 0) - 0.20) < 1e-9,
    'high-water mark ignores the PARTIAL +90% row (got '
    + str(st.get('hwm_pct')) + ', want 0.2)')
chk(st.get('checks_n') == 2,
    'only GOOD rows are counted (got ' + str(st.get('checks_n')) + ', want 2)')

# a PARTIAL break must not extend the streak
c2 = fresh_db()
c2.executemany('INSERT INTO checks VALUES (?,?,?,?,?)', [
    ('P2', (now - timedelta(days=2)).isoformat(), 1.0, 1, 'GOOD'),
    ('P2', (now - timedelta(days=1)).isoformat(), 1.0, 1, 'PARTIAL'),
])
st2 = lx.journal_state(c2, 'P2')
chk(st2.get('break_days') == 1,
    'a PARTIAL break does not extend the streak (got '
    + str(st2.get('break_days')) + ', want 1)')

# -- HELM-112: the writer --------------------------------------------------

print('\nHELM-112  capture_entry_thesis')
has_writer = hasattr(lx, 'capture_entry_thesis')
chk(has_writer, 'long_exit.capture_entry_thesis exists')

if has_writer:
    lx._CTX_CACHE.clear()
    c3 = fresh_db()
    c3.execute('INSERT INTO signals VALUES (?,?,?,?,?,?,?)',
               ('AAPL', datetime.now().isoformat(), 210.0, 200.0, 180.0,
                3.0, 31.5))
    src = lx.capture_entry_thesis(c3, 'POS-1', 'AAPL', 'LONG_CALL')
    row = c3.execute('SELECT source, bias_score, spot_price, sma_50, adx '
                     'FROM entry_thesis WHERE position_id = ?',
                     ('POS-1',)).fetchone()
    chk(src == 'signals' and row is not None,
        'a fresh scan row arms the position (source=' + str(src) + ')')
    chk(row is not None and row[1] == 3.0 and row[2] == 210.0 and row[3] == 200.0,
        'bias / spot / sma_50 are recorded from that row')
    chk(row is not None and row[4] == 31.5,
        'adx is carried across from the same scan row')

    # idempotent: a second call must not rewrite the entry read
    lx._CTX_CACHE.clear()
    c3.execute('UPDATE signals SET spot_price = 999.0, generated_at = ?',
               (datetime.now().isoformat(),))
    lx.capture_entry_thesis(c3, 'POS-1', 'AAPL', 'LONG_CALL')
    row2 = c3.execute('SELECT spot_price FROM entry_thesis '
                      'WHERE position_id = ?', ('POS-1',)).fetchone()
    chk(row2 is not None and row2[0] == 210.0,
        'a second call does not overwrite the original entry read')

    # fail unarmed: no context at all
    lx._CTX_CACHE.clear()
    c4 = fresh_db()
    src2 = lx.capture_entry_thesis(c4, 'POS-2', 'ZZZZ', 'LONG_CALL')
    n = c4.execute('SELECT COUNT(*) FROM entry_thesis').fetchone()[0]
    chk(src2 is None and n == 0,
        'no context writes NO row -- fails unarmed rather than guessing')

    # a stale scan row is not "today"
    lx._CTX_CACHE.clear()
    c5 = fresh_db()
    c5.execute('INSERT INTO signals VALUES (?,?,?,?,?,?,?)',
               ('MSFT', (now - timedelta(days=9)).isoformat(),
                400.0, 390.0, 370.0, 2.0, 28.0))
    src3 = lx.capture_entry_thesis(c5, 'POS-3', 'MSFT', 'LONG_CALL')
    n3 = c5.execute('SELECT COUNT(*) FROM entry_thesis').fetchone()[0]
    chk(src3 is None and n3 == 0,
        'a nine-day-old scan row does not arm a thesis')

    # not the LONG family
    lx._CTX_CACHE.clear()
    c6 = fresh_db()
    c6.execute('INSERT INTO signals VALUES (?,?,?,?,?,?,?)',
               ('KO', datetime.now().isoformat(), 60.0, 58.0, 55.0, 2.0, 30.0))
    src4 = lx.capture_entry_thesis(c6, 'POS-4', 'KO', 'CSP')
    n4 = c6.execute('SELECT COUNT(*) FROM entry_thesis').fetchone()[0]
    chk(src4 is None and n4 == 0,
        'a CSP is not armed -- only the family that reads the table')


# -- wiring: both open paths must arm ----------------------------------------

print('\nHELM-112  wiring (structural -- an AST walk, not a live open)')
src_path = os.path.join(ROOT, 'helm', 'cli', 'entry_snapshot.py')
tree = ast.parse(open(src_path, encoding='utf-8').read())
for fn in ('open_position_with_snapshot', 'open_multileg_with_snapshot'):
    node = next((n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == fn), None)
    called = False
    if node is not None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                name = getattr(f, 'attr', None) or getattr(f, 'id', None)
                if name == 'capture_entry_thesis':
                    called = True
    chk(called, fn + ' calls capture_entry_thesis')

# the arming call must sit OUTSIDE the transaction block, like the other
# best-effort stamps -- a journalling write must not be able to roll an open back
node = next((n for n in tree.body
             if isinstance(n, ast.FunctionDef)
             and n.name == 'open_position_with_snapshot'), None)
inside = False
if node is not None:
    for sub in ast.walk(node):
        if isinstance(sub, ast.With):
            for w in ast.walk(sub):
                if isinstance(w, ast.Call):
                    nm = getattr(w.func, 'attr', None) or getattr(w.func, 'id', None)
                    if nm == 'capture_entry_thesis':
                        inside = True
chk(not inside,
    'the arming call is outside the open transaction (cannot roll an open back)')

# -- HELM-121: attribution ---------------------------------------------------

print('\nHELM-121  origin_screen')
from dataclasses import fields as _fields
from helm.models.position import Position
chk('origin_screen' in {f.name for f in _fields(Position)},
    'Position carries an origin_screen field')

sig_src = open(src_path, encoding='utf-8').read()
chk('origin_screen' in sig_src.split('def open_multileg_with_snapshot')[0],
    'the single-leg open path accepts origin_screen')

schema = open(os.path.join(ROOT, 'helm', 'schema.sql'), encoding='utf-8').read()
chk('origin_screen' in schema, 'schema.sql declares origin_screen')

db = os.path.join(ROOT, 'data', 'helm.db')
live = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
cols = [r[1] for r in live.execute('PRAGMA table_info(positions)')]
chk('origin_screen' in cols, 'the live positions table has origin_screen')

# -- HELM-120: the paper booker's direction ----------------------------------

print('\nHELM-120  paper_open_one stamps direction')
import helm.cli._paper_open as po

CHAIN_ROW = {
    'ticker': 'AAPL', 'expiration': '2026-12-18', 'dte': 120, 'strike': 200.0,
    'opt_type': 'CALL', 'direction': 'SHORT',   # what fetch_chain_from_ibkr sends
    'bid': 20.0, 'ask': 21.0, 'mid': 20.5, 'spread': 1.0, 'spread_pct': 4.9,
    'delta': 0.75, 'theta': -0.05, 'iv': 28.0, 'oi': 4000, 'volume': 100,
    'source': 'ibkr',
}
BOOKED = {}


def fake_open(**kw):
    BOOKED.update(kw)
    return ('POS-X', 'LEG-X', 'SNAP-X')


_orig_eval = po.evaluate_contracts
_orig_open = po.open_position_with_snapshot
po.evaluate_contracts = lambda *a, **k: [dict(CHAIN_ROW)]
po.open_position_with_snapshot = fake_open
try:
    po.paper_open_one('AAPL', 'LONG_CALL', spot=210.0)
finally:
    po.evaluate_contracts = _orig_eval
    po.open_position_with_snapshot = _orig_open

got = (BOOKED.get('contract') or {}).get('direction')
chk(got == 'LONG',
    'a LONG_CALL books as LONG even when the chain row says SHORT (got '
    + str(got) + ')')
chk((BOOKED.get('fill_price') or 0) == 21.0,
    'the fill is still taken from the ask for a buyer (got '
    + str(BOOKED.get('fill_price')) + ')')

# a credit strategy must be untouched by the same line
BOOKED.clear()
row2 = dict(CHAIN_ROW)
row2['opt_type'] = 'PUT'
po.evaluate_contracts = lambda *a, **k: [dict(row2)]
po.open_position_with_snapshot = fake_open
try:
    po.paper_open_one('AAPL', 'CSP', spot=210.0)
finally:
    po.evaluate_contracts = _orig_eval
    po.open_position_with_snapshot = _orig_open
chk((BOOKED.get('contract') or {}).get('direction') == 'SHORT',
    'a CSP still books SHORT -- the sell side did not move')

# -- W13 data --------------------------------------------------------------

print('\nW13  the three mislabelled paper positions')
bad = live.execute(
    "SELECT COUNT(*) FROM positions p JOIN legs l ON l.position_id = p.id "
    "WHERE p.strategy IN ('LONG_CALL','LONG_PUT') AND l.direction = 'SHORT'"
).fetchone()[0]
chk(bad == 0, 'no long-family position has a SHORT leg (found ' + str(bad) + ')')
credit = live.execute(
    "SELECT COUNT(*) FROM positions WHERE strategy IN ('LONG_CALL','LONG_PUT') "
    "AND net_premium > 0").fetchone()[0]
chk(credit == 0, 'no long-family position carries a credit (found '
    + str(credit) + ')')
live.close()

print('\n' + str(OK) + ' ok, ' + str(FAIL) + ' failed')
sys.exit(0 if FAIL == 0 else 1)
