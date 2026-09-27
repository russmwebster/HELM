#!/usr/bin/env python3
"""Verify the long-call screen and its wiring into helm scan (HELM-101 step 4).

UPDATED FOR lc-screen-v2 (W195, 2026-09-27; Russ: no check may fail by
design). The gate half now pins v2: G1 recorded, not gated; the S&P-above-
200-day gate, failing closed; rank = 50% calmness + 50% cheapness with no
RSI or ADX term; the 0.72 bar. v1's G1 / 60-40 / RSI-penalty expectations
are gone with v1. The wiring half is unchanged. The fuller v2 suite (73
stored scans, routing, real flags) is _s123/verify_w195.py.

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
    """A name that clears every gate and the bar, before kw overrides it:
    HV252 18 (calmness 1.0), IV/HV90 0.72 (cheapness 0.9) -> score 0.95."""
    base = dict(ticker='TEST', bias_score=3.0, spot_price=100.0, sma_50=95.0,
                sma_200=90.0, rsi_14=55.0, adx=30.0, iv_hv90_ratio=0.72,
                hv_252=18.0, days_to_earnings=45, strategy='CSP')
    base.update(kw)
    return base


UP = {'above': True, 'spx': 6600.0, 'sma200': 6000.0}   # S&P above its 200-day


def scr(rows, market=UP):
    return S.screen(rows, market=market)


print('lc_screen -- gates (v2)')
r = row()
scr([r])
chk(r['lc_screen_pass'] == 1 and r['lc_screen_reject'] is None,
    'a clean name passes every gate')
chk(json.loads(r['lc_gates_json'])['version'] == 'lc-screen-v2 (W195)',
    'the record says lc-screen-v2')

r = row(bias_score=1.0, spot_price=80.0)
scr([r])
g1 = json.loads(r['lc_gates_json'])['g1']
chk(r['lc_screen_pass'] == 1 and g1['gate'] is False and g1['ok'] is False
    and g1['bias'] == 1.0,
    'G1 is recorded, not gated: weak bias and a broken stack still pass')

for label, mk, want in (('S&P below its 200-day', {'above': False}, 'S&P below 200d'),
                        ('S&P unread', {'above': None, 'error': 'x'}, 'S&P unknown'),
                        ('no S&P passed at all', None, 'S&P unknown')):
    r = row()
    scr([r], market=mk)
    chk(r['lc_screen_pass'] == 0 and r['lc_screen_reject'] == want,
        'rejects on ' + label + ' -- fails closed  (got ' + repr(r['lc_screen_reject']) + ')')

r = row(hv_252=30.0, iv_hv90_ratio=0.80)          # 0.5 x 0.5 + 0.5 x 0.5 = 0.50
scr([r])
chk(r['lc_rank_score'] == 0.5 and r['lc_screen_reject'] == 'below bar',
    'a name clearing every gate but scoring under 0.72 is rejected "below bar"')
chk(S.RANK_BAR == 0.72, 'the bar is 0.72 (provisional, review ~2026-10-27)')

cases = [
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
    scr([r])
    got = r['lc_screen_reject'] or ''
    chk(want in got and r['lc_screen_pass'] == 0,
        'rejects on ' + want + '  (got ' + repr(got) + ')')

# the three fail-closed cases are the ones worth stating separately: an
# unmeasured gate must refuse, not wave through
chk(all(scr([row(**o)]) == [] for _, o in cases if 'unknown' in _),
    'an unmeasurable gate refuses rather than passes')

# ADX and RSI neither gate nor score in v2 (W195); both are still recorded.
r = row(adx=4.0)
scr([r])
r2 = row(adx=40.0)
scr([r2])
chk(r['lc_screen_pass'] == 1 and r['lc_rank_score'] == r2['lc_rank_score'],
    'ADX neither excludes a name nor moves its score')
r3 = row(rsi_14=95.0)
scr([r3])
comp = json.loads(r3['lc_gates_json'])['components']
chk(r3['lc_screen_pass'] == 1 and r3['lc_rank_score'] == r2['lc_rank_score']
    and comp['rsi_penalty'] > 0,
    'an extended RSI costs nothing in v2 -- its penalty is recorded, not scored')

print('\nlc_screen -- ranking and records')
board = [row(ticker='AAA', iv_hv90_ratio=0.72),     # 0.95
         row(ticker='BBB', iv_hv90_ratio=0.78),     # 0.80
         row(ticker='CCC', iv_hv90_ratio=0.85)]     # 0.625 -> below bar
surv = scr(board)
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
scr(small)
gs = json.loads(small[0]['lc_gates_json'])['g5']
chk(gs['alt_quintile'] is None and gs['alt_ok'] is None
    and gs['alt_agrees'] is None,
    'too small a board logs the quintile as unknown, not as disagreement')

big = [row(ticker='T%d' % i, hv_252=10.0 + i * 4) for i in range(10)]
scr(big)
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
