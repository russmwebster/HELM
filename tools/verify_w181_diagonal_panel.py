"""W181 -- the diagonal has its own display group, and the CLI and PG cannot drift apart.

Usage:
    HELM_ROOT=~/Projects/helm python3 tools/verify_w181_diagonal_panel.py
    HELM_ROOT=~/Projects/helm python3 tools/verify_w181_diagonal_panel.py --control <check_cmd .bak>
      (control: expect the DIAGONAL cases to FAIL -- it maps to OTHER)

Checks, in order:
  0  the import  -- helm_engine imports in a CLEAN interpreter, engine present
                    AND absent. This case is here because its absence cost the
                    board: W181's first cut hoisted `from helm import posview`
                    to module level in helm_engine.py, where every other helm
                    import is deliberately lazy (helm reaches sys.path only via
                    _ensure_path, and PG is built to run with no engine at all).
                    helm_engine then failed to import and EVERY PG PAGE WENT
                    BLANK, 2026-09-23. The first harness passed 32 cases
                    because it imported helm_engine with the path already set
                    -- it never once started the way PG starts.
  A  the shared map -- posview is ONE object, imported by both surfaces
  B  the taxonomy   -- DIAGONAL / DIAGONAL_PUT / PMCC group together, others unmoved
  C  completeness   -- every family in FAMILY_ORDER has a CLI renderer AND a PG column set
  D  the reads      -- posview's diagonal helpers against arithmetic fixtures
  E  live sanity    -- every open DIAGONAL in the book classifies and prices without error

Prints every file it loaded. Reads the live database read-only; writes nothing.
"""
import sys, os, sqlite3, importlib, re
from importlib.machinery import SourceFileLoader

ROOT = os.environ.get("HELM_ROOT") or os.getcwd()
PG = os.path.join(os.path.dirname(ROOT), "helm-pg")
sys.path.insert(0, ROOT)

control = None
if "--control" in sys.argv:
    control = sys.argv[sys.argv.index("--control") + 1]

ok = True
def chk(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}{('  -- ' + str(detail)) if detail else ''}")

from helm import posview as P
print("posview   :", os.path.abspath(P.__file__))
if control:
    spec = importlib.util.spec_from_loader("cc", SourceFileLoader("cc", control))
    cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
else:
    import helm.cli.check_cmd as cc
print("check_cmd :", os.path.abspath(cc.__file__))

E = None
if os.path.isdir(PG):
    sys.path.insert(0, PG)
    try:
        import helm_engine as E
        print("helm_engine:", os.path.abspath(E.__file__))
    except Exception as e:
        print("helm_engine: NOT IMPORTABLE --", e)

# ---- 0. the import, the way PG actually starts ---------------------------
# A subprocess, because this interpreter already has HELM_ROOT on sys.path --
# which is exactly how the first harness fooled itself.
import subprocess, textwrap
if os.path.isdir(PG):
    probe = textwrap.dedent("""
        import sys; sys.path.insert(0, %r)
        import helm_engine as E
        print("OK", E.engine_available())
    """) % PG
    for label, env_extra in (("engine present", {"HELM_ROOT": ROOT}),
                             ("engine ABSENT", {"HELM_ROOT": os.path.join(ROOT, "__nonexistent__")})):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env.update(env_extra)
        r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                           env=env, cwd=PG)
        chk(f"0  helm_engine imports in a clean interpreter -- {label}",
            r.returncode == 0 and r.stdout.startswith("OK"),
            (r.stderr.strip().splitlines() or [""])[-1])

# ---- A. one map, not two -------------------------------------------------
chk("A1 CLI uses posview's order (same object)", cc._FAMILY_ORDER is P.FAMILY_ORDER)
if E:
    chk("A2 PG uses posview's order (same object)", E.FAMILY_ORDER is P.FAMILY_ORDER)
    chk("A3 CLI and PG agree on every strategy name",
        all(cc._family(s) == E._family(s) for s in
            ("CSP", "CASH_SECURED_PUT", "DIAGONAL", "DIAGONAL_PUT", "PMCC", "IRON_CONDOR",
             "LONG_CALL", "BEAR_CALL_SPREAD", "BULL_PUT_SPREAD", "LONG_STRADDLE", "", None)))

# ---- B. taxonomy ---------------------------------------------------------
for s in ("DIAGONAL", "DIAGONAL_PUT", "PMCC"):
    chk(f"B  {s} -> DIAGONAL", cc._family(s) == "DIAGONAL", cc._family(s))
for s, want in (("CSP", "CSP"), ("IRON_CONDOR", "IC"), ("LONG_CALL", "LONG_CALL"),
                ("BEAR_CALL_SPREAD", "CREDIT_SPREAD"), ("LONG_STRADDLE", "OTHER"),
                (None, "OTHER")):
    chk(f"B  {str(s):17s} -> {want} (unmoved)", cc._family(s) == want, cc._family(s))

# ---- C. completeness -----------------------------------------------------
chk("C1 every family has a CLI renderer",
    all(f in cc._GROUP_RENDER for f in cc._FAMILY_ORDER),
    [f for f in cc._FAMILY_ORDER if f not in cc._GROUP_RENDER])
chk("C2 every family has a META entry",
    all(f in cc._FAMILY_META for f in cc._FAMILY_ORDER))
tpl = os.path.join(PG, "templates", "positions.html")
if os.path.exists(tpl):
    src = open(tpl, encoding="utf-8").read()
    blk = src[src.index("const COLS = {"):]
    blk = blk[:blk.index("\n};")]
    keys = set(re.findall(r"^\s{2}([A-Z_]+):", blk, re.M))
    chk("C3 every family has a PG column set", set(P.FAMILY_ORDER) <= keys,
        sorted(set(P.FAMILY_ORDER) - keys))

# ---- D. the reads, on arithmetic fixtures --------------------------------
def leg(role, direction, strike, exp, status="OPEN", op=None, cp=None, n=1):
    return {"id": f"{role}-{strike}", "leg_role": role, "direction": direction,
            "option_type": "CALL", "strike": strike, "expiration": exp,
            "status": status, "open_price": op, "close_price": cp,
            "contracts": n, "multiplier": 100}

both = [leg("LONG_CALL", "LONG", 47, "2026-12-18", op=8.98, n=5),
        leg("SHORT_CALL", "SHORT", 55, "2026-10-16", op=2.33, n=5)]
harvested = [leg("LONG_CALL", "LONG", 47, "2026-12-18", op=8.98, n=5),
             leg("SHORT_CALL", "SHORT", 55, "2026-10-16", "CLOSED", op=2.33, cp=0.17, n=5)]
gone = [leg("LONG_CALL", "LONG", 47, "2026-12-18", "CLOSED", op=8.98, cp=3.30, n=5),
        leg("SHORT_CALL", "SHORT", 55, "2026-10-16", "CLOSED", op=2.33, cp=0.17, n=5)]

chk("D1 both legs live -> SHORT ON", P.diagonal_state(both) == "SHORT ON", P.diagonal_state(both))
chk("D2 short bought back -> LONG ONLY", P.diagonal_state(harvested) == "LONG ONLY", P.diagonal_state(harvested))
chk("D3 no open leg -> CLOSED", P.diagonal_state(gone) == "CLOSED", P.diagonal_state(gone))
chk("D4 open short found while SHORT ON", (P.open_short_leg(both) or {}).get("strike") == 55)
chk("D5 no open short once harvested", P.open_short_leg(harvested) is None)
chk("D6 long found in both states",
    (P.open_long_leg(both) or {}).get("strike") == 47 and (P.open_long_leg(harvested) or {}).get("strike") == 47)
chk("D7 captured 2.33 -> 0.17 is 92.7%", abs(P.captured_pct(P.open_short_leg(both), 0.17) - 92.7) < 0.1,
    P.captured_pct(P.open_short_leg(both), 0.17))
chk("D8 captured is NEGATIVE when run over", P.captured_pct(P.open_short_leg(both), 21.12) < 0)
chk("D9 no mark -> None, never a guess", P.captured_pct(P.open_short_leg(both), None) is None)
chk("D10 rent 0.00 while the short is still open", P.rent_collected(both) == 0.0, P.rent_collected(both))
chk("D11 rent = (2.33-0.17)*5*100 once closed", P.rent_collected(harvested) == 1080.0, P.rent_collected(harvested))
chk("D12 basis = 8.98*5*100 - 1080", P.effective_basis(harvested) == 3410.0, P.effective_basis(harvested))
chk("D13 basis before any rent is the raw debit", P.effective_basis(both) == 4490.0, P.effective_basis(both))
chk("D14 no open long -> basis None", P.effective_basis(gone) is None)
chk("D15 shorts_sold counts closed AND open", P.shorts_sold(harvested) == 1 and P.shorts_sold(both) == 1)

# ---- E. live sanity ------------------------------------------------------
db = os.path.join(ROOT, "data", "helm.db")
c = sqlite3.connect("file:" + db + "?mode=ro", uri=True); c.row_factory = sqlite3.Row
pos = [dict(r) for r in c.execute("select * from positions where strategy in ('DIAGONAL','DIAGONAL_PUT','PMCC') and status='OPEN'")]
bad = []
states = {}
for p in pos:
    legs = [dict(r) for r in c.execute("select * from legs where position_id=?", (p["id"],))]
    try:
        st = P.diagonal_state(legs)
        states[st] = states.get(st, 0) + 1
        P.effective_basis(legs); P.rent_collected(legs); P.shorts_sold(legs)
        if cc._family(p["strategy"]) != "DIAGONAL":
            bad.append((p["ticker"], "not grouped as DIAGONAL"))
    except Exception as ex:
        bad.append((p["ticker"], repr(ex)))
chk(f"E1 all {len(pos)} open diagonals classify and price", not bad, bad[:3])
chk("E2 states seen are only the three", set(states) <= {"SHORT ON", "LONG ONLY", "CLOSED"}, states)
print(f"      states: {states}")

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
