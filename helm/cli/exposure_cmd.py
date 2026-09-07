"""helm exposure -- W135. What is this candidate joining?

    helm exposure                 # the book, by group
    helm exposure NVDA            # what NVDA would join
    helm exposure NVDA --book PAPER
    helm exposure --json

Every entry gate in HELM judges a candidate alone. None of them reads the open
book. This one only reads the open book -- and it GATES NOTHING (HELM-193): on
the real book HELM informs, it does not act.

It reports COMMITTED CAPITAL, not max loss. See helm/exposure.py.
"""
import json
import sys

from helm import exposure as X


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def run():
    book = (_arg('--book', 'REAL') or 'REAL').upper()
    as_json = '--json' in sys.argv
    tickers = [a for a in sys.argv[2:]
               if not a.startswith('-') and a != book]
    ticker = tickers[0].upper() if tickers else None

    if ticker:
        out = X.candidate_read(ticker, book)
        if as_json:
            print(json.dumps(out, indent=2))
            return 0
        for line in out['lines']:
            print(line)
        return 0

    b = X.book_exposure(book)
    if as_json:
        print(json.dumps(b, indent=2, default=list))
        return 0
    total = b['total_committed']
    print("EXPOSURE BY GROUP  |  book %s  |  %d open positions  |  committed $%s"
          % (b['book'], b['open_positions'], format(int(total), ',d')))
    print("gates nothing -- HELM-193: on the real book HELM informs, it does not act.")
    print()
    print("%-24s %4s %13s %7s  %s" % ('group', 'pos', 'committed', 'share', 'tickers'))
    rows = sorted(b['groups'].items(), key=lambda kv: -kv[1]['committed'])
    for g, a in rows:
        print("%-24s %4d %13s %6.1f%%  %s"
              % (g, a['n'], '$' + format(int(a['committed']), ',d'),
                 a['share_pct'], ', '.join(a['tickers'])))
    if rows:
        g, a = rows[0]
        print()
        print("largest single group: %s at %.1f%% of committed capital"
              % (g, a['share_pct']))
    return 0
