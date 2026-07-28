"""HELM-134 / W78 -- HELM READ vol clauses. Behavioural checks.

Every case is drawn from the 2026-07-28 board so a failure names a real row.
"""
import os, sys
sys.path.insert(0, os.path.expanduser('~/Projects/helm'))
from helm import vol_read as vr

FAIL = []
def ck(name, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond: FAIL.append(name)

def joined(row):
    return ' ; '.join(vr.vol_read(row))

# --- the four rows that motivated this -------------------------------------
KO = dict(strategy='CSP', iv_rank=85.0, iv_percentile=92.4, iv_current=22.0,
          hv_30=31.4, vrp=-9.4, days_to_earnings=0, hv_30_source='dates')
AAPL = dict(strategy='CSP', iv_rank=85.5, iv_percentile=95.6, iv_current=30.2,
            hv_30=33.0, vrp=-2.8, days_to_earnings=2, hv_30_source='dates')

print('== the contradiction is gone ==')
for nm, row in (('KO', KO), ('AAPL', AAPL)):
    t = joined(row)
    ck(nm + ': never says "good premium"', 'good premium' not in t, t)
    ck(nm + ': no tick on the vol clause', (chr(0x2713) + ' IVR') not in t, t)
    ck(nm + ': states selling below realized', 'BELOW realized' in t, t)
ck('KO says earnings TODAY', 'earnings TODAY' in joined(KO), joined(KO))
ck('AAPL says the rank is the print', 'the rank is the print' in joined(AAPL), joined(AAPL))
ck('AAPL names the days', 'earnings in 2d' in joined(AAPL), joined(AAPL))

print()
print('== cause only fires when it is actually the cause ==')
far = dict(KO); far['days_to_earnings'] = 60
ck('no earnings clause when the print is far off', 'the rank is the print' not in joined(far), joined(far))
lowivr = dict(KO); lowivr['iv_rank'] = 20.0
ck('no earnings clause when the rank is not elevated', 'the rank is the print' not in joined(lowivr), joined(lowivr))

print()
print('== agreement still earns a tick ==')
good = dict(strategy='CSP', iv_rank=60.0, iv_percentile=62.0, iv_current=40.0,
            hv_30=30.0, vrp=10.0, days_to_earnings=60, hv_30_source='dates')
t = joined(good)
ck('tick when both tests agree', t.startswith(chr(0x2713) + ' IVR'), t)
ck('quotes both numbers', 'IV 40' in t and 'HV 30' in t, t)
ck('quotes the VRP', 'VRP +10.0' in t, t)

print()
print('== divergence between rank and percentile ==')
gs = dict(strategy='IRON_CONDOR', iv_rank=54.7, iv_percentile=84.1, iv_current=35.3,
          hv_30=39.9, vrp=-4.6, days_to_earnings=40, hv_30_source='dates')
ck('GS flags the 20+ point gap', 'rank and percentile disagree' in joined(gs), joined(gs))
tight = dict(gs); tight['iv_percentile'] = 58.0
ck('no flag when they agree', 'rank and percentile disagree' not in joined(tight), joined(tight))

print()
print('== confidence ==')
nod = dict(good); nod['hv_30_source'] = 'dates-none'
ck('flags weak HV provenance', 'no earnings dates' in joined(nod), joined(nod))
ck('silent when provenance is good', 'no earnings dates' not in joined(good), joined(good))

print()
print('== degrades rather than lying ==')
novrp = dict(strategy='CSP', iv_rank=70.0, iv_percentile=72.0, hv_30_source='dates')
t = joined(novrp)
ck('no VRP -> says so, claims no richness', 'no VRP available' in t, t)
ck('no VRP -> still no "good premium"', 'good premium' not in t, t)
ck('empty row returns no clauses', vr.vol_read({}) == [], vr.vol_read({}))

print()
print('== buy side is handled too ==')
lc = dict(strategy='LONG_CALL', iv_rank=20.0, iv_percentile=22.0, iv_current=25.0,
          hv_30=30.0, vrp=-5.0, days_to_earnings=60, hv_30_source='dates')
ck('long call below realized reads as favourable', 'buying below realized' in joined(lc), joined(lc))
lc2 = dict(lc); lc2['vrp'] = 5.0; lc2['iv_current'] = 35.0
ck('long call above realized warns', 'paying ABOVE realized' in joined(lc2), joined(lc2))

print()
print('== annotate() prepends and preserves ==')
rows = [dict(KO, bias_factors=['Price > SMA50 -- bullish stack'])]
n = vr.annotate(rows)
ck('annotate reports one row changed', n == 1, n)
ck('vol clause leads', 'earnings TODAY' in rows[0]['bias_factors'][0], rows[0]['bias_factors'][0])
ck('bias reasoning preserved', 'bullish stack' in rows[0]['bias_factors'][-1], rows[0]['bias_factors'][-1])
err = [{'error': 'boom', 'bias_factors': ['x']}]
vr.annotate(err)
ck('error rows untouched', err[0]['bias_factors'] == ['x'], err[0])

print()
print('== gated rows keep their clauses (HELM-136 follow-up) ==')
gk = dict(strategy='NO_SELL_EARNINGS', strategy_shadow='CSP', iv_rank=85.0,
          iv_percentile=92.4, iv_current=22.0, hv_30=29.0, vrp=-6.5,
          days_to_earnings=0, hv_30_source='dates')
t = ' ; '.join(vr.vol_read(gk))
ck('gated row reads through the shadow', 'earnings TODAY' in t, t)
ck('gated row keeps the VRP clause', 'BELOW realized' in t, t)
gn = dict(gk); gn.pop('strategy_shadow')
ck('gated row with NO shadow yields no sell clauses', 'realized' not in ' '.join(vr.vol_read(gn)))

print()
print('== module stays DB-free ==')
srctxt = open(os.path.expanduser('~/Projects/helm/helm/vol_read.py'), encoding='utf-8').read()
ck('no sqlite3 import', 'sqlite3' not in srctxt)
ck('no get_conn', 'get_conn' not in srctxt)

print()
print('ALL PASS' if not FAIL else 'FAILURES: ' + repr(FAIL))
