#!/usr/bin/env python3
"""s101 -- backfill the watchlist rows that never got an optionability check.

Those rows sit active=0, is_optionable=0, so `helm scan` (active=1 AND
is_optionable=1) never sees them. This runs HELM's own quick_eval on them,
then sets the flags from what it found. FLAG (no options / delisted) stays off.

  --eval   run the checks, write /tmp/wl_eval.json   (slow, ~3s per ticker)
  --plan   show what would change                     (default)
  --apply  write it
"""
import json, os, sys, sqlite3, datetime, shutil
sys.path.insert(0, os.path.expanduser('~/Projects/helm'))
DB = os.path.expanduser('~/Projects/helm/data/helm.db')
OUT = '/tmp/wl_eval.json'

def targets():
    c = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
    r = [x[0] for x in c.execute(
        "select ticker from watchlist where active=0 or is_optionable=0 order by ticker")]
    c.close()
    return r

def do_eval():
    from helm.cli.watchlist import quick_eval
    ts = targets()
    print('evaluating %d tickers' % len(ts), flush=True)
    out = {}
    for i, t in enumerate(ts, 1):
        try:
            out[t] = quick_eval(t, include_options=True)
        except Exception as e:
            out[t] = {'ticker': t, 'verdict': 'ERROR', 'verdict_reason': str(e)[:120]}
        print('  %2d/%d %-6s %s' % (i, len(ts), t, out[t].get('verdict')), flush=True)
    json.dump(out, open(OUT, 'w'), default=str)
    print('WROTE', OUT, flush=True)

def plan(apply=False):
    if not os.path.exists(OUT):
        print('No %s -- run with --eval first.' % OUT); sys.exit(1)
    res = json.load(open(OUT))
    con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
    cur = {t: (a, o) for t, a, o in con.execute(
        "select ticker, active, is_optionable from watchlist")}
    con.close()
    ons, offs = [], []
    print('%-7s %-9s %-10s %s' % ('TICKER', 'VERDICT', 'ACTION', 'REASON'))
    for t in sorted(res):
        v = res[t].get('verdict', 'ERROR')
        if t not in cur:
            print('  %-7s %-9s SKIP  not on watchlist' % (t, v)); continue
        if v in ('STRONG', 'GOOD', 'MARGINAL'):
            ons.append(t); act = 'switch ON'
        else:
            offs.append(t); act = 'leave off'
        print('%-7s %-9s %-10s %s' % (t, v, act, str(res[t].get('verdict_reason', ''))[:60]))
    print()
    print('switch on: %d   leave off: %d' % (len(ons), len(offs)))
    if not apply:
        print('\nPLAN ONLY -- nothing written. Re-run with --apply.'); return
    bak = DB + '.bak-s101-wl-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    shutil.copy2(DB, bak); print('backup:', bak)
    con = sqlite3.connect(DB); c = con.cursor()
    n = 0
    for t in ons:
        r = res[t]
        c.execute("update watchlist set active=1, is_optionable=1, "
                  "company_name=coalesce(company_name,?), sector=coalesce(sector,?), "
                  "market_cap=coalesce(market_cap,?), beta=coalesce(beta,?) "
                  "where ticker=?",
                  (r.get('company_name'), r.get('sector'), r.get('market_cap'),
                   r.get('beta'), t))
        n += c.rowcount
    con.commit()
    chk = list(con.execute("select count(*) from watchlist where active=1 and is_optionable=1"))
    con.close()
    print('rows updated:', n)
    print('scan universe is now:', chk[0][0])
    if n != len(ons):
        print('WARNING: expected %d updates, got %d -- restore from %s' % (len(ons), n, bak))

if __name__ == '__main__':
    if '--eval' in sys.argv: do_eval()
    else: plan('--apply' in sys.argv)
