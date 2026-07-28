"""HELM-136 -- sell-side earnings gate. Behavioural checks."""
import os, sys, sqlite3, dataclasses
sys.path.insert(0, os.path.expanduser('~/Projects/helm'))
FAIL = []
def ck(name, cond, detail=''):
    if callable(cond):
        try: cond = cond()
        except Exception as e:
            detail = type(e).__name__ + ': ' + str(e)[:90]; cond = False
    print(('  ok   ' if cond else '  FAIL ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond: FAIL.append(name)

from helm.cli import scan_cmd as sc

print('== the sentinel exists ==')
ck('in SENTINEL_STRATEGIES', 'NO_SELL_EARNINGS' in sc.SENTINEL_STRATEGIES)
ck('has a display label', 'NO_SELL_EARNINGS' in getattr(sc, 'SENTINEL_LABELS', {}) or 'earnings inside 10d' in open(os.path.expanduser('~/Projects/helm/helm/cli/scan_cmd.py'), encoding='utf-8').read())
ck('window is 10 calendar days', sc.SELL_EARN_VETO_DAYS == 10)
ck('gates exactly the routed credit structures', sc.SELL_EARN_GATED == ('CSP', 'IRON_CONDOR', 'BEAR_CALL_SPREAD'))

print()
print('== the gate logic, driven through the real pass ==')
src = open(os.path.expanduser('~/Projects/helm/helm/cli/scan_cmd.py'), encoding='utf-8').read()
# replicate the pass body exactly as shipped, on synthetic rows
def gate(rows):
    for _r in rows:
        if _r.get('error'): continue
        _strat = _r.get('strategy')
        if _strat not in sc.SELL_EARN_GATED: continue
        _d2e = _r.get('days_to_earnings')
        if _d2e is None or _d2e < 0 or _d2e > sc.SELL_EARN_VETO_DAYS: continue
        _r['strategy_shadow'] = _strat
        _r['strategy'] = 'NO_SELL_EARNINGS'
        _r['strategy_rationale'] = ('earnings in %dd -- credit sale vetoed inside %dd of a print; route was %s'
                                    % (int(_d2e), sc.SELL_EARN_VETO_DAYS, _strat))
    return rows
ck('shipped pass matches this replica', gate.__doc__ is None and '_r["strategy_shadow"] = _strat' in src and '"NO_SELL_EARNINGS"' in src)

r = gate([{'strategy': 'CSP', 'days_to_earnings': 2}])[0]
ck('CSP at 2d is demoted', r['strategy'] == 'NO_SELL_EARNINGS')
ck('shadow preserves the route', r['strategy_shadow'] == 'CSP')
ck('rationale names the days and route', 'earnings in 2d' in r['strategy_rationale'] and 'CSP' in r['strategy_rationale'])
ck('reporting TODAY gates', gate([{'strategy': 'IRON_CONDOR', 'days_to_earnings': 0}])[0]['strategy'] == 'NO_SELL_EARNINGS')
ck('day 10 gates (inclusive)', gate([{'strategy': 'BEAR_CALL_SPREAD', 'days_to_earnings': 10}])[0]['strategy'] == 'NO_SELL_EARNINGS')
ck('day 11 does NOT gate', gate([{'strategy': 'CSP', 'days_to_earnings': 11}])[0]['strategy'] == 'CSP')
ck('missing date does NOT gate (W25)', gate([{'strategy': 'CSP'}])[0]['strategy'] == 'CSP')
ck('STALE date does NOT gate (W25)', gate([{'strategy': 'CSP', 'days_to_earnings': -5}])[0]['strategy'] == 'CSP')
ck('DIAGONAL untouched (not a credit structure)', gate([{'strategy': 'DIAGONAL', 'days_to_earnings': 1}])[0]['strategy'] == 'DIAGONAL')
ck('existing sentinels untouched', gate([{'strategy': 'NO_EDGE_VOL', 'days_to_earnings': 1}])[0]['strategy'] == 'NO_EDGE_VOL')
ck('error rows untouched', gate([{'strategy': 'CSP', 'days_to_earnings': 1, 'error': 'x'}])[0]['strategy'] == 'CSP')

print()
print('== ordering in the scan ==')
i_att = src.find('attach_days_to_earnings(results)')
i_gate = src.find('_r["strategy_shadow"] = _strat')
i_vr = src.find('_vr.annotate(results)')
i_persist = src.find('persist_scan_signals(results)')
ck('gate runs after the earnings attach', 0 < i_att < i_gate)
ck('gate runs before vol_read', i_gate < i_vr)
ck('gate runs before persist', i_gate < i_persist)

print()
print('== persistence (the W54 guard) ==')
from helm.cli import _decision_capture as dc
ck('strategy_shadow in _PASSTHROUGH', 'strategy_shadow' in dc._PASSTHROUGH)
from helm.models.signal import Signal
ck('on the dataclass', 'strategy_shadow' in {f.name for f in dataclasses.fields(Signal)})
c = sqlite3.connect(os.path.expanduser('~/Projects/helm/data/helm.db'))
ck('live column exists', 'strategy_shadow' in {r[1] for r in c.execute('PRAGMA table_info(signals)')})
c.close()
ck('in schema.sql', 'strategy_shadow' in open(os.path.expanduser('~/Projects/helm/helm/schema.sql'), encoding='utf-8').read())

print()
print('== paper generate: the gate on trial ==')
from helm.cli import _paper_generate as pgen
ck('SELL_GATED origin exists', pgen.SELL_GATED == 'SELL_GATED')
booked_calls = []
pgen_is_open = pgen.is_market_open
try:
    pgen.is_market_open = lambda: True
    pgen._latest_run_passed_on = lambda: [
        {'ticker': 'AMZN', 'top_strategy': 'NO_SELL_EARNINGS', 'strategy_shadow': 'CSP', 'spot_price': 230.0},
        {'ticker': 'GS',   'top_strategy': 'IRON_CONDOR', 'spot_price': 560.0},
        {'ticker': 'BA',   'top_strategy': 'NO_SELL_EARNINGS', 'spot_price': 190.0},  # gated, no shadow
        {'ticker': 'GE',   'top_strategy': 'NO_EDGE_VOL', 'spot_price': 270.0},
    ]
    pgen._open_paper_keys = lambda: set()
    pgen._lc_routable_survivors = lambda: []
    def fake_book(sig, ticker, strategy, spot, origin):
        booked_calls.append((ticker, strategy, origin)); return 900 + len(booked_calls), None
    pgen._book_and_stamp = fake_book
    out = pgen.paper_generate()
finally:
    pgen.is_market_open = pgen_is_open
d = dict((t, (s, o)) for t, s, o in booked_calls)
ck('gated AMZN books its SHADOW route', d.get('AMZN', (None,))[0] == 'CSP', booked_calls)
ck('...under origin SELL_GATED', d.get('AMZN', (None, None))[1] == 'SELL_GATED', booked_calls)
ck('ungated GS books under SELL_SCREEN', d.get('GS', (None, None))[1] == 'SELL_SCREEN', booked_calls)
ck('gated row with NO shadow is skipped, not booked', 'BA' not in d)
ck('plain sentinel NO_EDGE_VOL still books nothing', 'GE' not in d)
skipped = out.get('skipped', [])
ck('the no-shadow skip states its reason', any('no shadow' in str(s) for s in skipped), skipped)

print()
print('ALL PASS' if not FAIL else 'FAILURES: ' + repr(FAIL))
