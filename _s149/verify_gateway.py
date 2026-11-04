"""s149: a gateway failure is said, not read as "no quotes"; a snapshot that read nothing is "failed".

  A. the chain reader (helm/cli/open_cmd.fetch_chain_from_ibkr) with a stand-in IB:
     refused connection / failed read / connection lost / healthy -- report_errors on and off
  B. helm puts and helm pmcc runs (build) with stand-in fetches: the honest reason, the stop after
     3 names in a row, the rest marked, a real empty chain still "no quotes"
  C. calltool / rolltool fetches turn a ChainReadError into their error, not empty rows
  D. advice.write_files on a DB copy: 0 of 7 -> failed (advice.json untouched), 5 of 7 -> ok + reason,
     7 of 7 -> ok, no positions -> ok
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s149/verify_gateway.py [CODE_DIR]
"""
import json, os, sqlite3, sys, types, shutil
from datetime import date
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"
CODE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else ROOT
sys.path.insert(0, CODE)
os.environ.setdefault("HELM_ROOT", ROOT)
W = "/tmp/s149gw"; os.makedirs(W, exist_ok=True)
COPY = W + "/gw.db"
if os.path.exists(COPY): os.remove(COPY)
sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (COPY,))
os.environ["HELM_DB"] = COPY
os.environ["HELM_ADVICE_DIR"] = W + "/adv"; os.makedirs(W + "/adv", exist_ok=True)
fails = []
def ok(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)

# ---- a stand-in ib_insync
MODE = {"m": "healthy"}
class _G: pass
class _T:
    def __init__(s, k):
        s.bid, s.ask = 1.00, 1.05; g = _G(); g.delta, g.theta, g.gamma, g.vega, g.impliedVol = -0.27, -0.03, 0.02, 0.05, 0.30
        s.modelGreeks, s.lastGreeks, s.openInterest = g, None, 100
class IB:
    def __init__(s): s.c = False
    def connect(s, *a, **k):
        if MODE["m"] == "refused": raise TimeoutError()
        s.c = True
    def reqMarketDataType(s, *a): pass
    def isConnected(s): return s.c and MODE["m"] != "lost"
    def disconnect(s): s.c = False
    def qualifyContracts(s, *cs):
        for x in cs: x.conId = 1
    def reqSecDefOptParams(s, *a):
        if MODE["m"] == "readfail": raise ConnectionResetError("peer reset")
        p = _G(); p.expirations = {"20261218"}; p.strikes = {95.0, 100.0}; return [p]
    def sleep(s, *a): pass
    def reqMktData(s, o, *a): return _T(o.strike)
class Option:
    def __init__(s, sym, exp, k, right, *a): s.strike = k; s.conId = 0
class Stock:
    def __init__(s, *a): s.conId = 0
fake = types.ModuleType("ib_insync"); fake.IB, fake.Option, fake.Stock = IB, Option, Stock
sys.modules["ib_insync"] = fake

from helm.cli import open_cmd as oc
from helm import puttool as P, pmcctool as M, calltool as C, rolltool as R, advice as A
print("code loaded:", oc.__file__)

def fetch(rep):
    return oc.fetch_chain_from_ibkr("XYZ", "PUT", [("2026-12-18", 45)], 100.0, 2.0, 0.20, 0.35, (0.25, 0.30),
                                    spread_threshold=10.0, strike_range=(90, 105), report_errors=rep)
import importlib.machinery as _ml
OLD = _ml.SourceFileLoader("open_cmd_old", ROOT + "/helm/cli/open_cmd.py.bak-20261103-s149").load_module()
def fetch_old():
    return OLD.fetch_chain_from_ibkr("XYZ", "PUT", [("2026-12-18", 45)], 100.0, 2.0, 0.20, 0.35, (0.25, 0.30),
                                     spread_threshold=10.0, strike_range=(90, 105))
print("A. the chain reader")
MODE["m"] = "healthy"; h0, h1 = fetch(False), fetch(True)
ok(len(h0) == 2 and h0 == h1 == fetch_old(), "healthy: the same rows off, on and from the old reader (%d)" % len(h0))
for m, want in (("refused", "IB Gateway not reachable on client 15 (TimeoutError)"),
                ("readfail", "chain read failed after connecting (ConnectionResetError: peer reset)"),
                ("lost", "IB Gateway connection lost while reading")):
    MODE["m"] = m
    ok(fetch(False) == fetch_old(), "%s, report_errors off: exactly what the old reader returns (%d rows)" % (m, len(fetch_old())))
    try:
        fetch(True); got = None
    except oc.ChainReadError as e:
        got = str(e)
    ok(got == want, "%s, report_errors on: raises %r" % (m, got))

print("B. helm puts / helm pmcc runs")
DOWN = "IB Gateway not reachable on client 15 (TimeoutError)"
def run_puts(names, down):
    P_screen = P.screen
    P.screen = lambda conn, today, tickers=None: ([{"ticker": t} for t in names], {}, "2026-11-03", len(names))
    try:
        f = lambda t: ([], 50.0, DOWN) if t in down else (([], 50.0, None) if t == "EMPTY" else ([], 50.0, "no expiry between 30 and 60 days"))
        return P.build(None, date(2026, 11, 3), fetch=f, book={"share": 1.0, "used": 0.0})
    finally:
        P.screen = P_screen
r = run_puts(["A", "B", "EMPTY", "C", "D"], {"A", "B", "C", "D"})
ok(r["stopped"] is None, "two down, a readable name, two down: no stop (not 3 in a row)")
ok(r["rejects"]["A"] == "chain not read: " + DOWN, "a down name says the gateway, not a market reason: %r" % r["rejects"]["A"])
ok(r["rejects"]["EMPTY"].startswith("IBKR returned no two-sided put quotes"), "a real empty chain still reads 'no quotes'")
r = run_puts(["A", "B", "C", "D", "E"], {"A", "B", "C", "D", "E"})
ok(r["stopped"] == ["A", "B", "C", "D", "E"], "3 in a row stop the run; the rerun covers the streak and the rest: %s" % r["stopped"])
ok(r["rejects"]["D"] == r["rejects"]["E"] == "not read: run stopped -- IB Gateway unreachable (3 names in a row)",
   "the names after the stop are marked not read")
ok(r["rejects"]["C"] == "chain not read: " + DOWN, "the third down name keeps its own reason")
M_screen = M.screen
M.screen = lambda conn, today, tickers=None: ([{"ticker": t, "iv_current": 30.0} for t in "PQRST"], {}, "2026-11-03", 5)
seen = []
try:
    r = M.build(None, date(2026, 11, 3), fetch=lambda t, td, sg: ([], [], 50.0, DOWN if t in "PQR" else None),
                trends={}, book={"share": 1.0, "used": 0.0}, progress=lambda t, w, s: seen.append(t))
finally:
    M.screen = M_screen
ok(r["stopped"] == ["P", "Q", "R", "S", "T"] and r["rejects"]["S"].startswith("not read: run stopped"),
   "pmcc: stops after 3 down, rest marked: %s" % r["stopped"])
ok(seen == ["P", "Q", "R"], "pmcc: progress printed for the names read, then stops: %s" % seen)

print("C. calltool / rolltool fetches")
class _YT:
    def __init__(s, t):
        s.fast_info = _G(); s.fast_info.last_price = 100.0; s.options = ("2026-12-18",); s.info = {}
    def history(s, **k):
        raise RuntimeError("no history")
fy = types.ModuleType("yfinance"); fy.Ticker = _YT; sys.modules["yfinance"] = fy
MODE["m"] = "refused"
rows, spot, exdiv, err = C.fetch_calls("XYZ", today=date(2026, 11, 3)) if "today" in C.fetch_calls.__code__.co_varnames else C.fetch_calls("XYZ")
ok(rows == [] and err == DOWN, "calls: a refused connection is its error: %r" % err)
q = R.fetch_roll("XYZ", "PUT", 95.0, "2026-12-04", today=date(2026, 11, 3))
ok(q["rows"] == [] and q["err"] == DOWN, "rolls: a refused connection is its error: %r" % q["err"])
MODE["m"] = "healthy"
q = R.fetch_roll("XYZ", "PUT", 95.0, "2026-12-04", today=date(2026, 11, 3))
ok(q["err"] is None, "rolls: healthy reads carry no error")

print("D. advice-status after a snapshot")
adv = os.environ["HELM_ADVICE_DIR"]
for f in os.listdir(adv): os.remove(os.path.join(adv, f))
good = A.write_files(snapshot={"started": "2026-11-03T12:30:05", "attempted": 7, "journaled": 7}, source="helm snapshot")
ok(good["status"] == "ok" and good.get("reason") is None, "7 of 7: ok, no reason")
before = open(adv + "/advice.json", "rb").read()
st = A.write_files(snapshot={"started": "2026-11-03T15:15:06", "attempted": 7, "journaled": 0}, source="helm snapshot")
ok(st["status"] == "failed" and st["ok"] is False and "read none of the 7" in st["reason"], "0 of 7: failed -- %s" % st.get("reason"))
ok(open(adv + "/advice.json", "rb").read() == before, "0 of 7: advice.json is left as the last good one")
ok(json.load(open(adv + "/advice-status.json"))["status"] == "failed", "the status file on disk says failed")
st = A.write_files(snapshot={"started": "2026-11-03T15:15:06", "attempted": 7, "journaled": 5}, source="helm snapshot")
ok(st["status"] == "ok" and st["reason"] == "the snapshot read 5 of 7 positions", "5 of 7: ok, with how many -- %s" % st.get("reason"))
st = A.write_files(snapshot={"started": "2026-11-03T10:00:05", "attempted": 0, "journaled": 0}, source="helm snapshot")
ok(st["status"] == "ok", "no positions open: ok, as before")
st = A.write_files(source="helm advise --json")
ok(st["status"] == "ok" and st.get("reason") is None, "helm advise --json (no snapshot): ok, as before")

print("\n%d failure(s)" % len(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
