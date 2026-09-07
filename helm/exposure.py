"""helm/exposure.py -- W135. Does a candidate duplicate exposure the book
already carries?

WHY THIS EXISTS. Every entry gate in HELM judges a candidate on its own merits.
Not one of them reads the open positions. Measured 2026-08-18: 84.7% of reserved
capital -- $183,300 of $216,300 -- sat in one complex.

WHAT IT DOES, per HELM-193: on the REAL book it REPORTS and gates nothing. The
paper book is where a gate belongs, and that is deliberately NOT built here --
say which product you want before wiring one.

ONE DEFINITION OF COMMITTED CAPITAL. `committed()` moved here from
tools/exposure_report.py, which now imports it. Two copies of a number is how
W147 happened -- the documented value and the executing value drifting apart.

IT IS NOT MAX LOSS. For the 20-lot LRCX condor: committed $20,000, max loss
$10,440, assignment obligation $720,000. This answers "how concentrated am I",
not "what can this cost me".
"""
import os
import sqlite3
from collections import defaultdict


def committed(legs):
    """Capital a position ties up, derived from its legs.

    short leg with a protecting long of the same type : wider wing x 100 x qty
    short put with no long put (cash-secured)         : strike    x 100 x qty
    short call with no long call (covered)            : 0, the shares secure it
    long legs only (debit)                            : premium paid

    An iron condor takes the WIDER SIDE only -- both sides cannot finish ITM.
    """
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


def _conn(db=None):
    if db is None:
        from helm.config import DB_PATH
        db = str(DB_PATH)
    c = sqlite3.connect('file:' + os.path.abspath(db) + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def group_for(ticker, db=None):
    """The exposure group a ticker belongs to. None when the name is not on the
    watchlist; '(ungrouped)' when it is listed but carries no group -- W143's
    case, and the two are NOT the same answer."""
    c = _conn(db)
    try:
        r = c.execute("select exposure_group from watchlist where ticker = ?",
                      (ticker.upper(),)).fetchone()
    finally:
        c.close()
    if r is None:
        return None
    return r['exposure_group'] or '(ungrouped)'


def book_exposure(book='REAL', db=None):
    """Committed capital by exposure group across OPEN positions."""
    c = _conn(db)
    try:
        q = ("select p.id, p.ticker, coalesce(w.exposure_group,'(ungrouped)') g "
             "from positions p left join watchlist w on w.ticker = p.ticker "
             "where p.status like 'OPEN'")
        args = ()
        if book != 'ALL':
            q += " and p.book = ?"
            args = (book,)
        pos = [dict(r) for r in c.execute(q, args)]
        for p in pos:
            legs = [dict(r) for r in c.execute(
                'select * from legs where position_id = ?', (p['id'],))]
            p['committed'], p['kind'] = committed(legs)
    finally:
        c.close()
    agg = defaultdict(lambda: {'n': 0, 'committed': 0.0, 'tickers': set()})
    for p in pos:
        a = agg[p['g']]
        a['n'] += 1
        a['committed'] += p['committed']
        a['tickers'].add(p['ticker'])
    total = sum(a['committed'] for a in agg.values())
    return {'book': book, 'total_committed': total, 'open_positions': len(pos),
            'groups': {g: {'n': a['n'], 'committed': a['committed'],
                           'tickers': sorted(a['tickers']),
                           'share_pct': (100.0 * a['committed'] / total) if total else 0.0}
                       for g, a in agg.items()}}


def candidate_read(ticker, book='REAL', db=None):
    """What would this candidate be joining?

    Returns a dict, always -- never raises on an unknown name, because a name
    HELM has never heard of is a real answer and must not read like an error.
    `lines` is display-ready; nothing here gates anything.
    """
    ticker = (ticker or '').upper()
    g = group_for(ticker, db)
    b = book_exposure(book, db)
    total = b['total_committed']
    out = {'ticker': ticker, 'group': g, 'book': book,
           'total_committed': total, 'gates': False}
    if g is None:
        out['lines'] = ["%s is not on the watchlist, so it has no exposure group "
                        "-- concentration cannot be read for it." % ticker]
        return out
    e = b['groups'].get(g)
    if not e:
        out['share_pct'] = 0.0
        out['lines'] = ["%s joins %s, where the book currently has nothing open."
                        % (ticker, g)]
        return out
    held = ticker in e['tickers']
    out['share_pct'] = e['share_pct']
    out['positions'] = e['n']
    out['tickers'] = e['tickers']
    lines = ["%s joins %s -- already %.1f%% of committed capital "
             "(%s across %d position%s: %s)."
             % (ticker, g, e['share_pct'],
                '$' + format(int(e['committed']), ',d'), e['n'],
                '' if e['n'] == 1 else 's', ', '.join(e['tickers']))]
    if held:
        lines.append("You already hold %s in this group." % ticker)
    lines.append("Reported, not gated: on the real book HELM informs and never "
                 "acts (HELM-193).")
    out['lines'] = lines
    return out
