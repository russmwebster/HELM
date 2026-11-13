"""s157b: the import's add-ons, on database copies, through the real CLI (`helm fills`).
Usage: python3 -B _s157b/verify_add_on.py LIVE_DB DOWNLOAD.csv   (copies go to /tmp/s157b/v; nothing live written)
Says which code it loaded. Synthetic downloads = the real one with extra rows on top (account kept, never printed)."""
import os, re, shutil, sqlite3, subprocess, sys, csv, io
LIVE, DL = sys.argv[1], sys.argv[2]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = "/tmp/s157b/v"; os.makedirs(W, exist_ok=True)
import helm.fillimport as FI
print("code:", FI.__file__, "add-on aware:", hasattr(FI, "_add_on_item"))
PID, LID = "PFE-CSP-20261110-F23856", "PFE-CSP-20261110-F23856-SH-81D8"
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)

def fresh(name):
    p = os.path.join(W, name + ".db")
    for x in (p, p + "-wal", p + "-shm"):
        if os.path.exists(x): os.remove(x)
    sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True).execute("VACUUM INTO ?", (p,))
    return p

lines = open(DL, encoding="utf-8-sig").read().split("\n")
hdr_i = [i for i, l in enumerate(lines) if l.startswith("Run Date,")][0]
tmpl = [l for l in lines if "PFE261218P28" in l and l.startswith("11/12/2026")][0]
def row(date, action, sym, desc, price, qty, comm, fees, amount):
    f = next(csv.reader([tmpl]))
    f[0], f[3], f[4], f[5] = date, action, " " + sym, desc
    f[7], f[8], f[9], f[10], f[12], f[13] = str(price), str(qty), str(comm), str(fees), str(amount), date
    out = io.StringIO(); csv.writer(out, lineterminator="").writerow(f); return out.getvalue()
def download(name, extra, drop_pfe_1112=False):
    body = [l for l in lines if not (drop_pfe_1112 and l == tmpl)]
    hi = [i for i, l in enumerate(body) if l.startswith("Run Date,")][0]
    body = body[:hi + 1] + extra + body[hi + 1:]
    p = os.path.join(W, "Accounts_History-s157b-" + name + ".csv"); open(p, "w", encoding="utf-8-sig").write("\n".join(body)); return p
PFE_D = "PUT (PFE) PFIZER INC DEC 18 26 $28 (100 SHS)"
def pfe_close(date, qty, price):
    return row(date, "YOU BOUGHT CLOSING TRANSACTION %s (Cash)" % PFE_D, "-PFE261218P28", PFE_D, price, qty, 6.5 * qty / 10, 0.13,
               round(-price * 100 * qty - 6.5 * qty / 10 - 0.13, 2))

def fills(db, dl, apply=False):
    env = dict(os.environ, HELM_DB=db, COLUMNS="250", HELM_ROOT=ROOT)
    a = [sys.executable, "-B", os.path.join(ROOT, "helm.py"), "fills", "--file", dl] + (["--apply", "--yes"] if apply else [])
    import time; time.sleep(1.1)
    r = subprocess.run(a, env=env, capture_output=True, text=True, timeout=170, cwd=ROOT)
    return re.sub(r"\d{8,}", "X", r.stdout + r.stderr)

def leg(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    return c.execute("SELECT l.contracts, l.open_price, l.status, p.net_premium, p.total_contracts, p.status ps, "
                     "p.realized_pnl, p.exit_reason FROM legs l JOIN positions p ON p.id=l.position_id WHERE l.id=?", (LID,)).fetchone()

def tables(db):
    c = sqlite3.connect(db); out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        out[t] = set(c.execute("SELECT * FROM '%s'" % t))
    return out
def changed(a, b):
    return sorted(t for t in set(a) | set(b) if a.get(t) != b.get(t))

# 1. the real download: PFE adds 10 at 0.40
db = fresh("real"); t0 = tables(db)
out = fills(db, DL)
ok("adds to the open position (opened 2026-11-10): 10 -> 20 contracts, entry 0.44 -> 0.42 (average), premium $440.00 -> $840.00; 50% buy-back 0.21" in out, "real: dry run shows the PFE add-on")
ok(tables(db) == t0, "real: dry run writes nothing")
out = fills(db, DL, apply=True)
L = leg(db)
ok((L["contracts"], round(L["open_price"], 6), L["net_premium"], L["total_contracts"]) == (20, 0.42, 840.0, 20), "real: leg 20 @ 0.42, premium 840, total 20")
ch = changed(t0, tables(db))
ok(ch == ["broker_fills", "helm_events", "legs", "positions"], "real: only broker_fills, helm_events, legs, positions change (%s)" % ch)
c = sqlite3.connect(db)
ok(c.execute("SELECT COUNT(*) FROM leg_checks WHERE leg_id=?", (LID,)).fetchone()[0] ==
   sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True).execute("SELECT COUNT(*) FROM leg_checks WHERE leg_id=?", (LID,)).fetchone()[0],
   "real: the leg's readings kept")
from helm import netpnl
c.row_factory = sqlite3.Row
ok(netpnl.position(c, PID)["fees"] == 13.26, "real: both sales' fees count ($13.26)")
out2 = fills(db, DL)
ok("Nothing to write." in out2 and "PFE" not in out2, "real: second run proposes nothing and reports nothing on PFE")

# 2. then all 20 bought back at 0.21 -> one close, profit target
d2 = download("close20", [pfe_close("11/13/2026", 20, 0.21)])
out = fills(db, d2, apply=True)
L = leg(db)
ok(L["status"] == "CLOSED" and L["ps"] == "CLOSED" and abs(L["realized_pnl"] - 420.0) < 0.01 and L["exit_reason"] == "PROFIT_TARGET",
   "close 20: one close, +$420, profit target (%s %s %s)" % (L["ps"], L["realized_pnl"], L["exit_reason"]))

# 3. from the added position, 10 bought back -> a clean split 10/10
db = fresh("split"); fills(db, DL, apply=True)
d3 = download("close10", [pfe_close("11/13/2026", 10, 0.21)])
out = fills(db, d3, apply=True)
c = sqlite3.connect(db)
ok(c.execute("SELECT contracts FROM legs WHERE id=?", (LID,)).fetchone()[0] == 10 and
   c.execute("SELECT COUNT(*) FROM positions WHERE tags LIKE ?", ("%split_from:" + PID + "%",)).fetchone()[0] == 1,
   "close 10: split -- 10 stay open, a closed piece of 10")
out = fills(db, d3)
ok("Nothing to write." in out and "PFE" not in out, "close 10: next run quiet on PFE")

# 4. add-on and a close in the same download: add booked, close held for the next run, then booked
db = fresh("same"); d4 = download("addclose", [pfe_close("11/13/2026", 20, 0.21)])
out = fills(db, d4, apply=True)
L = leg(db)
ok(L["contracts"] == 20 and L["status"] == "OPEN" and "run the import again" in out, "same download: add-on booked, close held")
out = fills(db, d4, apply=True)
L = leg(db)
ok(L["ps"] == "CLOSED" and abs(L["realized_pnl"] - 420.0) < 0.01, "same download: the rerun books the close (+$420)")

# 5. same-day extra sale stays a report (unchanged behaviour)
db = fresh("sameday")
d5 = download("sameday", [row("11/10/2026", "YOU SOLD OPENING TRANSACTION %s (Cash)" % PFE_D, "-PFE261218P28", PFE_D, 0.43, -5, 3.25, 0.07, 211.68)],
              drop_pfe_1112=True)
out = fills(db, d5)
ok("ADD_ON" not in out and "adds to the open position" not in out and "check by hand" in out, "same day: not added, reported")

# 6. a PMCC short sold again is not added (reported)
db = fresh("pmcc"); NKE_D = "CALL (NKE) NIKE INC CLASS B COM DEC 18 26 $42.5 (100 SHS)"
d6 = download("pmcc", [row("11/12/2026", "YOU SOLD OPENING TRANSACTION %s (Margin)" % NKE_D, "-NKE261218C42.5", NKE_D, 0.70, -1, 0.65, 0.01, 69.34)])
out = fills(db, d6); t0 = tables(db)
ok("NKE" in out and "adds to the open position" not in out.split("NKE")[0][-5:] + out[out.find("NKE"):out.find("NKE") + 400], "PMCC short: not added")
fills(db, d6, apply=True)
ok(sqlite3.connect(db).execute("SELECT SUM(contracts) FROM legs l JOIN positions p ON p.id=l.position_id WHERE p.ticker='NKE' "
                               "AND p.status='OPEN' AND l.direction='SHORT'").fetchone()[0] == 1, "PMCC short: NKE short stays 1 contract")

# 7. a covered call with no free shares: reported, not added
db = fresh("cc"); c = sqlite3.connect(db)
c.execute("INSERT INTO positions (id, account_id, strategy, ticker, status, opened_at, total_contracts, net_premium, book, created_at, updated_at) "
          "VALUES ('GOOGL-CC-TEST', 'fidelity_9e60c8', 'COVERED_CALL', 'GOOGL', 'OPEN', '2026-11-10T16:00:00', 1, 300.0, 'REAL', '2026-11-10', '2026-11-10')")
c.execute("INSERT INTO legs (id, position_id, leg_role, option_type, direction, strike, expiration, contracts, multiplier, open_price, open_date, status, created_at) "
          "VALUES ('GOOGL-CC-TEST-SH', 'GOOGL-CC-TEST', 'SHORT_CALL', 'CALL', 'SHORT', 330.0, '2026-12-18', 1, 100, 3.0, '2026-11-10', 'OPEN', '2026-11-10')")
c.commit()
G_D = "CALL (GOOGL) ALPHABET INC CAP STK DEC 18 26 $330 (100 SHS)"
d7 = download("cc", [row("11/12/2026", "YOU SOLD OPENING TRANSACTION %s (Cash)" % G_D, "-GOOGL261218C330", G_D, 2.5, -1, 0.65, 0.01, 249.34)])
out = fills(db, d7)
gl = [l for l in out.split("\n") if "GOOGL 2026-12-18 CALL 330" in l]
ok(len(gl) == 1 and "0 free GOOGL shares" in gl[0] and "adds to" not in gl[0], "covered call: no free shares -> reported")
c = sqlite3.connect(db); c.execute("UPDATE stock_positions SET shares=200 WHERE ticker='GOOGL'"); c.commit()
out = fills(db, d7, apply=True)
c = sqlite3.connect(db)
ok(c.execute("SELECT contracts, round(open_price,4) FROM legs WHERE id='GOOGL-CC-TEST-SH'").fetchone() == (2, 2.75),
   "covered call with 100 free shares: added, 2 @ 2.75")

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
