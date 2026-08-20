#!/usr/bin/env python3
"""tools/exposure_report.py - s108, 2026-08-20.  READ-ONLY.

The reader for W136's watchlist.exposure_group.  It exists so the column is not
another W107 - a field HELM stores, renders nowhere and reads never.  That item
has been open since s102 for exactly this reason.

    python3 tools/exposure_report.py                # the real book
    python3 tools/exposure_report.py --book PAPER
    python3 tools/exposure_report.py --book ALL
    python3 tools/exposure_report.py --json

WHAT IT ANSWERS.  "Given what I already own, how much of the book is one bet?"
No gate in HELM asks this - that is W135 - and watchlist.sector cannot answer it,
which is W136: 19 of the 84 names are one trade and GICS calls them two
different things, while calling two different things Industrials.

COMMITTED CAPITAL, derived per position from legs
    short leg with a protecting long of the same type : wider wing x 100 x qty
    short put with no long put (cash-secured)         : strike    x 100 x qty
    short call with no long call (covered)            : 0, the shares secure it
    long legs only (debit)                            : premium paid
An iron condor takes the WIDER SIDE only, not both, because both sides cannot
finish in the money.

THREE NUMBERS THAT ARE NOT THE SAME, and s108 got caught by the difference.
For the 20-lot LRCX condor:
    committed capital (this tool)  $20,000
    max loss                       $10,440
    assignment obligation         $720,000
This tool reports the first.  It is the one that answers "how concentrated am
I", and it is NOT the one that answers "what can this cost me".

BLIND SPOTS, stated so nobody assumes otherwise
  * It reports what HELM BELIEVES.  A position assigned but not recorded is
    overstated here.  At s108 that is true of LRCX - 13 of 20 short puts were
    assigned 2026-08-19 and HELM has not been told.
  * Diagonals get the vertical treatment; a calendar's real requirement differs.
  * It measures concentration WITHIN a group, and says nothing about correlation
    BETWEEN groups.  In a selloff those converge - see the deployment review.
"""
import argparse, json, os, sqlite3
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, os.pardir, 'data', 'helm.db')


def committed(legs):
    by = defaultdict(list)
    for L in legs:
        by[(L['option_type'] or '').upper()].append(L)
    spreads, other, kinds = [], 0.0, set()
    for typ, ls in by.items():
        sh = [x for x in ls if (x['direction'] or '').upper() == 'SHORT']
        lo = [x for x in ls if (x['direction'] or '').upper() == 'LONG']
        if sh and lo:
            w = max(abs((s['strike'] or 0) - (l['strike'] or 0)) for s in sh for l in lo)
            q = max(abs(s['contracts'] or 0) for s in sh)
            spreads.append(w * 100 * q)
            kinds.add('vertical')
        elif sh:
            for s in sh:
                if typ == 'PUT':
                    other += (s['strike'] or 0) * 100 * abs(s['contracts'] or 0)
                    kinds.add('cash-secured put')
                else:
                    kinds.add('covered call')
        elif lo:
            for l in lo:
                other += ((l['open_price'] or 0) * (l['multiplier'] or 100)
                          * abs(l['contracts'] or 0))
            kinds.add('debit')
    req = (max(spreads) if len(spreads) > 1 else sum(spreads)) + other
    return req, '+'.join(sorted(kinds)) or 'unknown'


def build(db, book, cash_only=False):
    con = sqlite3.connect('file:' + os.path.abspath(db) + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    q = ("select p.id, p.ticker, p.strategy, p.book, "
         "coalesce(w.exposure_group,'(ungrouped)') g "
         "from positions p left join watchlist w on w.ticker = p.ticker "
         "where p.status like 'OPEN'")
    if book != 'ALL':
        q += " and p.book like '" + book + "'"
    pos = [dict(r) for r in con.execute(q)]
    dropped = []
    for p in pos:
        legs = [dict(r) for r in con.execute(
            'select * from legs where position_id = ?', (p['id'],))]
        p['committed'], p['kind'] = committed(legs)
    if cash_only:
        dropped = [x for x in pos if x['kind'] == 'debit']
        pos = [x for x in pos if x['kind'] != 'debit']
    names = defaultdict(list)
    for r in con.execute("select ticker, exposure_group from watchlist where coalesce(active,1) = 1"):
        names[r['exposure_group'] or '(ungrouped)'].append(r['ticker'])
    con.close()
    agg = defaultdict(lambda: {'n': 0, 'committed': 0.0, 'tickers': set()})
    for p in pos:
        a = agg[p['g']]
        a['n'] += 1
        a['committed'] += p['committed']
        a['tickers'].add(p['ticker'])
    total = sum(a['committed'] for a in agg.values()) or 1.0
    rows = []
    for g in sorted(agg, key=lambda k: -agg[k]['committed']):
        a = agg[g]
        rows.append({'group': g, 'positions': a['n'],
                     'committed': round(a['committed'], 2),
                     'share_pct': round(100.0 * a['committed'] / total, 1),
                     'tickers': sorted(a['tickers']),
                     'watchlist_names': len(names.get(g, []))})
    return {'book': book, 'open_positions': len(pos),
            'basis': 'cash reserved' if cash_only else 'all committed',
            'excluded_debit_positions': len(dropped),
            'total_committed': round(total, 2), 'groups': rows}


def report(b):
    L = ['EXPOSURE BY GROUP  |  book %s  |  basis %s'
         % (b['book'], b['basis']),
         '%d open positions  |  committed %s%s'
         % (b['open_positions'], format(int(b['total_committed']), ',d'),
            ('  |  %d debit positions excluded' % b['excluded_debit_positions'])
            if b['excluded_debit_positions'] else ''),
         '',
         '%-22s %4s %13s %7s  %s' % ('group', 'pos', 'committed', 'share', 'tickers')]
    for r in b['groups']:
        L.append('%-22s %4d %13s %6.1f%%  %s'
                 % (r['group'][:22], r['positions'],
                    format(int(r['committed']), ',d'), r['share_pct'],
                    ' '.join(r['tickers'])[:44]))
    top = b['groups'][0] if b['groups'] else None
    if top:
        L += ['', 'largest single group: %s at %.1f%% of committed capital'
              % (top['group'], top['share_pct'])]
    return '\n'.join(L)


def main():
    p = argparse.ArgumentParser(description='Read-only exposure concentration by group (W136).')
    p.add_argument('--db', default=os.environ.get('HELM_DB', DEFAULT_DB))
    p.add_argument('--book', default='REAL', choices=['REAL', 'PAPER', 'ALL'])
    p.add_argument('--json', action='store_true')
    p.add_argument('--cash-only', action='store_true', dest='cash_only',
                   help='count only capital actually reserved as cash -- '
                        'cash-secured puts and spread requirements -- and drop '
                        'long-debit positions. The two bases answer different '
                        'questions and are not interchangeable.')
    a = p.parse_args()
    b = build(a.db, a.book, a.cash_only)
    print(json.dumps(b, indent=2, sort_keys=True) if a.json else report(b))


if __name__ == '__main__':
    main()
