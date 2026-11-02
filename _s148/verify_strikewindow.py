"""s148 -- the proposal tools read strikes from the stock's IV, not 3 x ATR.

Stand-in IBKR (made-up chains, Black-Scholes with a put skew) and a stand-in Yahoo (a calm
14-day range). Compares the old fetch functions (.bak-20261102-s148) with the new ones.
Usage:  TMPDIR=/tmp python3 -B _s148/verify_strikewindow.py COPY.db   [CODE=dir to load new code from]
COPY.db is a VACUUM INTO copy of helm.db; nothing in it may change.
"""
import hashlib, math, os, sqlite3, sys, types
from datetime import date
from importlib.machinery import SourceFileLoader
from statistics import NormalDist

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = os.environ.get("CODE", REPO)
DB = sys.argv[1]
os.environ["HELM_ROOT"] = REPO
os.environ["HELM_DB"] = DB
sys.path.insert(0, CODE)
sys.path.insert(1, "/tmp/pylib")
print("new code loaded from", CODE, "| old from", REPO, "*.bak-20261102-s148 | db", DB)

fails, n = [], [0]
def ok(c, m):
    n[0] += 1
    print(("  ok   " if c else "  FAIL ") + m)
    if not c:
        fails.append(m)

def table_hash(path):
    c = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        h = hashlib.sha1()
        for r in c.execute('SELECT * FROM "%s" ORDER BY 1' % t):
            h.update(repr(r).encode())
        out[t] = h.hexdigest()
    c.close()
    return out

class _O:
    def __init__(self, **k): self.__dict__.update(k)

TODAY = date.today()
N = NormalDist()
def bs(S, K, sig, days, right):
    T = days / 365.0
    d1 = (math.log(S / K) + (0.045 + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    if right == "C":
        return S * N.cdf(d1) - K * math.exp(-0.045 * T) * N.cdf(d2), N.cdf(d1)
    return K * math.exp(-0.045 * T) * N.cdf(-d2) - S * N.cdf(-d1), N.cdf(d1) - 1

STATE = {}
def fake_ib():
    m = types.ModuleType("ib_insync")
    class IB:
        RequestTimeout = 0
        def connect(self, *a, **k): pass
        def isConnected(self): return True
        def disconnect(self): pass
        def reqMarketDataType(self, *a): pass
        def sleep(self, *a): pass
        def qualifyContracts(self, *cs):
            for c in cs: c.conId = 1
        def reqSecDefOptParams(self, *a):
            return [_O(expirations=STATE["exps"], strikes=STATE["strikes"])]
        def reqMktData(self, opt, *a):
            S, base = STATE["spot"], STATE["iv"]
            STATE["asked"].append(opt.strike)
            sig = base * (1 + 1.5 * max(0.0, (S - opt.strike) / S))     # a put skew
            days = (date(int(opt.exp[:4]), int(opt.exp[4:6]), int(opt.exp[6:])) - TODAY).days
            px, d = bs(S, opt.strike, sig, days, opt.right)
            px = max(px, 0.05)
            return _O(bid=round(px * 0.98, 2), ask=round(px * 1.02, 2) + 0.01, openInterest=10,
                      modelGreeks=_O(delta=d, theta=-0.02, gamma=0.01, vega=0.2, impliedVol=sig), lastGreeks=None)
    m.IB = IB
    m.Option = lambda t, exp, k, right, *a: _O(symbol=t, exp=exp, strike=k, right=right, conId=0)
    m.Stock = lambda *a: _O(conId=0)
    return m

class _FS:
    def __init__(self, v): self.v = v
    def __sub__(self, o): return _FS([a - b for a, b in zip(self.v, o.v)])
    def rolling(self, k): return _O(mean=lambda: _O(iloc=[sum(self.v[-k:]) / k]))
def fake_yf():
    y = types.ModuleType("yfinance")
    def T(t):
        S, rng = STATE["spot"], STATE["range"]
        h = _O(empty=False, __len__=None)
        class H:
            empty = False
            def __len__(self): return 20
            def __getitem__(self, k): return _FS([S + rng / 2] * 20 if k == "High" else [S - rng / 2] * 20)
        return _O(fast_info=_O(last_price=S), options=STATE["yexps"], history=lambda **k: H(), info={})
    y.Ticker = T
    return y

sys.modules["ib_insync"] = fake_ib()
sys.modules["yfinance"] = fake_yf()

def iso(d): return d.isoformat()
from datetime import timedelta
E1, E2 = TODAY + timedelta(days=32), TODAY + timedelta(days=39)
E_FAR = TODAY + timedelta(days=60)
def setup(spot, iv, rng, step, lo, hi, cur_exp=None):
    STATE.update(spot=spot, iv=iv, range=rng, asked=[],
                 strikes=[round(lo + i * step, 2) for i in range(int((hi - lo) / step) + 1)])
    ex = [E1, E2, E_FAR] + ([cur_exp] if cur_exp else [])
    STATE["yexps"] = tuple(sorted(iso(e) for e in ex))
    STATE["exps"] = [e.replace("-", "") for e in STATE["yexps"]]

before = table_hash(DB)
import helm.puttool as Pn, helm.calltool as Cn, helm.rolltool as Rn, helm.strikewindow as SW
Po = SourceFileLoader("put_old", os.path.join(REPO, "helm/puttool.py.bak-20261102-s148")).load_module()
Co = SourceFileLoader("call_old", os.path.join(REPO, "helm/calltool.py.bak-20261102-s148")).load_module()
Ro = SourceFileLoader("roll_old", os.path.join(REPO, "helm/rolltool.py.bak-20261102-s148")).load_module()
print("strikewindow from", SW.__file__)

# the stock's IV is read from iv_history (percent -> fraction)
c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
row = c.execute("SELECT ticker, iv_current FROM iv_history WHERE date=(SELECT MAX(date) FROM iv_history) "
                "AND ticker='XLC'").fetchone()
c.close()
iv_xlc = SW.latest_iv("XLC", TODAY)
ok(row and iv_xlc is not None and abs(iv_xlc - row[1] / 100.0) < 1e-12, "latest_iv reads XLC's stored IV as a fraction: %s" % iv_xlc)
ok(SW.latest_iv("NOSUCH", TODAY) is None, "no stored IV -> None (the 50% fallback)")

def inband(rows, lo, hi):
    return sorted((r["strike"], round(r["delta"], 3)) for r in rows if r.get("delta") and lo <= r["delta"] <= hi)

# P1 -- XLC-like calm fund: the old window misses 0.25-0.30 puts; the new one finds them
setup(114.0, iv_xlc, 1.0, 1.0, 80, 140)
ro, so, eo = Po.fetch_contracts("XLC", TODAY)
STATE["asked"] = []
rn, sn, en = Pn.fetch_contracts("XLC", TODAY)
bo, why_o = Po.best_contract(ro)
bn, why_n = Pn.best_contract(rn)
ok(bo is None and "nearest" in (why_o or ""), "P1 old: XLC left off -- %s" % why_o)
ok(bn is not None and 0.25 <= bn["delta"] <= 0.30, "P1 new: XLC offered %s %s at %.3f delta" % (bn and bn["expiration"], bn and bn["strike"], bn["delta"] if bn else 0))
ok(all(k <= 114.0 for k in STATE["asked"]), "P1 new: puts read below the price only (%d strikes asked)" % len(STATE["asked"]))

# P2 -- a volatile stock both windows reach: same best contract
setup(200.0, 0.60, 12.0, 2.5, 100, 300)
ro, _, _ = Po.fetch_contracts("NOSUCH", TODAY); n_old = len(STATE["asked"]); STATE["asked"] = []
rn, _, _ = Pn.fetch_contracts("NOSUCH", TODAY); n_new = len(STATE["asked"])
bo, _ = Po.best_contract(ro); bn, _ = Pn.best_contract(rn)
ok(bo is not None and bn is not None and (bo["strike"], bo["expiration"]) == (bn["strike"], bn["expiration"]),
   "P2 volatile stock: the same put either way (%s %s)" % (bn and bn["strike"], bn and bn["expiration"]))
print("       strikes asked: old %d, new %d (no stored IV, so the 50%% fallback)" % (n_old, n_new))

# C1 -- AAPL-like calm stock: the old window ends before 0.20-0.25 calls
setup(340.93, 0.26, 5.0, 5.0, 250, 450)
ro, _, _, _ = Co.fetch_calls("NOSUCH", TODAY)
STATE["asked"] = []
rn, _, _, _ = Cn.fetch_calls("NOSUCH", TODAY)
lo_o, lo_n = inband(ro, 0.20, 0.25), inband(rn, 0.20, 0.25)
ok(not lo_o, "C1 old: no 0.20-0.25 call read on a calm stock (%s)" % lo_o)
ok(lo_n and inband(rn, 0.25, 0.35), "C1 new: the whole 0.20-0.35 band read: %s" % inband(rn, 0.20, 0.35))
ok(all(k >= 340.93 for k in STATE["asked"]), "C1 new: calls read above the price only")

# R1 -- roll a calm stock's put: the current contract and lower strikes read
cur_exp = TODAY + timedelta(days=18)
setup(114.0, iv_xlc, 1.0, 1.0, 80, 140, cur_exp)
qo = Ro.fetch_roll("XLC", "PUT", 112.0, iso(cur_exp), today=TODAY)
qn = Rn.fetch_roll("XLC", "PUT", 112.0, iso(cur_exp), today=TODAY)
ok(qo["cur"] is not None and qn["cur"] is not None, "R1 the current contract is read either way")
lo_o = min((r["strike"] for r in qo["rows"]), default=None); lo_n = min((r["strike"] for r in qn["rows"]), default=None)
ok(lo_n is not None and lo_o is not None and lo_n < lo_o and inband(qn["rows"], 0.20, 0.30),
   "R1 new reads further down (lowest strike old %s, new %s; 0.20-0.30: %s)" % (lo_o, lo_n, inband(qn["rows"], 0.20, 0.30)))

# R2 -- a deep in-the-money put (GOOGL 335 at 280): its expiry is still read, the current contract found
setup(280.0, 0.35, 8.0, 5.0, 150, 400, cur_exp)
qn = Rn.fetch_roll("NOSUCH", "PUT", 335.0, iso(cur_exp), today=TODAY)
ok(qn["cur"] is not None and qn["cur"]["strike"] == 335.0, "R2 the deep in-the-money current put is read (%s)" % (qn["cur"] and qn["cur"]["strike"]))

# R3 -- edge: an expiry listing no strike inside the range still yields the current contract
setup(280.0, 0.35, 8.0, 5.0, 330, 340, cur_exp)
qn = Rn.fetch_roll("NOSUCH", "PUT", 335.0, iso(cur_exp), today=TODAY)
ok(qn["cur"] is not None, "R3 a chain with no strike near the price still reads the current 335 put")

# nothing written
ok(table_hash(DB) == before, "every table in the copy unchanged")
print("verify_strikewindow: %d checks, %d failed" % (n[0], len(fails)))
sys.exit(1 if fails else 0)
