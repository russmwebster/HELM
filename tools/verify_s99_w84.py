# -*- coding: utf-8 -*-
"""HELM-152 (W84) -- the fill guard. Runs against pre-change code too (getattr)."""
import os, sys, io, re, sqlite3, shutil, glob
ROOT = os.environ.get("HELM_ROOT", "/Users/russmacbookpro/Projects/helm")
sys.path.insert(0, ROOT)
PASS, FAIL = [], []
def check(n, fn):
    try: ok, d = fn()
    except Exception as e: FAIL.append((n, "EXC %s: %s" % (type(e).__name__, e))); return
    (PASS if ok else FAIL).append((n, d))

try:
    from helm import fill_guard as fg
except Exception:
    fg = None

def t_module(): return fg is not None, "helm.fill_guard present"
check("the guard module exists", t_module)

def t_match():
    if fg is None: return False, "no module"
    return fg.compare(10, 1.63, 10, 1.63) is None, "identical booking passes"
check("a booking that matches the form is allowed", t_match)

def t_gm():
    """The actual failure: right price, half the size."""
    if fg is None: return False, "no module"
    r = fg.compare(10, 1.63, 5, 1.63)
    return (r is not None and "10" in r and "5" in r), repr(r)
check("the GM case is refused and names both numbers", t_gm)

def t_price():
    if fg is None: return False, "no module"
    r = fg.compare(1, 1.63, 1, 3.00)
    return r is not None and "1.63" in r, repr(r)
check("a wrong fill price is refused", t_price)

def t_both():
    if fg is None: return False, "no module"
    r = fg.compare(10, 1.63, 5, 3.00)
    return r is not None and " and " in r, repr(r)
check("both wrong -> both reported", t_both)

def t_none():
    if fg is None: return False, "no module"
    return (fg.compare(None, None, 5, 3.0) is None
            and fg.compare(None, 1.63, 7, 1.63) is None), "unstated halves are not checked"
check("no expectation means no check (the interactive CLI is untouched)", t_none)

def t_tol():
    if fg is None: return False, "no module"
    return (fg.compare(1, 1.63, 1, 1.6301) is None
            and fg.compare(1, 1.63, 1, 1.64) is not None), "half-cent tolerance"
check("float noise passes, a real cent does not", t_tol)

def t_refusal():
    if fg is None: return False, "no module"
    return "Nothing has been recorded" in fg.refusal_text("x"), "refusal states nothing was written"
check("the refusal says nothing was recorded", t_refusal)

# ---- open_cmd surface
oc = io.open(os.path.join(ROOT, "helm", "cli", "open_cmd.py"), encoding="utf-8").read()
def t_nofallback():
    n = len(re.findall(r"num_contracts = suggested(_n)?\s*\n", oc))
    return n == 0, "silent sizing fallbacks remaining: %d" % n
check("no flow silently substitutes its own contract count", t_nofallback)
def t_refusals():
    return oc.count("HELM-152 (W84): REFUSE") == 3, "%d refusals" % oc.count("HELM-152 (W84): REFUSE")
check("all three flows refuse instead (single, spread, condor)", t_refusals)
def t_guarded():
    return oc.count("if not _expectation_holds(") == 3, "%d guard calls" % oc.count("if not _expectation_holds(")
check("the receipt check runs before the write in all three flows", t_guarded)
def t_flags():
    return ('"--expect-contracts"' in oc and '"--expect-fill"' in oc), "flags parsed"
check("--expect-contracts / --expect-fill are parsed", t_flags)
def t_before_write():
    """The guard must sit ABOVE the confirmation, so a mismatch never reaches
    open_position_with_snapshot."""
    i = oc.find("if not _expectation_holds(")
    j = oc.find("open_position_with_snapshot(", i)
    return (i > 0 and j > i), "guard at %d, first write at %d" % (i, j)
check("the guard precedes the write (refuse, not flag-after)", t_before_write)

# ---- PG
en = io.open("/Users/russmacbookpro/Projects/helm-pg/helm_engine.py", encoding="utf-8").read()
def t_pg():
    return ('"--expect-contracts"' in en and '"--expect-fill"' in en), "PG passes the expectation"
check("PG tells the engine what the form asked for", t_pg)

# ---- snapshot + schema + live DB
sn = io.open(os.path.join(ROOT, "helm", "cli", "entry_snapshot.py"), encoding="utf-8").read()
def t_snap():
    return (sn.count("bid_ask_spread_pct, bid, ask,") == 2
            and 'bid=contract.get("bid")' in sn and 'bid=p_lg.get("bid")' in sn), "both writers"
check("bid/ask captured on both snapshot writers", t_snap)
def t_marks():
    m = re.search(r"\)\s*VALUES\s*\(([?,]+)\)", sn)
    return m.group(1).count("?") == 27, "%d placeholders" % m.group(1).count("?")
check("the INSERT placeholder count matches the columns", t_marks)
def t_schema():
    sc = io.open(os.path.join(ROOT, "helm", "schema.sql"), encoding="utf-8").read()
    return ("ADD COLUMN bid REAL;" in sc and "ADD COLUMN ask REAL;" in sc), "schema.sql carries them"
check("a rebuilt database would have the columns (the W4 rule)", t_schema)
def t_live():
    con = sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True)
    cols = [r[0] for r in con.execute("select name from pragma_table_info('entry_snapshots')")]
    return ("bid" in cols and "ask" in cols), "live columns present: %s" % ([c for c in cols if c in ("bid","ask")],)
check("the LIVE database has the columns (migration ran)", t_live)

print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
for n, d in PASS: print("  ok   %s -- %s" % (n, d))
for n, d in FAIL: print("  FAIL %s -- %s" % (n, d))
sys.exit(1 if FAIL else 0)
