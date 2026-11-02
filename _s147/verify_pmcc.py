"""s147 -- verify `helm pmcc`, the chain reader's strike range and the import's trend at entry.

    python3 -B _s147/verify_pmcc.py LIVE_DB [CODE_DIR]

Works on VACUUM INTO copies in /tmp/s147 (never the live database); IBKR and Yahoo are not
reached (the reader is driven by a fake ib_insync). CODE_DIR (default: this repo) is the code
under test, so a perturbed copy can be checked. Says which code it loaded.
  A  the rules' self-test through the CLI
  B  `helm help` lists pmcc
  C  the chain reader: old (.bak) and new give identical rows without strike_range; with it,
     only strikes inside the range, deep in-the-money calls included
  D  `helm pmcc NAMES --force` on a copy: runs, records, and changes only its own two tables
  E  the import booking a PMCC on a copy: the same tables change as with the old fillopens,
     plus one pmcc_entry_trend row
"""
import hashlib, json, math, os, sqlite3, subprocess, sys, types
from importlib.machinery import SourceFileLoader

LIVE = sys.argv[1]
CODE = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), ".."))
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
W = "/tmp/s147"
os.makedirs(W, exist_ok=True)
print("code under test:", CODE)
fails = []


def ok(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c:
        fails.append(m)


def copy(name):
    p = os.path.join(W, name)
    if os.path.exists(p):
        os.remove(p)
    s = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
    s.execute("VACUUM INTO ?", (p,))
    s.close()
    return p


def dump(p):
    c = sqlite3.connect(p)
    out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        cols = [r[1] for r in c.execute('PRAGMA table_info("%s")' % t)]
        rows = sorted(repr(r) for r in c.execute('SELECT * FROM "%s"' % t))
        out[t] = (tuple(cols), len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
    c.close()
    return out


def changed(a, b):
    return sorted(t for t in set(a) | set(b) if a.get(t) != b.get(t))


def env(db):
    e = dict(os.environ, HELM_DB=db, HELM_ROOT=REPO, COLUMNS="220", TMPDIR="/tmp", PYTHONPATH=CODE + ":/tmp/pylib",
             HELM_ADVICE_DIR=os.path.join(W, "advice"))
    return e


# A
r = subprocess.run([sys.executable, "-B", os.path.join(CODE, "helm.py"), "pmcc", "--selftest"],
                   capture_output=True, text=True, env=env(os.path.join(W, "none.db")), cwd=CODE)
ok(r.returncode == 0 and "all passed" in r.stdout, "A  self-test through the CLI (%s)" % r.stdout.strip().splitlines()[-1:])

# B
r = subprocess.run([sys.executable, "-B", os.path.join(CODE, "helm.py"), "help"], capture_output=True, text=True,
                   env=env(copy("b.db")), cwd=CODE)
ok("pmcc" in r.stdout and "rent cover" in r.stdout, "B  helm help lists pmcc")

# C -- a fake ib_insync drives both readers
def bs(spot, k, sig, days, right):
    T = days / 365.0
    d1 = (math.log(spot / k) + (0.045 + sig * sig / 2) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    call = spot * N(d1) - k * math.exp(-0.045 * T) * N(d2)
    if right == "C":
        return call, N(d1)
    return call - spot + k * math.exp(-0.045 * T), N(d1) - 1


class _O:
    def __init__(self, **k):
        self.__dict__.update(k)


def fake_ib_module(spot, strikes, sig):
    m = types.ModuleType("ib_insync")

    class IB:
        RequestTimeout = 0
        def connect(self, *a, **k): pass
        def isConnected(self): return True
        def disconnect(self): pass
        def reqMarketDataType(self, *a): pass
        def sleep(self, *a): pass
        def qualifyContracts(self, *cs):
            for c in cs:
                c.conId = 1
        def reqSecDefOptParams(self, *a):
            return [_O(expirations=self._exps, strikes=strikes)]
        def reqMktData(self, opt, *a):
            from datetime import date
            days = (date(int(opt.exp[:4]), int(opt.exp[4:6]), int(opt.exp[6:])) - date(2026, 11, 2)).days
            px, d = bs(spot, opt.strike, sig, days, opt.right)
            return _O(bid=round(px * 0.99, 2), ask=round(px * 1.01, 2) + 0.01, openInterest=10,
                      modelGreeks=_O(delta=d, theta=-0.02, gamma=0.01, vega=0.2, impliedVol=sig), lastGreeks=None)

    def Option(t, exp, k, right, *a):
        return _O(symbol=t, exp=exp, strike=k, right=right, conId=0)

    def Stock(*a):
        return _O(conId=0)
    m.IB, m.Option, m.Stock = IB, Option, Stock
    return m, IB


strikes = [float(k) for k in range(40, 165, 5)]
mod, IBc = fake_ib_module(100.0, strikes, 0.35)
sys.modules["ib_insync"] = mod
sys.path.insert(0, CODE)
sys.path.insert(1, "/tmp/pylib")
os.environ["HELM_ROOT"] = REPO
os.environ["HELM_DB"] = copy("c.db")
new = SourceFileLoader("oc_new", os.path.join(CODE, "helm/cli/open_cmd.py")).load_module()
old = SourceFileLoader("oc_old", os.path.join(REPO, "helm/cli/open_cmd.py.bak-20261102-s147")).load_module()
same = True
for typ, dmin, dmax, exps in (("PUT", 0.20, 0.35, [("2026-12-11", 39)]), ("CALL", 0.15, 0.40, [("2026-12-11", 39), ("2026-12-18", 46)]),
                              ("CALL", 0.0, 1.0, [("2027-12-17", 410)])):
    IBc._exps = [e.replace("-", "") for e, _ in exps]
    a = old.fetch_chain_from_ibkr("TST", typ, exps, 100.0, 2.5, dmin, dmax, (0.25, 0.30), spread_threshold=10.0)
    b = new.fetch_chain_from_ibkr("TST", typ, exps, 100.0, 2.5, dmin, dmax, (0.25, 0.30), spread_threshold=10.0)
    same = same and a == b and len(a) > 0
ok(same, "C  without strike_range the reader returns exactly what it did before (puts, calls, a LEAPS)")
IBc._exps = ["20271217"]
rng = (52.0, 96.0)
b = new.fetch_chain_from_ibkr("TST", "CALL", [("2027-12-17", 410)], 100.0, 2.5, 0.65, 0.95, (0.75, 0.85),
                              spread_threshold=10.0, strike_range=rng)
ks = sorted(x["strike"] for x in b)
ok(ks and all(rng[0] <= k <= rng[1] for k in ks) and any(0.75 <= x["delta"] <= 0.85 for x in b) and min(ks) <= 70,
   "C  with strike_range: only strikes inside it, deep in-the-money calls read (%s)" % ks)
a = old.fetch_chain_from_ibkr("TST", "CALL", [("2027-12-17", 410)], 100.0, 2.5, 0.65, 0.95, (0.75, 0.85), spread_threshold=10.0)
ok(not any(0.75 <= x["delta"] <= 0.85 for x in a),
   "C  (control: the plain +/-3xATR window misses the 0.75-0.85 band -- why the range is needed)")
# F (added after the 11-02 live run) -- fetch_pairs reads the 0.20-0.30 short calls on a calm stock.
# AAPL 11-02: price 340.93, IV 26%, a 14-day range of about $5, so the plain +/-3xATR window ends near 356.
from datetime import date as _date
class _FS:
    def __init__(self, v): self.v = v
    def __sub__(self, o): return _FS([a - b for a, b in zip(self.v, o.v)])
    def rolling(self, n): return _FR(self.v, n)
class _FR:
    def __init__(self, v, n): self.v, self.n = v, n
    def mean(self): return _O(iloc=[sum(self.v[-self.n:]) / self.n])
class _H:
    empty = False
    def __len__(self): return 20
    def __getitem__(self, k): return _FS([343.0] * 20 if k == "High" else [338.0] * 20)
yf = types.ModuleType("yfinance")
yf.Ticker = lambda t: _O(fast_info=_O(last_price=340.93), options=("2026-12-04", "2026-12-11", "2027-12-17"),
                         history=lambda **k: _H(), info={})
sys.modules["yfinance"] = yf
mod2, IB2 = fake_ib_module(340.93, [float(k) for k in range(150, 420, 5)], 0.26)
IB2._exps = ["20261204", "20261211", "20271217"]
sys.modules["ib_insync"] = mod2
import importlib
sys.modules["helm.cli.open_cmd"] = new
pm = SourceFileLoader("pm_new", os.path.join(CODE, "helm/pmcctool.py")).load_module()
longs, shorts, spot, err = pm.fetch_pairs("AAPL", _date(2026, 11, 2), 0.26)
inb = sorted((x["strike"], round(x["delta"], 2)) for x in shorts if 0.20 <= x["delta"] <= 0.30)
ok(err is None and inb, "F  the short calls in the 0.20-0.30 band are read on a calm stock: %s" % inb)
pair, why = pm.best_pair(longs, shorts, spot)
ok(longs and any(0.75 <= x["delta"] <= 0.85 for x in longs), "F  and the longs are read once a short passes (%d longs)" % len(longs))
del sys.modules["ib_insync"], sys.modules["yfinance"], sys.modules["helm.cli.open_cmd"]

# D
db = copy("d.db")
before = dump(db)
r = subprocess.run([sys.executable, "-B", os.path.join(CODE, "helm.py"), "pmcc", "NVDA,SPY,HD", "--force"],
                   capture_output=True, text=True, env=env(db), cwd=CODE, timeout=300)
after = dump(db)
ch = changed(before, after)
ok(r.returncode == 0 and "New PMCCs" in r.stdout, "D  helm pmcc runs on a copy (rc %s)" % r.returncode)
_n = lambda d, t: d.get(t, ((), 0, ""))[1]
ok(set(ch) <= {"pmcc_candidates", "pmcc_entry_trend", "pmcc_runs"} and "pmcc_runs" in ch
   and _n(after, "pmcc_runs") == _n(before, "pmcc_runs") + 1
   and _n(after, "pmcc_entry_trend") == _n(before, "pmcc_entry_trend"),
   "D  only its own tables change -- one run added, no entry trend (works whether or not the copy already has runs): %s" % ch)
c = sqlite3.connect(db)
rj = json.loads(c.execute("SELECT rejects_json FROM pmcc_runs WHERE note='partial run: NVDA,SPY,HD' "
                          "ORDER BY run_at DESC, rowid DESC LIMIT 1").fetchone()[0])
c.close()
ok(set(rj) == {"NVDA", "SPY", "HD"} and all(v for v in rj.values()),
   "D  every name left off carries a reason (no network here): %s" % rj)
r2 = subprocess.run([sys.executable, "-B", os.path.join(CODE, "helm.py"), "pmcc", "--last", "--why"],
                    capture_output=True, text=True, env=env(db), cwd=CODE)
ok(r2.returncode == 0 and "NVDA" in r2.stdout and dump(db) == after, "D  --last --why reads it back and writes nothing")
if r.returncode != 0:
    print(r.stdout[-1500:], r.stderr[-1500:])

# E -- book one PMCC through fillopens.book on two copies: old code, new code
E_SCRIPT = r'''
import sys, os
sys.path[:0] = os.environ["PYTHONPATH"].split(":")
from importlib.machinery import SourceFileLoader
which = sys.argv[1]
if which == "old":
    m = SourceFileLoader("helm.fillopens", os.path.join(sys.argv[2], "helm/fillopens.py.bak-20261102-s147")).load_module()
    sys.modules["helm.fillopens"] = m
import helm.fillopens as F
from helm.fillimport import Item
F._quotes = lambda tk, legs: (200.0, {0: {"bid": 61.0, "ask": 62.0, "mid": 61.5, "delta": 0.8, "gamma": 0.004, "theta": 0.02, "vega": 0.5, "iv": 40.0},
                                      1: {"bid": 3.3, "ask": 3.5, "mid": 3.4, "delta": 0.25, "gamma": 0.01, "theta": 0.08, "vega": 0.2, "iv": 45.0}}, [])
import helm.pmcctool as M
falling = [300 - i * 0.4 for i in range(252)]
M.fetch_trends = lambda ts, as_of=None: {t: dict(M.trend(falling), last_bar="2026-10-30") for t in ts}
it = Item("OPEN", "test", ticker="ZZTST", as_of="2026-10-30")
it.open = {"strategy": "PMCC", "contracts": 1, "flags": [], "grade": "B", "parent": None,
           "legs": [{"direction": "LONG", "opt_type": "CALL", "strike": 140.0, "expiration": "2027-12-17", "price": 61.5, "contracts": 1},
                    {"direction": "SHORT", "opt_type": "CALL", "strike": 215.0, "expiration": "2026-12-04", "price": 3.4, "contracts": 1}]}
pid, lids, lab = F.book(it, "2026-11-02 12:00", "16:00:00")
print("PID", pid)
print("LAB", lab[-200:])
'''
open(os.path.join(W, "e_book.py"), "w").write(E_SCRIPT)
res = {}
HOME = os.path.join(W, "home")          # the account id lives in ~/.helm_profile (standing rule, s144)
os.makedirs(HOME, exist_ok=True)
_c = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
open(os.path.join(HOME, ".helm_profile"), "w").write(
    _c.execute("SELECT account_id FROM positions WHERE book='REAL' ORDER BY opened_at DESC LIMIT 1").fetchone()[0])
_c.close()
for which in ("old", "new"):
    db = copy("e_%s.db" % which)
    b0 = dump(db)
    r = subprocess.run([sys.executable, "-B", os.path.join(W, "e_book.py"), which, REPO], capture_output=True, text=True,
                       env=dict(env(db), HOME=HOME), cwd=CODE, timeout=300)
    b1 = dump(db)
    res[which] = (r, {t: (b1[t][1] - b0.get(t, ((), 0, ""))[1]) for t in changed(b0, b1)}, db)
    if r.returncode != 0:
        print(which, r.stdout[-800:], r.stderr[-1500:])
ro, rn = res["old"], res["new"]
ok(ro[0].returncode == 0 and rn[0].returncode == 0, "E  the import books the PMCC with old and new code")
extra = {t: n for t, n in rn[1].items() if ro[1].get(t) != n}
ok(extra == {"pmcc_entry_trend": 1}, "E  new code changes the same tables as old, plus one entry-trend row: %s (old %s)"
   % (extra, ro[1]))
c = sqlite3.connect(rn[2])
row = c.execute("SELECT t.ticker, t.as_of, t.downtrend, t.note, p.strategy FROM pmcc_entry_trend t "
                "JOIN positions p ON p.id = t.position_id").fetchone()
c.close()
ok(row and row[0] == "ZZTST" and row[1] == "2026-10-30" and row[2] == 1 and row[4] == "PMCC" and "DOWNTREND" in row[3],
   "E  the row names the PMCC, the fill date and the downtrend: %s" % (row,))
ok("trend at entry: DOWNTREND" in rn[0].stdout, "E  the import's line reports the trend")

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
