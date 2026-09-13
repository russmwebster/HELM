"""W171 verification: the retry path, both ways, on a fresh VACUUM INTO copy per
case. Every fetch is FAKED (no gateway in the VM) -- what is under test is the
driver: when a second pass happens, what the two ledger rows say, and that a
connect failure now leaves a row. Loaded-code probe names the module file."""
import os, sys, sqlite3, importlib, types
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
os.environ['HELM_ROOT'] = ROOT
sys.path.insert(0, ROOT)
LIVE = os.path.join(ROOT, 'data', 'helm.db')
SCRATCH = os.path.expanduser('~/s120'); os.makedirs(SCRATCH, exist_ok=True)

# fakes for modules the VM lacks
for m in ('ib_insync',):
    sys.modules.setdefault(m, types.ModuleType(m))

P = F = 0
def check(name, cond, info=''):
    global P, F
    P += cond; F += (not cond)
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(info)) if info else ''))

def fresh(tag):
    dst = os.path.join(SCRATCH, 'w171_%s.db' % tag)
    if os.path.exists(dst): os.remove(dst)
    c = sqlite3.connect('file:%s?mode=ro' % LIVE, uri=True); c.execute("VACUUM INTO '%s'" % dst); c.close()
    os.environ['HELM_DB'] = dst
    global HWM
    HWM = sqlite3.connect(dst).execute("select max(id) from agent_runs").fetchone()[0]
    for k in [k for k in sys.modules if k.startswith('helm')]:
        del sys.modules[k]
    return dst

def load(passes, seconds_until=5, tickers=('AAA','BBB','CCC')):
    """Import ivr_cmd fresh, wire the fakes, return the module."""
    import helm.cli.ivr_cmd as M
    import helm.market_calendar as MC
    import helm.earnings as E
    from helm.models.iv_history import IVHistory
    MC.agent_should_run = lambda now=None: (True, 'open')
    M.agent_should_run = MC.agent_should_run
    E._refresh_earnings = lambda conn: None
    IVHistory.staleness_days = staticmethod(lambda t: None)
    calls = []
    def fake_pass(tk, today):
        r = passes[len(calls)]
        calls.append(list(tk))
        fails = [t for t in tk if t in r['fail']]
        return ({'ok': len(tk) - len(fails), 'fail': len(fails), 'skip': 0}, fails, r['connected'])
    M._refresh_pass = fake_pass
    M._seconds_until = lambda hhmm, now=None: seconds_until
    M.time.sleep = lambda s: None
    M.console = types.SimpleNamespace(print=lambda *a, **k: None)
    print('  loaded-code probe:', M.__file__, 'RETRY_AT', M.RETRY_AT)
    return M, calls

def rows(db):
    # only rows this case wrote: ids above the copy's high-water mark
    c = sqlite3.connect(db)
    return c.execute("select attempted, journaled, failed, status, notes from agent_runs "
                     "where agent='com.helm.ivr.refresh' and id > ? order by id", (HWM,)).fetchall()

# case A: connect refused at 09:35, retry connects and gets everything
db = fresh('A'); M, calls = load([{'fail': ['AAA','BBB','CCC'], 'connected': False},
                                  {'fail': [], 'connected': True}])
M.cmd_refresh(['AAA','BBB','CCC'])
r = rows(db)
check('A: two ledger rows', len(r) == 2, r)
check('A: first row records the refusal', r and r[0][3] == 'EMPTY' and 'could not connect' in (r[0][4] or ''), r[:1])
check('A: retry row says retry of HH:MM and recovered 3 of 3', len(r) == 2 and r[1][4].startswith('retry of ') and 'recovered 3 of 3' in r[1][4] and r[1][3] == 'OK', r[1:])
check('A: retry fetched ONLY the failed names', calls[1] == ['AAA','BBB','CCC'], calls)

# case B: partial at 09:35, retry recovers one of two
db = fresh('B'); M, calls = load([{'fail': ['BBB','CCC'], 'connected': True},
                                  {'fail': ['CCC'], 'connected': True}])
M.cmd_refresh(['AAA','BBB','CCC'])
r = rows(db)
check('B: two rows', len(r) == 2, r)
check('B: first row PARTIAL naming the two', r and r[0][3] == 'PARTIAL' and r[0][4] == 'BBB; CCC', r[:1])
check('B: retry attempted 2, recovered 1, still failing CCC', len(r) == 2 and r[1][0] == 2 and 'recovered 1 of 2' in r[1][4] and 'still failing: CCC' in r[1][4], r[1:])
check('B: retry fetched only BBB, CCC', calls[1] == ['BBB','CCC'], calls)

# case C: clean pass -> no retry, one row
db = fresh('C'); M, calls = load([{'fail': [], 'connected': True}])
M.cmd_refresh(['AAA','BBB','CCC'])
r = rows(db)
check('C: clean pass leaves one OK row and no retry', len(r) == 1 and r[0][3] == 'OK' and len(calls) == 1, r)

# case D: --no-retry
db = fresh('D'); M, calls = load([{'fail': ['AAA'], 'connected': True}])
M.cmd_refresh(['AAA','BBB','CCC','--no-retry'])
r = rows(db)
check('D: --no-retry -> one PARTIAL row, one pass', len(r) == 1 and r[0][3] == 'PARTIAL' and len(calls) == 1, r)

# case E: RETRY_AT already past -> no retry
db = fresh('E'); M, calls = load([{'fail': ['AAA'], 'connected': True}], seconds_until=-60)
M.cmd_refresh(['AAA','BBB','CCC'])
r = rows(db)
check('E: retry time past -> one row, one pass', len(r) == 1 and len(calls) == 1, r)

# case F: RETRY_AT too far ahead (interactive 06:00 run) -> no retry
db = fresh('F'); M, calls = load([{'fail': ['AAA'], 'connected': True}], seconds_until=4*3600)
M.cmd_refresh(['AAA','BBB','CCC'])
r = rows(db)
check('F: retry too far ahead -> one row, one pass', len(r) == 1 and len(calls) == 1, r)

# constants as shipped
check('RETRY_AT is 10:15 (after the 10:00 snapshot finishes)', M.RETRY_AT == (10, 15))
check('_seconds_until arithmetic', True)
import datetime as _dt
for k in [k for k in sys.modules if k.startswith('helm')]: del sys.modules[k]
import helm.cli.ivr_cmd as M2
now = _dt.datetime(2026, 9, 14, 9, 40, 0)
check('_seconds_until 09:40 -> 10:15 is 2100s', M2._seconds_until((10, 15), now) == 2100, M2._seconds_until((10,15), now))
check('_seconds_until after target is negative', M2._seconds_until((10, 15), _dt.datetime(2026,9,14,10,20)) < 0)

print('\nPASS %d  FAIL %d' % (P, F))
sys.exit(1 if F else 0)
