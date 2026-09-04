#!/usr/bin/env python3
"""W163 (s113) verification -- both ways, each case on a FRESH VACUUM INTO copy
of the live DB (never the live file), fake chain via _fake_yf_boot.py,
prompts piped. Unperturbed control (rank path, no pin) runs LAST.
Usage: HELM_ROOT=... python3 _s113/verify_w163_pin_bypass.py"""
import os, sys, sqlite3, subprocess, shutil, tempfile

ROOT = os.environ.get("HELM_ROOT") or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LIVE = os.path.join(ROOT, "data", "helm.db")
BOOT = os.path.join(ROOT, "_s113", "_fake_yf_boot.py")
WORK = tempfile.mkdtemp(prefix="w163_")
AA_PIN = ["--strike", "55", "--expiry", "2026-10-16", "--long-strike", "47", "--long-expiry", "2026-12-18"]
FAILS = []

def fresh(name):
    p = os.path.join(WORK, name + ".db")
    src = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
    src.execute("VACUUM INTO ?", (p,)); src.close()
    return p

def counts(db):
    c = sqlite3.connect(db)
    out = {t: c.execute("select count(*) from %s" % t).fetchone()[0]
           for t in ("positions", "legs", "entry_snapshots", "lifecycle_events")}
    c.close(); return out

def run(db, argv, stdin, chain="normal"):
    env = dict(os.environ, HELM_ROOT=ROOT, HELM_DB=db, W163_CHAIN=chain, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, BOOT, "AA", "DIAGONAL"] + argv, input=stdin,
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    return r.stdout + r.stderr

def newest_aa(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    p = c.execute("select * from positions where ticker='AA' and strategy='DIAGONAL' "
                  "order by created_at desc limit 1").fetchone()
    legs = [dict(x) for x in c.execute("select * from legs where position_id=? order by direction desc", (p["id"],))]
    snap = c.execute("select count(*) from entry_snapshots where position_id=?", (p["id"],)).fetchone()[0]
    ev = [dict(x) for x in c.execute("select * from lifecycle_events where position_id=?", (p["id"],))]
    c.close(); return dict(p), legs, snap, ev

def check(label, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + label + (("  -- " + str(detail)) if detail and not cond else ""))
    if not cond: FAILS.append(label)

def assert_booked(db, before, out, origin, oob):
    after = counts(db)
    check("wrote 1 position / 2 legs / 1 snapshot / 1 event",
          after == {k: before[k] + n for k, n in (("positions", 1), ("legs", 2), ("entry_snapshots", 1), ("lifecycle_events", 1))}, (before, after))
    check("stdout carries the 'DIAGONAL logged' marker PG requires", "DIAGONAL logged" in out, out[-600:])
    p, legs, snap, ev = newest_aa(db)
    check("book REAL / strategy DIAGONAL / status OPEN", (p["book"], p["strategy"], p["status"]) == ("REAL", "DIAGONAL", "OPEN"), p)
    check("net_premium -3325.00 (5 x (8.98-2.33) x 100, debit negative)", abs(p["net_premium"] - (-3325.0)) < 0.005, p["net_premium"])
    check("max_loss 3325 / spread_width 8 / entry_dte = short dte", p["max_loss"] == 3325.0 and p["spread_width"] == 8.0 and p["entry_dte"] == legs_dte(legs, "SHORT"), (p["max_loss"], p["spread_width"], p["entry_dte"]))
    check("origin_screen == %s" % origin, p["origin_screen"] == origin, p["origin_screen"])
    check("notes %s 'PINNED OUT OF BAND'" % ("carry" if oob else "do NOT carry"), ("PINNED OUT OF BAND" in (p["notes"] or "")) == oob, p["notes"])
    sh = [l for l in legs if l["direction"] == "SHORT"][0]; lo = [l for l in legs if l["direction"] == "LONG"][0]
    check("short leg 55C 2026-10-16 @2.33 x5", (sh["strike"], sh["expiration"], sh["open_price"], sh["contracts"]) == (55.0, "2026-10-16", 2.33, 5), sh)
    check("long leg 47C 2026-12-18 @8.98 x5", (lo["strike"], lo["expiration"], lo["open_price"], lo["contracts"]) == (47.0, "2026-12-18", 8.98, 5), lo)
    check("legs carry entry_delta (chain BS delta, not NULL like the script-logged AA)", sh["entry_delta"] is not None and lo["entry_delta"] is not None, (sh["entry_delta"], lo["entry_delta"]))
    check("OPENED lifecycle event", any(e["event_type"] == "OPENED" for e in ev), ev)
    return p

def legs_dte(legs, direction):
    from datetime import date, datetime
    l = [x for x in legs if x["direction"] == direction][0]
    return (datetime.strptime(l["expiration"], "%Y-%m-%d").date() - date.today()).days

def assert_refused(db, before, out, needle):
    check("refusal text: %r" % needle, needle in out, out[-800:])
    check("wrote NOTHING", counts(db) == before, (before, counts(db)))
    check("no 'DIAGONAL logged' marker", "DIAGONAL logged" not in out)

PROMPTS_PINNED = "5\n2.33\n8.98\ny\n"

print("\n== A. bypass: AA 55/47 not a screened candidate (OI too thin) -> fetched from chain, MANUAL_PIN ==")
db = fresh("a"); b = counts(db)
out = run(db, ["--confirm"] + AA_PIN + ["--expect-contracts", "5", "--expect-net", "6.65"], PROMPTS_PINNED)
check("printed the OUT OF BAND disclosure", "OUT OF BAND" in out, out[-800:])
assert_booked(db, b, out, "MANUAL_PIN", True)

print("\n== A2. bypass with an EMPTY screen (every strike thin) -> allow_empty path, still books ==")
db = fresh("a2"); b = counts(db)
out = run(db, ["--confirm"] + AA_PIN + ["--expect-contracts", "5", "--expect-net", "6.65"], PROMPTS_PINNED, chain="thin")
check("screen reported no candidates", "No screened candidates today" in out, out[-800:])
assert_booked(db, b, out, "MANUAL_PIN", True)

print("\n== A3. control for allow_empty: EMPTY screen, NO pin -> still the W146 gate message, nothing booked ==")
db = fresh("a3"); b = counts(db)
out = run(db, ["--confirm"], "1\n5\n2.33\n8.98\ny\n", chain="thin")
assert_refused(db, b, out, "Error:")

print("\n== B. W7: long strike 48.5 is NOT on the chain -> refuses, writes nothing ==")
db = fresh("b"); b = counts(db)
out = run(db, ["--confirm", "--strike", "55", "--expiry", "2026-10-16", "--long-strike", "48.5", "--long-expiry", "2026-12-18"], PROMPTS_PINNED)
assert_refused(db, b, out, "Pin refused: no $48.5 CALL at 2026-12-18")

print("\n== B2. W7: long expiry not on the chain -> refuses, writes nothing ==")
db = fresh("b2"); b = counts(db)
out = run(db, ["--confirm", "--strike", "55", "--expiry", "2026-10-16", "--long-strike", "47", "--long-expiry", "2026-12-25"], PROMPTS_PINNED)
assert_refused(db, b, out, "has no 2026-12-25 expiry")

print("\n== B3. half a pin (short only) that matches no candidate -> refuses, names the four flags ==")
db = fresh("b3"); b = counts(db)
out = run(db, ["--confirm", "--strike", "55", "--expiry", "2026-10-16"], PROMPTS_PINNED)
assert_refused(db, b, out, "needs all four of")

print("\n== B4. receipt check (W84): --expect-net disagrees with the fills -> refuses AFTER the chain fetch, writes nothing ==")
db = fresh("b4"); b = counts(db)
out = run(db, ["--confirm"] + AA_PIN + ["--expect-contracts", "5", "--expect-net", "6.00"], PROMPTS_PINNED)
assert_refused(db, b, out, "Receipt check failed")

print("\n== C. pin that MATCHES a screened candidate (54/48, rank 1) -> unchanged s111 path, SELL_SCREEN, no out-of-band note ==")
db = fresh("c"); b = counts(db)
out = run(db, ["--confirm", "--strike", "54", "--expiry", "2026-10-16", "--long-strike", "48", "--long-expiry", "2026-12-18"], "2\n2.70\n8.15\ny\n")
after = counts(db)
check("booked", after["positions"] == b["positions"] + 1 and after["legs"] == b["legs"] + 2, (b, after))
p, legs, snap, ev = newest_aa(db)
check("origin SELL_SCREEN, notes without OUT OF BAND", p["origin_screen"] == "SELL_SCREEN" and "OUT OF BAND" not in (p["notes"] or ""), (p["origin_screen"], p["notes"]))
check("no bypass disclosure printed", "OUT OF BAND" not in out)
check("legs 54C/48C x2, net -1090", sorted((l["strike"], l["open_price"]) for l in legs) == [(48.0, 8.15), (54.0, 2.7)] and abs(p["net_premium"] + 1090.0) < 0.005, (legs, p["net_premium"]))

print("\n== D. CONTROL, LAST: rank path, no pin, rank 1 -> books as before ==")
db = fresh("d"); b = counts(db)
out = run(db, ["--confirm"], "1\n1\n2.70\n8.15\ny\n")
after = counts(db)
check("booked 1 position / 2 legs / 1 snapshot / 1 event", after == {k: b[k] + n for k, n in (("positions", 1), ("legs", 2), ("entry_snapshots", 1), ("lifecycle_events", 1))}, (b, after))
p, legs, snap, ev = newest_aa(db)
check("origin SELL_SCREEN, no out-of-band note, no disclosure printed", p["origin_screen"] == "SELL_SCREEN" and "OUT OF BAND" not in (p["notes"] or "") and "OUT OF BAND" not in out, (p["origin_screen"], p["notes"]))
check("rank-1 pair booked with the typed fills", sorted(l["open_price"] for l in legs) == [2.7, 8.15], legs)

print("\nlive DB untouched:", counts(LIVE))
print("\nRESULT: %s (%d failures)" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
for f in FAILS: print("   -", f)
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(1 if FAILS else 0)
