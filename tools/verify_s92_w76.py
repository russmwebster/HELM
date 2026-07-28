import os, sys, re, sqlite3, datetime, statistics as st
R = os.path.expanduser('~/Projects/helm')
sys.path.insert(0, R)
FAIL = []
def ck(name, cond, detail=''):
    if callable(cond):
        try:
            cond = cond()
        except Exception as _e:
            detail = type(_e).__name__ + ': ' + str(_e)[:90]
            cond = False
    print(('  ok   ' if cond else '  FAIL ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond: FAIL.append(name)

print('== A. the gate denominator is raw HV90 (HELM-132) ==')
src = open(os.path.join(R,'helm/cli/scan_cmd.py'), encoding='utf-8').read()
m = re.search(r'result\["iv_hv90_ratio"\]\s*=\s*round\(float\(_iv\)\s*/\s*float\((\w+)\)', src)
ck('writer assigns iv_hv90_ratio from a named denominator', m is not None)
if m:
    ck('gate denominator is _hv90 (raw), not _hv90x', m.group(1) == '_hv90', m.group(1))
m2 = re.search(r'_hv90\s*=\s*result\.get\("(\w+)"\)', src)
ck('_hv90 is sourced from the raw hv_90 column', m2 is not None and m2.group(1) == 'hv_90', m2.group(1) if m2 else None)
m3 = re.search(r'result\["iv_hv90_ratio_xearn"\]\s*=\s*round\(float\(_iv\)\s*/\s*float\((\w+)\)', src)
ck('the ex-earnings twin is still computed', m3 is not None and m3.group(1) == '_hv90x', m3.group(1) if m3 else None)

print('== B. the twin is logged, never gated ==')
lc = open(os.path.join(R,'helm/lc_screen.py'), encoding='utf-8').read()
gate_area = lc[lc.index('def evaluate_gates'):lc.index('def rank_score')]
ck('no gate reads iv_hv90_ratio_xearn as a pass/fail input',
   not re.search(r'g3_ok\s*=.*xearn', gate_area))
ck('g3_ok still derives only from the raw ratio',
   'g3_ok = ratio is not None and ratio <= G3_RATIO_MAX' in gate_area)
ck('lc_screen stays DB-free', 'sqlite3' not in lc and 'get_conn' not in lc)

print('== C. earn_flag behaviour (HELM-133) ==')
import importlib
lcm = importlib.import_module('helm.lc_screen')
importlib.reload(lcm)
ck('flag absent when there is no earnings date',
   lambda: lcm.earn_flag({'iv_hv90_ratio_xearn': 1.0}, 0.9) is None)
ck('flag absent when the print has aged out of the 90d window',
   lambda: lcm.earn_flag({'earn_days_since': 120, 'earn_in_hv90_window': 0,
                  'iv_hv90_ratio_xearn': 1.0}, 0.9) is None)
f = (lambda: lcm.earn_flag({'earn_days_since': 11, 'earn_in_hv90_window': 1,
                   'iv_hv90_ratio_xearn': 0.899}, 0.859))
try:
    f = f()
except Exception as _e:
    f = None
ck('flag fires inside the window and reports the divergence', f is not None, f)
ck('flag states days since print', f is not None and '11d' in f, f)
ck('flag states the signed divergence', f is not None and '+0.040' in f, f)
ck('flag degrades gracefully with no twin',
   lambda: lcm.earn_flag({'earn_days_since': 5, 'earn_in_hv90_window': 1}, 0.9) == 'reported 5d ago')

print('== D. the verdict surfaces it ==')
row = {'bias_score': 3, 'spot': 100, 'sma_50': 90, 'sma_200': 80, 'rsi_14': 55,
       'adx': 30, 'iv_hv90_ratio': 0.859, 'hv_90': 30.0, 'hv_90_ex_earn': 28.7,
       'hv_252': 25.0, 'days_to_earnings': 60, 'iv_hv90_ratio_xearn': 0.899,
       'earn_days_since': 11, 'earn_in_hv90_window': 1}
try:
    g, _fails = lcm.evaluate_gates(row)
except Exception as _e:
    g, _fails = {'g3': {}}, []
g3 = g['g3']
ck('g3 exposes the raw denominator it now divides by', g3.get('hv_90') == 30.0, g3)
ck('g3 carries the informational flag', g3.get('earn_flag') is not None, g3.get('earn_flag'))
ck('g3 carries the ex-earnings twin', g3.get('ratio_xearn') == 0.899, g3.get('ratio_xearn'))
ck('g3 still passes on the raw ratio', g3.get('ok') is True, g3)
ck('G3 is not among the failures', not any('G3' in f for f in _fails), _fails)

print('== E. the columns have a writer (the W54 guard) ==')
from helm.cli import _decision_capture as dc
for col in ['iv_hv90_ratio_xearn','earn_days_since','earn_in_hv90_window']:
    ck('persisted: ' + col, col in dc._PASSTHROUGH)
from helm.models.signal import Signal
import dataclasses
fields = {f.name for f in dataclasses.fields(Signal)}
for col in ['iv_hv90_ratio_xearn','earn_days_since','earn_in_hv90_window']:
    ck('on the dataclass: ' + col, col in fields)
c = sqlite3.connect(os.path.join(R,'data','helm.db'))
cols = {r[1] for r in c.execute('PRAGMA table_info(signals)')}
for col in ['iv_hv90_ratio_xearn','earn_days_since','earn_in_hv90_window']:
    ck('live column exists: ' + col, col in cols)

print('== F. the new denominator reproduces the decision numbers ==')
best = {}
for tk, iv, hv, hvx, tsx in c.execute("select ticker, iv_current, hv_90, hv_90_ex_earn, created_at from signals where date(created_at)='2026-07-27' and iv_current is not null and hv_90>0 and hv_90_ex_earn>0"):
    if tk not in best or tsx > best[tk][3]: best[tk] = (iv, hv, hvx, tsx)
passes = [t for t,(iv,hv,hvx,_) in best.items() if iv/hv <= 0.90]
ck('raw form passes 10 of 67 on the 2026-07-27 board', len(best)==67 and len(passes)==10, '%d of %d' % (len(passes), len(best)))
ge = best.get('GE')
ck('GE reads 0.859 raw', ge is not None and round(ge[0]/ge[1],3)==0.859, round(ge[0]/ge[1],3) if ge else None)
ck('GE clears the 0.89 routing margin', ge is not None and round(ge[0]/ge[1],3) <= 0.90 - lcm.ROUTE_MARGIN)
ck('GE would NOT have cleared it under the old mixed form', ge is not None and round(ge[0]/ge[2],3) > 0.90 - lcm.ROUTE_MARGIN, round(ge[0]/ge[2],3) if ge else None)
c.close()

print()
print(('ALL PASS' if not FAIL else 'FAILURES: ' + repr(FAIL)))