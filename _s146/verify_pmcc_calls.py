"""s146: helm calls on PMCC longs -- verify on a DATABASE COPY (never the live one).

    python3 _s146/verify_pmcc_calls.py COPY.db [CODE_DIR]

Runs the real `helm calls` command in-process against COPY.db with IBKR replaced by
a fixed NVDA chain, in two states of the NVDA PMCC: as booked (short open) and with
its short closed at $1.70 (written to the copy only). Checks every table: only
call_runs / call_candidates may change, and call_candidates' old rows are kept.
CODE_DIR (default: this repo) says which code is loaded -- printed first.
"""
import hashlib, io, os, sqlite3, sys
from contextlib import redirect_stdout
from datetime import date

db = os.path.abspath(sys.argv[1])
code = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), ".."))
assert "/data/helm.db" not in db, "never the live database"
os.environ["HELM_DB"] = db
os.environ["COLUMNS"] = "220"
sys.path.insert(0, code)
import helm
print("code:", os.path.dirname(helm.__file__), "| db:", db)
from helm import calltool as C
from helm.cli import calls_cmd, puts_cmd

FAILS = []
def ok(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c: FAILS.append(m)

EXP = "2026-12-04"
DTE = (date(2026, 12, 4) - date.today()).days
def row(k, mid, d):
    return {"source": "ibkr", "strike": k, "mid": mid, "bid": mid - 0.05, "ask": mid + 0.05, "delta": d,
            "dte": DTE, "spread_pct": 2.9, "expiration": EXP, "gamma": 0.01, "theta": -0.1, "vega": 0.2, "iv": 0.5}
CHAIN = [row(221.20, 4.6, 0.30), row(222.90, 4.0, 0.29), row(225.0, 3.4, 0.27), row(230.0, 2.5, 0.22),
         row(235.0, 1.9, 0.18), row(215.0, 6.0, 0.34), row(222.0, 4.3, 0.32)]
def fake_fetch(t, today=None):
    return (CHAIN, 204.64, None, None) if t == "NVDA" else ([], None, None, "no chain in the test")
d = list(C.build.__defaults__); d[2] = fake_fetch; C.build.__defaults__ = tuple(d)  # (tickers, assigned, fetch, account_id)
puts_cmd._in_window = lambda *a, **k: None

def tables(conn):
    out = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1"):
        cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)]
        h = hashlib.sha1()
        for r in conn.execute("SELECT * FROM %s ORDER BY %s" % (t, ", ".join('"%s"' % c for c in cols))):
            h.update(repr(tuple(r)).encode())
        out[t] = (tuple(cols), h.hexdigest(), conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
    return out

def run_calls():
    sys.argv = ["helm", "calls", "--why"]
    buf = io.StringIO()
    calls_cmd.console.file = buf
    calls_cmd.run()
    return buf.getvalue()

def step(title):
    print("\n--", title)
    c = sqlite3.connect(db); before = tables(c)
    old_cc = c.execute("SELECT run_id, ticker, choice, strike FROM call_candidates ORDER BY 1,2,3").fetchall() \
        if "call_candidates" in before else []
    c.close()
    out = run_calls()
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row; after = tables(c)
    changed = sorted(t for t in set(before) | set(after) if before.get(t) != after.get(t))
    ok(set(changed) <= {"call_runs", "call_candidates"}, "only call_runs / call_candidates changed: %s" % changed)
    kept = c.execute("SELECT run_id, ticker, choice, strike FROM call_candidates WHERE run_id IN (%s) ORDER BY 1,2,3"
                     % ",".join("?" * len({r[0] for r in old_cc})), tuple({r[0] for r in old_cc})).fetchall() if old_cc else []
    ok([tuple(r) for r in kept] == [tuple(r) for r in old_cc], "every earlier call_candidates row kept (%d)" % len(old_cc))
    run = C.latest(c, 0)
    c.close()
    print(out)
    return run, out

run, out = step("1. NVDA PMCC as booked (its 225 short is open)")
ok("NVDA (PMCC)" in run["rejects"] and "already open" in run["rejects"]["NVDA (PMCC)"]
   and not [x for x in run["candidates"] if x["kind"] == "pmcc"], "nothing to sell while the short is open")

c = sqlite3.connect(db)
n = c.execute("UPDATE legs SET status='CLOSED', close_price=1.70, close_date='2026-10-31' WHERE position_id IN "
              "(SELECT id FROM positions WHERE strategy='PMCC' AND ticker='NVDA' AND status='OPEN') "
              "AND direction='SHORT' AND status='OPEN'").rowcount
c.commit(); c.close()
ok(n == 1, "copy only: NVDA's short closed at $1.70 (rent $170)")

run, out = step("2. NVDA PMCC with no short")
pc = [x for x in run["candidates"] if x["kind"] == "pmcc"]
ok([x["strike"] for x in pc] == [222.90, 225.0, 230.0],
   "choices above $221.20, delta 0.20-0.30: %s (221.20 at the floor, 215 at 0.34, 222 at 0.32, 235 at 0.18 left off)" % [x["strike"] for x in pc])
ok(all(abs(x["floor_strike"] - 221.20) < 1e-9 for x in pc), "floor strike 160 + 62.90 - 1.70 = 221.20")
ok(abs(pc[0]["ann_return"] - 4.0 / 62.90 * 365 / DTE) < 1e-12, "yearly return on the long's $62.90")
ok(abs(pc[1]["if_called"] - ((225 - 221.20) * 100 + 340)) < 1e-6, "225 if called, at least: $380 + $340")
ok(all("INSIDE THE TRADE" in (x["earnings"] or "") for x in pc), "NVDA's 11-17 report inside a 12-04 short is flagged")
ok("NVDA PMCC" in out and "above $221.20" in out and "If called, at least" in out, "the printout names the floor")
print("\n%d failed" % len(FAILS) if FAILS else "\nALL PASS")
sys.exit(1 if FAILS else 0)
