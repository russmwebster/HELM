"""s146: helm roll and same-day rolls through the import -- on DATABASE COPIES only.

    python3 _s146/verify_rolls.py COPY.db [CODE_DIR]

Part A runs the real `helm roll` in-process on COPY.db with IBKR replaced by fixed
chains (NVDA tested PMCC short, GOOGL deep in the money, AMZN, VST graded D) and
checks every table: only roll_runs / roll_items / roll_contracts may change.
Part B builds a Fidelity-format download (from the newest real one's header, account
field and footer -- written to a temp folder, never printed) holding two same-day
rolls on 11/02: NVDA's 225 call bought back + a 230 12/11 call sold, AMZN's 245 put
bought back + a 235 12/11 put sold; it plans the import on a fresh copy (planning
needs no IBKR). Booking them needs IB Gateway: add --apply on the Mac.
"""
import glob, hashlib, io, os, shutil, sqlite3, sys, tempfile
from datetime import date

db = os.path.abspath(sys.argv[1])
code = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--")
                       else os.path.join(os.path.dirname(__file__), ".."))
assert "/data/helm.db" not in db, "never the live database"
os.environ["HELM_DB"] = db
os.environ["COLUMNS"] = "220"
sys.path.insert(0, code)
pristine = tempfile.mkdtemp(prefix="s146r-")
shutil.copy(db, os.path.join(pristine, "b.db"))          # part B starts from the untouched copy
import helm
print("code:", os.path.dirname(helm.__file__), "| db:", db)
from helm import rolltool as R
from helm.cli import roll_cmd, puts_cmd

FAILS = []
def ok(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c: FAILS.append(m)

import importlib.util
spec = importlib.util.spec_from_file_location("helm_main", os.path.join(code, "helm.py"))
hm = importlib.util.module_from_spec(spec); spec.loader.exec_module(hm)
ok(hm.COMMANDS["roll"][0] == "helm.cli.roll_cmd" and "roll" not in sum((list(n) for _, n in hm.LEGACY), [])
   and "roll" in dict(hm.MAIN)["Trading"], "`helm roll` is the new tool, listed under Trading, gone from legacy")

NEW = "2026-12-11"
DTE = (date(2026, 12, 11) - date.today()).days
def q(k, b, a, d):
    return {"source": "ibkr", "strike": k, "bid": b, "ask": a, "mid": round((b + a) / 2, 2), "delta": d, "dte": DTE,
            "expiration": NEW, "spread_pct": round((a - b) / ((a + b) / 2) * 100, 1)}
CH = {"NVDA": ({"bid": 8.8, "ask": 9.0}, [q(230, 9.6, 9.8, .42), q(228.5, 9.8, 10.0, .45), q(235, 7.8, 8.0, .36)]),
      "GOOGL": ({"bid": 54.2, "ask": 55.6}, [q(335, 54.2, 55.6, .97), q(330, 49.0, 50.5, .96)]),
      "AMZN": ({"bid": 8.8, "ask": 9.0}, [q(245, 11.0, 11.4, .48), q(240, 8.6, 8.9, .42), q(235, 9.05, 9.3, .36),
                                          q(230, 5.0, 5.3, .30), q(250, 14.0, 14.4, .49)]),
      "VST": ({"bid": 6.0, "ask": 6.2}, [q(135, 6.5, 6.8, .40)])}
def fake(t, ot, k, e, lp=None, today=None):
    cur, rows = CH[t]
    return {"rows": rows, "cur": dict(cur, strike=k, expiration=e), "long_q": {"bid": 80.0} if lp else None,
            "spot": 231.0, "exdiv": None, "err": None}
d = list(R.build.__defaults__); d[0] = fake; R.build.__defaults__ = tuple(d)
puts_cmd._in_window = lambda *a, **k: None

def tables(path):
    c = sqlite3.connect(path); out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % t)]
        h = hashlib.sha1()
        for r in c.execute("SELECT * FROM %s ORDER BY %s" % (t, ", ".join('"%s"' % x for x in cols))):
            h.update(repr(tuple(r)).encode())
        out[t] = (tuple(cols), h.hexdigest())
    c.close(); return out

print("\n-- A. helm roll NVDA,GOOGL,AMZN,VST on the copy, fixed chains")
before = tables(db)
sys.argv = ["helm", "roll", "NVDA,GOOGL,AMZN,VST,XYZ", "--why"]
buf = io.StringIO(); roll_cmd.console.file = buf
roll_cmd.run()
out = buf.getvalue(); print(out)
after = tables(db)
ch = sorted(t for t in set(before) | set(after) if before.get(t) != after.get(t))
ok(set(ch) <= {"roll_runs", "roll_items", "roll_contracts"}, "only the roll tables changed: %s" % ch)
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
items = {r["ticker"]: r for r in c.execute("SELECT * FROM roll_items")}
chosen = lambda t: [r["strike"] for r in c.execute(
    "SELECT strike FROM roll_contracts rc JOIN roll_items ri ON ri.run_id=rc.run_id AND ri.position_id=rc.position_id "
    "WHERE ri.ticker=? AND choice IS NOT NULL ORDER BY choice", (t,))]
ok(chosen("NVDA") == [230.0], "NVDA PMCC: roll to 230 (228.50 fails the width test once the $9.00 buy-back counts)")
ok(chosen("AMZN") == [235.0, 245.0], "AMZN put: 235 then 245 (lowest strike that credits first; 250 is a roll up)")
ok(chosen("GOOGL") == [] and items["GOOGL"]["why_none"].startswith("no roll passes: no roll earns a net credit"),
   "GOOGL: no roll earns a credit -- %s" % items["GOOGL"]["why_none"])
ok("grades D" in (items["VST"]["flags_json"] or ""), "VST: flagged graded D")
ok(chosen("VST") == [] and "no put roll: earnings" in (items["VST"]["why_none"] or ""),
   "VST: no put roll with its report inside 10 days -- %s" % items["VST"]["why_none"])
fee = c.execute("SELECT fee_per_contract FROM roll_runs").fetchone()[0]
nc = c.execute("SELECT net_credit FROM roll_contracts WHERE strike=235 AND choice=1").fetchone()
ok(nc and abs(nc[0] - round((9.05 - 9.00) * 200 - 2 * fee * 2, 2)) < 1e-9,
   "AMZN 235: net credit = sell at the 9.05 bid - buy at the 9.00 ask, x 200, - both legs' fees ($%.2f)" % fee)
ok("Let the short be assigned" in out and "Take the shares" in out and "next floor $228.50" in out,
   "the alternatives and the PMCC's next floor are printed")
ok("XYZ: HELM has no open" in out, "a name with nothing open says so")
c.close()

print("\n-- B. same-day rolls through the import (plan only)")
from helm import brokerfills as B, fillimport as FI
real = sorted(glob.glob(os.path.expanduser("~/mnt/Downloads/Accounts_History*.csv")), key=os.path.getmtime)[-1]
raw = open(real, encoding="utf-8-sig").read().splitlines()
hdr_i = next(i for i, l in enumerate(raw) if l.startswith("Run Date,"))
acct_row = next(l for l in raw[hdr_i + 1:] if "Rollover IRA" in l and "OPENING TRANSACTION" in l)
acct = acct_row.split(",")[2]
foot_i = next(i for i, l in enumerate(raw) if l.startswith("The data and information"))
def row(action, sym, desc, px, qty, amt):
    return ('11/02/2026,Rollover IRA,%s,%s (Cash)," %s",%s,Cash,%s,"%s",0.65,0.01,"",%s,11/03/2026'
            % (acct, action, sym, desc, px, qty, amt))
rows = [row("YOU BOUGHT CLOSING TRANSACTION CALL (NVDA) NVIDIA CORPORATION DEC 04 26 $225 (100 SHS)", "-NVDA261204C225",
            "CALL (NVDA) NVIDIA CORPORATION DEC 04 26 $225 (100 SHS)", "9", "1", "-900.66"),
        row("YOU SOLD OPENING TRANSACTION CALL (NVDA) NVIDIA CORPORATION DEC 11 26 $230 (100 SHS)", "-NVDA261211C230",
            "CALL (NVDA) NVIDIA CORPORATION DEC 11 26 $230 (100 SHS)", "9.6", "-1", "959.34"),
        row("YOU BOUGHT CLOSING TRANSACTION PUT (AMZN) AMAZON.COM INC NOV 20 26 $245 (100 SHS)", "-AMZN261120P245",
            "PUT (AMZN) AMAZON.COM INC NOV 20 26 $245 (100 SHS)", "9", "2", "-1801.32"),
        row("YOU SOLD OPENING TRANSACTION PUT (AMZN) AMAZON.COM INC DEC 11 26 $235 (100 SHS)", "-AMZN261211P235",
            "PUT (AMZN) AMAZON.COM INC DEC 11 26 $235 (100 SHS)", "9.05", "-2", "1808.68")]
tmp = tempfile.mkdtemp(prefix="s146imp-")
path = os.path.join(tmp, "Accounts_History-s146test.csv")
with open(path, "w", encoding="utf-8-sig") as f:
    f.write("\n".join(raw[:hdr_i + 1] + rows + [""] + raw[foot_i:-1] + ["Date downloaded 11/02/2026 05:00 pm"]))
bdb = os.path.join(pristine, "b.db")
os.environ["HELM_DB"] = bdb
cb = sqlite3.connect("file:%s?mode=ro" % bdb, uri=True); cb.row_factory = sqlite3.Row
ira = FI.stored_ira_hash(cb)
exp = B.read_export(path, ira)
p = FI.plan(cb, [exp])
its = p["items"] if isinstance(p, dict) else p.items
for it in its:
    if it.ticker in ("NVDA", "AMZN"):
        print("    %-15s %s" % (it.kind, it.text[:150]))
nv_pos = cb.execute("SELECT id FROM positions WHERE ticker='NVDA' AND strategy='PMCC' AND status='OPEN'").fetchone()[0]
am_pos = cb.execute("SELECT id FROM positions WHERE ticker='AMZN' AND strategy='CSP' AND status='OPEN'").fetchone()[0]
nv = [i for i in its if i.ticker == "NVDA"]
am = [i for i in its if i.ticker == "AMZN"]
ok(any(i.kind == "LEG_CLOSE" for i in nv) and any(i.kind == "OPEN" and i.open["strategy"] == "PMCC_SHORT"
   and i.position_id == nv_pos and not any("width" in f for f in i.open["flags"]) for i in nv),
   "NVDA: the 225 short closes and the 230 call books as the PMCC's next short, width test passing")
ok(any(i.kind == "POSITION_CLOSE" for i in am) and any(i.kind == "OPEN" and i.open["strategy"] == "CSP"
   and i.open["parent"] == am_pos for i in am), "AMZN: the 245 put closes and the 235 put books as a roll of it")
# a brand-new PMCC (long + short the same day) goes through the one width test too
import helm.fillopens as FO
def g(exp, k, d, q, px):
    return (("OPEN", "2026-11-02", "AAPL", exp, "CALL", float(k), d), {"contracts": q, "price": px}, [])
for k, want in ((222.90, True), (223.0, False)):
    it = FO.classify(cb, [g("2027-12-17", 160, "LONG", 1, 62.90), g("2026-12-11", k, "SHORT", 1, 2.0)],
                     {"closed": {}, "closing_legs": set(), "assign_shares": {}})[0]
    ok(it.kind == "OPEN" and any("width test failed" in f for f in it.open["flags"]) == want,
       "a new PMCC with its short at %g: width flag %s" % (k, "raised (at the floor is not above it)" if want else "clear"))
cb.close()
if "--apply" in sys.argv:
    done = FI.apply(p, db_path=None, ira_hash=ira)
    print("applied on the copy:", done)
shutil.rmtree(tmp, ignore_errors=True)
print("\n%d failed" % len(FAILS) if FAILS else "\nALL PASS")
sys.exit(1 if FAILS else 0)
