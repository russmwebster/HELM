#!/usr/bin/env python3
"""Verify the long-call screen and its wiring into helm scan (HELM-101 step 4).

Two halves, and they are different kinds of evidence:

  * The lc_screen unit checks exercise new code. They pass as soon as the
    module exists, so they are NOT a differential -- they are there to pin the
    gate semantics, especially the fail-closed ones.
  * The wiring checks ARE the differential: they fail against the tree before
    apply_s90b ran.

    python3 tools/verify_s90b_lc_screen.py
"""

import os
import sys
import ast
import json

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


from helm import lc_screen as S


def row(**kw):
    """A name that clears every gate, before kw overrides it."""
    base = dict(ticker='TEST', bias_score=3.0, spot_price=100.0, sma_50=95.0,
                sma_200=90.0, rsi_14=55.0, adx=30.0, iv_hv90_ratio=0.80,
                hv_252=25.0, days_to_earnings=45, strategy='CSP')
    base.update(kw)
    return base


print('lc_screen -- gates')
r = row()
S.screen([r])
chk(r['lc_screen_pass'] == 1 and r['lc_screen_reject'] is None,
    'a clean name passes every gate')

cases = [
    ('G1 bias', dict(bias_score=1.0)),
    ('G1 stack', dict(spot_price=80.0)),
    ('G3 vol', dict(iv_hv90_ratio=0.95)),
    ('G3 vol unknown', dict(iv_hv90_ratio=None)),
    ('G4 earnings unknown', dict(days_to_earnings=None)),
    ('G4 earnings stale', dict(days_to_earnings=-3)),
    ('G4 earnings ramp', dict(days_to_earnings=3)),
    ('G5 vol ceiling', dict(hv_252=45.0)),
    ('G5 hv252 unknown', dict(hv_252=None)),
]
for want, over in cases:
    r = row(**over)
    S.screen([r])
    got = r['lc_screen_reject'] or ''
    chk(want in got and r['lc_screen_pass'] == 0,
        'rejects on ' + want + '  (got ' + repr(got) + ')')

# the three fail-closed cases are the ones worth stating separately: an
# unmeasured gate must refuse, not wave through
chk(all(S.screen([row(**o)]) == [] for _, o in cases if 'unknown' in _),
    'an unmeasurable gate refuses rather than passes')

# ADX must not gate -- it is a rank input only (design doc 7.3)
r = row(adx=4.0)
S.screen([r])
chk(r['lc_screen_pass'] == 1, 'a very low ADX does not exclude a name')
r2 = row(adx=40.0)
S.screen([r2])
chk((r2['lc_rank_score'] or 0) > (r['lc_rank_score'] or 0),
    'but a higher ADX ranks above a lower one')

# RSI is a penalty, never a gate
r = row(rsi_14=95.0)
S.screen([r])
chk(r['lc_screen_pass'] == 1, 'an extended RSI does not exclude a name')
chk(S.rsi_penalty(95.0) > 0 and S.rsi_penalty(55.0) == 0.0,
    'but it costs rank score')

print('\nlc_screen -- ranking and records')
board = [row(ticker='AAA', iv_hv90_ratio=0.72),
         row(ticker='BBB', iv_hv90_ratio=0.88),
         row(ticker='CCC', bias_score=1.0)]
surv = S.screen(board)
chk([r['ticker'] for r in surv] == ['AAA', 'BBB'],
    'cheaper vol ranks first (got ' + str([r['ticker'] for r in surv]) + ')')
chk([r['lc_screen_rank'] for r in surv] == [1, 2], 'ranks are 1-based and dense')
chk(board[2]['lc_screen_rank'] is None, 'a rejected name carries no rank')
chk(all(isinstance(json.loads(r['lc_gates_json']), dict) for r in board),
    'every row carries a parseable gates record, passing or not')
g = json.loads(board[0]['lc_gates_json'])
chk('alt_quintile' in g['g5'] and 'alt_ok' in g['g5'],
    'the unacted quintile alternative is logged on every row')
chk(g['g5']['max'] == 40.0, 'the acted ceiling is the absolute one')

# an alternative that could not be measured must read as unknown, not as
# "the alternative disagreed" -- a board of four has no quintile
small = [row(ticker='AAA'), row(ticker='BBB')]
S.screen(small)
gs = json.loads(small[0]['lc_gates_json'])['g5']
chk(gs['alt_quintile'] is None and gs['alt_ok'] is None
    and gs['alt_agrees'] is None,
    'too small a board logs the quintile as unknown, not as disagreement')

big = [row(ticker='T%d' % i, hv_252=10.0 + i * 4) for i in range(10)]
S.screen(big)
gb = json.loads(big[0]['lc_gates_json'])['g5']
chk(gb['alt_quintile'] is not None and gb['alt_ok'] is True,
    'a full board does compute the quintile alternative')

# non-routing is the whole safety property of this ship
chk(all(r.get('strategy') == 'CSP' for r in board),
    'the screen never rewrites strategy -- it routes nothing')


print('\nwiring -- helm scan  (this half is the differential)')
scan_src = open(os.path.join(ROOT, 'helm', 'cli', 'scan_cmd.py'),
                encoding='utf-8').read()
tree = ast.parse(scan_src)

names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
chk('_print_lc_screen' in names, 'scan_cmd defines _print_lc_screen')

run = next((n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == 'run'), None)


def call_lines(node, needle):
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            nm = getattr(sub.func, 'attr', None) or getattr(sub.func, 'id', None)
            if nm == needle:
                out.append(sub.lineno)
        if isinstance(sub, (ast.Import, ast.ImportFrom)):
            for a in sub.names:
                if a.name == needle or a.asname == needle:
                    out.append(sub.lineno)
    return sorted(out)


screen_at = call_lines(run, 'screen') if run else []
attach_at = call_lines(run, 'attach_days_to_earnings') if run else []
persist_at = call_lines(run, 'persist_scan_signals') if run else []
print_at = call_lines(run, '_print_lc_screen') if run else []

chk(bool(screen_at), 'run() calls lc_screen.screen')
chk(bool(attach_at), 'run() attaches days_to_earnings before screening')
chk(bool(print_at), 'run() prints the screen block')
chk(bool(screen_at) and bool(persist_at) and min(screen_at) < min(persist_at),
    'the screen runs BEFORE persistence, so its verdict is stored')
chk(bool(attach_at) and bool(screen_at) and min(attach_at) < min(screen_at),
    'G4 has its input before the gate reads it')

# the lc_* fields have to survive the persistence layer
from helm.cli import _decision_capture as DC
for f in ('lc_screen_pass', 'lc_screen_rank', 'lc_screen_reject',
          'lc_rank_score', 'lc_gates_json'):
    chk(f in DC._PASSTHROUGH, 'signals persistence carries ' + f)
chk(hasattr(DC, 'attach_days_to_earnings'),
    '_decision_capture exposes attach_days_to_earnings')

# one source for days_to_earnings: persist must prefer what the screen saw
dc_src = open(os.path.join(ROOT, 'helm', 'cli', '_decision_capture.py'),
              encoding='utf-8').read()
chk('res.get("days_to_earnings")' in dc_src,
    'persist prefers the attached value rather than recomputing blind')

# and the screen must not have been wired into the SELL side by accident
chk('lc_screen' not in scan_src.split('def bias_to_strategy')[1]
    .split('def score_label')[0],
    'bias_to_strategy is untouched -- the sell side did not move')

print('\n' + str(OK) + ' ok, ' + str(FAIL) + ' failed')
sys.exit(0 if FAIL == 0 else 1)
