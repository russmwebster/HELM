#!/usr/bin/env python3
"""tools/watchlist_baseline.py - s108, 2026-08-20.  READ-ONLY.

Records the state of the watchlist funnel BEFORE the W137 expansion, so the
effect of adding names can be measured rather than asserted.

    python3 tools/watchlist_baseline.py             # the report
    python3 tools/watchlist_baseline.py --json      # machine readable
    python3 tools/watchlist_baseline.py --freeze    # write the fixture
    python3 tools/watchlist_baseline.py --selftest  # re-run against the fixture

WHY A FIXTURE.  s107's lesson from W16: an unsaved study cannot be re-run.
Rebuilding s101's sweep reproduced its published figures only to within 1-9%,
so a later run could not have separated method drift from a real change.
--selftest pins the METHOD, not the answer.

THE TWO NUMBERS THIS EXISTS TO TRACK
    expected quiet-day qualifiers : sum over sectors of names x quiet pass rate.
                                    The expansion is meant to RAISE this.
    Technology's share of them    : the expansion is meant to LOWER this.

METHOD.  Scan days are ranked by the fraction of scanned names clearing IV rank
50.  The bottom third are "quiet", the top third "busy".  A sector's quiet pass
rate is its clear-rate across the quiet days only.  Quiet days are the ones that
matter: a watchlist is too small when a slow Tuesday offers nothing to trade.

BLIND SPOTS, stated so nobody assumes otherwise
  * Pass rates use whatever is in signals.  At s108 that is June-August 2026
    only - one volatility regime, not four seasons.
  * It assumes a new name behaves like the existing names in its sector.  The
    current Energy and Materials names are all mega-cap; several planned
    additions are mid-cap and will run hotter.  Direction is safe, size is not.
  * IV rank 50 is hardcoded because that is the screen's gate.  Move the gate
    and this tool measures the wrong thing until it is updated too.
  * Sector comes from watchlist.sector, which cannot see thematic exposure --
    17 of 26 "Technology" names are one trade.  See W136.
"""
import argparse, json, os, sqlite3, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, os.pardir, 'data', 'helm.db')
FIXTURE = os.path.join(HERE, 'watchlist_baseline_fixture.json')
IVR_GATE = 50.0
MIN_NAMES_PER_DAY = 40


def load(db):
    con = sqlite3.connect('file:' + os.path.abspath(db) + '?mode=ro', uri=True)
    rows = list(con.execute(
        "select date(s.generated_at), coalesce(w.sector,'(unset)'), s.iv_rank "
        "from signals s join watchlist w on w.ticker = s.ticker "
        "where s.iv_rank is not null"))
    wl = list(con.execute(
        "select coalesce(sector,'(unset)'), coalesce(active,1) from watchlist"))
    con.close()
    return rows, wl


def compute(rows, wl):
    byday = defaultdict(list)
    for d, sec, ivr in rows:
        byday[d].append((sec, ivr))
    rate = dict((d, sum(1 for _, v in r if v >= IVR_GATE) / float(len(r)))
                for d, r in byday.items() if len(r) >= MIN_NAMES_PER_DAY)
    order = sorted(rate, key=lambda d: (rate[d], d))
    third = max(1, len(order) // 3)
    quiet, busy = set(order[:third]), set(order[-third:])
    agg = defaultdict(lambda: [0, 0, 0, 0])
    for d, r in byday.items():
        qd, bd = d in quiet, d in busy
        if not (qd or bd):
            continue
        for sec, ivr in r:
            hit = 1 if ivr >= IVR_GATE else 0
            a = agg[sec]
            if qd:
                a[0] += 1
                a[1] += hit
            if bd:
                a[2] += 1
                a[3] += hit
    census = Counter(sec for sec, act in wl if act)
    sectors, exp_quiet = {}, 0.0
    for sec in sorted(census):
        n = census[sec]
        a = agg.get(sec, [0, 0, 0, 0])
        qp = a[1] / float(a[0]) if a[0] else 0.0
        bp = a[3] / float(a[2]) if a[2] else 0.0
        sectors[sec] = {'names': n, 'quiet_pass': round(qp, 4),
                        'busy_pass': round(bp, 4),
                        'quiet_expected': round(n * qp, 2)}
        exp_quiet += n * qp
    tech = sectors.get('Technology', {}).get('quiet_expected', 0.0)
    latest = max(byday) if byday else None
    lat = byday.get(latest, [])
    return {
        'ivr_gate': IVR_GATE,
        'scan_days_used': len(order),
        'scan_day_first': min(order) if order else None,
        'scan_day_last': max(order) if order else None,
        'quiet_days': third,
        'busy_days': third,
        'watchlist_active': sum(census.values()),
        'expected_quiet_qualifiers': round(exp_quiet, 2),
        'tech_expected_quiet': round(tech, 2),
        'tech_share_pct': round(100.0 * tech / exp_quiet, 1) if exp_quiet else 0.0,
        'latest_scan_date': latest,
        'latest_scanned': len(lat),
        'latest_cleared': sum(1 for _, v in lat if v >= IVR_GATE),
        'sectors': sectors,
    }


def report(b):
    L = []
    L.append('WATCHLIST BASELINE  |  IV rank gate %.0f' % b['ivr_gate'])
    L.append('scan days %d (%s to %s)  |  quiet %d  |  busy %d'
             % (b['scan_days_used'], b['scan_day_first'], b['scan_day_last'],
                b['quiet_days'], b['busy_days']))
    L.append('active watchlist names %d' % b['watchlist_active'])
    L.append('')
    L.append('%-23s %5s %7s %7s %10s' % ('sector', 'names', 'quiet', 'busy', 'exp.quiet'))
    for sec in sorted(b['sectors'], key=lambda s: -b['sectors'][s]['quiet_pass']):
        s = b['sectors'][sec]
        L.append('%-23s %5d %6.0f%% %6.0f%% %10.2f'
                 % (sec[:23], s['names'], 100 * s['quiet_pass'],
                    100 * s['busy_pass'], s['quiet_expected']))
    L.append('')
    L.append('THE TWO TRACKED NUMBERS')
    L.append('  expected quiet-day qualifiers %9.2f' % b['expected_quiet_qualifiers'])
    L.append('  Technology share of them     %8.1f%%  (%.2f names)'
             % (b['tech_share_pct'], b['tech_expected_quiet']))
    L.append('')
    L.append('latest scan %s  |  scanned %d  |  cleared %d'
             % (b['latest_scan_date'], b['latest_scanned'], b['latest_cleared']))
    return '\n'.join(L)


def selftest(b):
    if not os.path.exists(FIXTURE):
        print('SELFTEST  no fixture found; run --freeze first')
        return 1
    with open(FIXTURE) as f:
        old = json.load(f)
    keys = ['ivr_gate', 'scan_days_used', 'watchlist_active', 'scan_day_first',
            'scan_day_last', 'expected_quiet_qualifiers', 'tech_share_pct']
    drift = [(k, old.get(k), b.get(k)) for k in keys if old.get(k) != b.get(k)]
    for sec in sorted(set(old.get('sectors', {})) | set(b['sectors'])):
        o = old.get('sectors', {}).get(sec, {})
        n = b['sectors'].get(sec, {})
        for f in ('names', 'quiet_pass', 'busy_pass'):
            if o.get(f) != n.get(f):
                drift.append((sec + '.' + f, o.get(f), n.get(f)))
    if not drift:
        print('SELFTEST PASS  |  %d scalars and %d sectors reproduce the fixture'
              % (len(keys), len(b['sectors'])))
        return 0
    print('SELFTEST DRIFT  |  %d differences' % len(drift))
    for k, o, n in drift[:12]:
        print('  %-32s fixture %-12s now %s' % (k, o, n))
    if len(drift) > 12:
        print('  ; and %d more' % (len(drift) - 12))
    return 1


def main():
    p = argparse.ArgumentParser(description='Read-only watchlist funnel baseline (W137).')
    p.add_argument('--db', default=os.environ.get('HELM_DB', DEFAULT_DB))
    p.add_argument('--json', action='store_true')
    p.add_argument('--freeze', action='store_true')
    p.add_argument('--selftest', action='store_true')
    a = p.parse_args()
    rows, wl = load(a.db)
    b = compute(rows, wl)
    if a.selftest:
        sys.exit(selftest(b))
    if a.freeze:
        with open(FIXTURE, 'w') as f:
            json.dump(b, f, indent=2, sort_keys=True)
        print('frozen %s (%d bytes)' % (os.path.basename(FIXTURE),
                                        os.path.getsize(FIXTURE)))
        return
    print(json.dumps(b, indent=2, sort_keys=True) if a.json else report(b))


if __name__ == '__main__':
    main()
