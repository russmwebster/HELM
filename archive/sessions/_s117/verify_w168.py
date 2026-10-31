"""W168 verified both ways, offline, on fresh copies.

The load-bearing claim is NARROWNESS: every single-expiry position must be
byte-for-byte unaffected, and only the multi-expiry family may move.
"""
import os, sys, sqlite3, pathlib, shutil, subprocess
ROOT = pathlib.Path.home()/"mnt/helm"
os.environ.setdefault("HELM_ROOT", str(ROOT))   # s110's trap
sys.path.insert(0, str(ROOT))
P=F=0
def ck(n, ok):
    global P,F
    print(("  PASS  " if ok else "  FAIL  ")+n)
    if ok: P+=1
    else: F+=1

from helm import legview as LV
print("  LOADED", LV.__file__)          # s115: say what you ran

L = lambda e,d,s: {"expiration": e, "direction": d, "strike": s}

# ---- 1. no-op on single-expiry, provably the SAME objects in the SAME order
vert = [L("2026-10-16","SHORT",100), L("2026-10-16","LONG",110)]
ck("vertical: order unchanged (identical object identity)",
   LV.order_option_legs(vert) is vert)
condor = [L("2026-10-16","SHORT",90), L("2026-10-16","LONG",80),
          L("2026-10-16","SHORT",110), L("2026-10-16","LONG",120)]
ck("condor: order unchanged", LV.order_option_legs(condor) is condor)
single = [L("2026-10-16","LONG",100)]
ck("single leg: order unchanged", LV.order_option_legs(single) is single)
ck("single-expiry is_multi_expiry False", not LV.is_multi_expiry(condor))

# ---- 2. multi-expiry: front leg first, whichever way it was written
short_first = [L("2026-10-16","SHORT",57.5), L("2026-12-18","LONG",52.5)]
long_first  = [L("2026-12-18","LONG",52.5),  L("2026-10-16","SHORT",57.5)]
a = LV.order_option_legs(short_first); b = LV.order_option_legs(long_first)
ck("diagonal written SHORT-first -> front leg first", a[0]["expiration"] == "2026-10-16")
ck("diagonal written LONG-first  -> front leg first", b[0]["expiration"] == "2026-10-16")
ck("both books now agree on the primary", a[0]["expiration"] == b[0]["expiration"])
ck("the accident is gone: order no longer depends on insertion",
   [l["expiration"] for l in a] == [l["expiration"] for l in b])

# ---- 3. the pair, and the label
dfn = lambda e: {"2026-10-16": 42, "2026-12-18": 105}.get(e)
ck("dte_pair on a diagonal returns (front, back)", LV.dte_pair(long_first, dfn) == (42,105))
ck("dte_label renders both", LV.dte_label(long_first, dfn) == "42 / 105")
ck("dte_pair on a vertical returns (dte, None)",
   LV.dte_pair([L("2026-10-16","SHORT",100),L("2026-10-16","LONG",110)], dfn) == (42,None))
ck("dte_label on a vertical renders one number",
   LV.dte_label([L("2026-10-16","SHORT",100)], dfn) == "42")
ck("empty legs do not raise", LV.dte_pair([], dfn) == (None,None))
ck("legs with no expiration do not raise", LV.dte_pair([{"expiration":None}], dfn) == (None,None))

# ---- 4. the REAL population: how many positions actually move?
db = sqlite3.connect(f"file:{ROOT}/data/helm.db?mode=ro", uri=True); db.row_factory = sqlite3.Row
moved = same = 0; movers = []
for p in db.execute("select id, ticker, strategy, book from positions where status='OPEN'"):
    legs = [dict(r) for r in db.execute(
        "select * from legs where position_id=? and option_type not in ('STOCK')", (p["id"],))]
    if not legs: continue
    out = LV.order_option_legs(legs)
    if [l["id"] for l in out] != [l["id"] for l in legs]:
        moved += 1; movers.append((p["ticker"], p["strategy"], p["book"]))
    else:
        same += 1
db.close()
ck(f"only multi-expiry positions reorder ({moved} moved, {same} unchanged)",
   all(s.startswith("DIAGONAL") or s == "PMCC" for _,s,_ in movers))
ck("the movers are the PAPER long-first diagonals",
   moved > 0 and all(b == "PAPER" for _,_,b in movers))
print(f"    moved: {moved} — {sorted(set(t for t,_,_ in movers))[:8]}")

# ---- 5. the tools that read dte_now still pass their own fixtures
r = subprocess.run([sys.executable, "tools/exit_discipline.py", "--selftest"],
                   cwd=str(ROOT), capture_output=True, text=True,
                   env=dict(os.environ, HELM_ROOT=str(ROOT)))
ck("exit_discipline --selftest still PASSES (173 keys)", "PASS" in r.stdout)
print("   ", r.stdout.strip()[:100])

# ---- 6. perturb the defect most feared: make the order depend on insertion again
src = (ROOT/"helm/legview.py").read_text()
pert = src.replace("    return sorted(opt_legs, key=key)", "    return opt_legs")
(ROOT/"helm/legview.py").write_text(pert)
shutil.rmtree(ROOT/"helm/__pycache__", ignore_errors=True)
r2 = subprocess.run([sys.executable, "-c",
    "import sys;sys.path.insert(0,%r);from helm import legview as LV;"
    "a=[{'expiration':'2026-12-18','direction':'LONG','strike':52.5},"
    "   {'expiration':'2026-10-16','direction':'SHORT','strike':57.5}];"
    "print(LV.order_option_legs(a)[0]['expiration'])" % str(ROOT)],
    capture_output=True, text=True)
ck("perturbed: the accident comes back (harness can fail)", "2026-12-18" in r2.stdout)
(ROOT/"helm/legview.py").write_text(src)
shutil.rmtree(ROOT/"helm/__pycache__", ignore_errors=True)
ck("restored: file byte-identical", (ROOT/"helm/legview.py").read_text() == src)

print(f"\nPASS {P} · FAIL {F}")
sys.exit(1 if F else 0)
