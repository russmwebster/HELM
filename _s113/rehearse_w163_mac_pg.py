"""W163 (s113) -- Mac-side rehearsal of the PG half: engine_store.log_contract ->
helm_engine.log_open -> helm open (subprocess), three cases on fresh copies:
bypass_ok (55/45, MANUAL_PIN), bad_pin (48.5 not on chain -> PG status refused, 0 rows),
candidate (55/47 -> SELL_SCREEN). Results /tmp/w163_pg.json."""
import os, sys, sqlite3, json, time
HOME = os.path.expanduser('~'); ROOT = HOME + '/Projects/helm'; PG = HOME + '/Projects/helm-pg'
LIVE = ROOT + '/data/helm.db'; RES = '/tmp/w163_pg.json'
def cnt(db):
    c = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    d = {t: c.execute('select count(*) from ' + t).fetchone()[0] for t in ('positions', 'legs', 'entry_snapshots', 'lifecycle_events')}
    c.close(); return d
def fresh(name):
    p = '/tmp/w163_pg_' + name + '.db'
    try: os.remove(p)
    except FileNotFoundError: pass
    s = sqlite3.connect('file:' + LIVE + '?mode=ro', uri=True); s.execute('VACUUM INTO ?', (p,)); s.close(); return p
sys.path.insert(0, PG); os.environ['HELM_ROOT'] = ROOT
res = {'live_before': cnt(LIVE)}
import engine_store
cases = {
 'bypass_ok': dict(strike=55, expiry='2026-10-16', long_strike=45, long_expiry='2026-12-18', contracts=5, price=2.33, long_price=10.20),
 'bad_pin':   dict(strike=55, expiry='2026-10-16', long_strike=48.5, long_expiry='2026-12-18', contracts=5, price=2.33, long_price=10.20),
 'candidate': dict(strike=55, expiry='2026-10-16', long_strike=47, long_expiry='2026-12-18', contracts=5, price=2.33, long_price=8.98),
}
for name, kw in cases.items():
    db = fresh(name); os.environ['HELM_DB'] = db; b = cnt(db); t0 = time.time()
    r = engine_store.log_contract('AA', 'DIAGONAL', 'diagonal', 1, kw['contracts'], kw['price'], dte=None,
                                  strike=kw['strike'], expiry=kw['expiry'], long_strike=kw['long_strike'],
                                  long_expiry=kw['long_expiry'], long_price=kw['long_price'])
    a = cnt(db)
    c = sqlite3.connect('file:' + db + '?mode=ro', uri=True); c.row_factory = sqlite3.Row
    p = c.execute("select id, origin_screen, net_premium, notes from positions where ticker='AA' and strategy='DIAGONAL' order by created_at desc limit 1").fetchone()
    res[name] = {'status': r.get('status'), 'ok': r.get('ok'), 'cmd': r.get('cmd'), 'secs': round(time.time() - t0, 1),
                 'delta_rows': {k: a[k] - b[k] for k in a}, 'newest': dict(p) if p else None,
                 'tail': (r.get('output') or '')[-500:]}
res['live_after'] = cnt(LIVE)
json.dump(res, open(RES, 'w'), default=str, indent=1)
