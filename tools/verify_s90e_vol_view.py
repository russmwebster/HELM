#!/usr/bin/env python3
"""Verify the realized-vol context on the open board (s90).

The vol_view half is behavioural. The PG half is structural, and says so.

    python3 tools/verify_s90e_vol_view.py
"""

import os
import re
import sys
import sqlite3
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PG = os.path.expanduser('~/Projects/helm-pg')

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


from helm import vol_view as V

VIEW = {'available': True, 'hv_30': 100.0, 'hv_90': 80.0,
        'hv_90_ex_earn': 90.0, 'hv_90_source': 'dates', 'hv_252': 70.0,
        'iv': 95.0, 'iv_rank': 82.0, 'iv_percentile': 84.0, 'source': 'scan',
        'age_days': 0}

print('vol_view -- the window is matched to the contract, and labelled')
chk(V.hv_for_dte(VIEW, 25) == (100.0, 'HV30'), 'a 25-day contract uses HV30')
chk(V.hv_for_dte(VIEW, 45) == (100.0, 'HV30'), 'the boundary belongs to HV30')
chk(V.hv_for_dte(VIEW, 46) == (90.0, 'HV90ex'),
    'past the boundary it uses the ex-earnings window')
plain = dict(VIEW, hv_90_source='plain')
chk(V.hv_for_dte(plain, 90) == (90.0, 'HV90'),
    'and says HV90, not HV90ex, when the trim was unavailable')
chk(V.hv_for_dte({'available': False}, 30) == (None, None),
    'an unavailable view yields no window rather than a default')

print('\nvol_view -- the ratio')
chk(V.iv_hv_ratio(95.0, VIEW, 25) == 0.95, 'IV 95 against HV30 100 is 0.95')
chk(V.iv_hv_ratio(95.0, VIEW, 90) == round(95.0 / 90.0, 3),
    'the same IV at 90 DTE is measured against the 90-day window')
chk(V.iv_hv_ratio(None, VIEW, 25) is None, 'no IV, no ratio')
chk(V.iv_hv_ratio(95.0, dict(VIEW, hv_30=0), 25) is None,
    'a zero HV does not divide')

print('\nvol_view -- the header says what it knows and what it does not')
line = V.header_line(VIEW, dte=25)
chk('yellow' in line and 'BELOW realized' in line,
    'under 1.00 is called out in yellow -- the case IVR cannot show')
rich = dict(VIEW, hv_30=80.0)
chk('green' in V.header_line(rich, dte=25), 'at or over 1.10 reads green')
mid = dict(VIEW, hv_30=92.0)
hl = V.header_line(mid, dte=25)
chk('green' not in hl and 'yellow' not in hl, 'in between carries no colour')
chk('unavailable' in V.header_line({'available': False}),
    'no data says so rather than rendering zeros')
chk('IVR 82/IVP 84' in line, 'IVR and IVP still shown -- this adds, it does not replace')

print('\nvol_view -- source selection is hybrid and honest')
c = sqlite3.connect(':memory:')
c.execute('CREATE TABLE signals (ticker TEXT, generated_at TEXT, spot_price REAL,'
          ' iv_current REAL, iv_rank REAL, iv_percentile REAL, hv_30 REAL,'
          ' hv_90 REAL, hv_90_ex_earn REAL, hv_90_source TEXT, hv_252 REAL)')
c.execute('INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?)',
          ('ZZ', datetime.now().isoformat(), 10.0, 30.0, 60.0, 65.0,
           25.0, 24.0, 23.0, 'dates', 22.0))
v = V.vol_view('ZZ', conn=c)
chk(v.get('source') == 'scan' and v.get('hv_30') == 25.0,
    'a fresh scan row is preferred -- the board agrees with the screen')
chk(V.vol_view('ZZ', conn=c, iv_hint=41.0).get('iv') == 41.0,
    'a caller-supplied IV wins, so the ratio does not need a scan row')

c2 = sqlite3.connect(':memory:')
c2.execute('CREATE TABLE signals (ticker TEXT, generated_at TEXT, spot_price REAL,'
           ' iv_current REAL, iv_rank REAL, iv_percentile REAL, hv_30 REAL,'
           ' hv_90 REAL, hv_90_ex_earn REAL, hv_90_source TEXT, hv_252 REAL)')
c2.execute('INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?)',
           ('ZZ', (datetime.now() - timedelta(days=9)).isoformat(), 10.0, 30.0,
            60.0, 65.0, 25.0, 24.0, 23.0, 'dates', 22.0))
row = V.from_signals(c2, 'ZZ')
chk(row['age_days'] >= 9, 'a nine-day-old row reports its age')


print('\nPG board  (structural -- the JS half cannot be exercised from here)')
es = open(os.path.join(PG, 'engine_store.py'), encoding='utf-8').read()
chk('def _attach_vol' in es, 'engine_store defines _attach_vol')
chk('_attach_vol(sym, res)' in es, 'and qualifying_contracts routes through it')
chk("mode=ro" in es,
    'the earnings-cache read is READ-ONLY -- PG keeps its guarantee (W34)')
chk(es.count('vol_view') >= 1, 'it uses the shared module, not a second copy')

html = open(os.path.join(PG, 'templates', 'open.html'), encoding='utf-8').read()
chk('function ivhv' in html, 'the template has an IV/HV renderer')
chk(html.count("['IV/HV', r=>ivhv(r)]") == 2,
    'the column is on BOTH families, single and spread (found '
    + str(html.count("['IV/HV', r=>ivhv(r)]")) + ')')
chk('id="volline"' in html, 'the header element exists')
chk('volline' in html.split('function renderTable')[1],
    'and renderTable populates it')

# W11's lesson, applied to a JS column spec: a header with no cell shifts every
# value in the row one place left, renders cleanly, and no grep can see it.
for fam in ('single', 'spread'):
    # split on the family's own closing bracket at its indent level --
    # splitting on '],' alone stops at the first ROW and makes this check
    # vacuously pass, which is precisely the failure mode it exists to catch
    block = html.split(fam + ': [')[1].split('\n  ],')[0]
    heads = re.findall(r"\['([^']+)'", block)
    cells = len(re.findall(r'r=>', block))
    chk(len(heads) == cells,
        fam + ' family: ' + str(len(heads)) + ' headers and ' + str(cells)
        + ' cells match')

print('\n' + str(OK) + ' ok, ' + str(FAIL) + ' failed')
sys.exit(0 if FAIL == 0 else 1)
