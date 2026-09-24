"""tools/verify_w180_step6.py -- W180 step 6: sell the next short against a
diagonal's long, and the readers a second short leg reaches.

    HELM_ROOT=<tree> HELM_DB=<copy.db> HOME=<home with .helm_profile> \
        python3 tools/verify_w180_step6.py

Imports the engine FROM HELM_ROOT and says which files it loaded. WRITES to
HELM_DB -- point it at a VACUUM INTO copy, never the live file. The chain is
FAKED (a stand-in yfinance): the harness controls its own quotes, so a case
never depends on the market (standing rule).

Feared defects, each with a case that must fail if it comes back:
  * W82 -- the netting dict keyed (type, strike): a re-sold short at the SAME
    strike as the one bought back takes the other's mark  -> same_strike_pnl
  * a bought-back short counted as the nearer wall        -> buffer_open_only
  * the re-sell written as a second position (W184)       -> one_position
  * the chain_contract lift changing what the W163 pin returns -> pin_equiv
"""
import os, sys, types, sqlite3, hashlib, importlib, io
from contextlib import redirect_stdout

ROOT = os.environ["HELM_ROOT"]
sys.path.insert(0, ROOT)
FAILS = []


def case(name, got, want):
    ok = got == want
    print("%s  %-22s got %r%s" % ("PASS" if ok else "FAIL", name, got,
                                  "" if ok else "   want %r" % (want,)))
    if not ok:
        FAILS.append(name)


def h(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:12]


# ---- a stand-in yfinance: one chain, controlled -------------------------------
import pandas as pd

CHAIN = {  # (exp) -> calls rows
    "2026-10-16": [(55.0, 0.15, 0.19, 0.17, 0.60, 900)],
    "2026-11-20": [(50.0, 0.95, 1.05, 1.00, 0.55, 500), (55.0, 0.40, 0.50, 0.45, 0.52, 700),
                   (60.0, 0.10, 0.14, 0.12, 0.50, 300)],
    "2026-12-18": [(47.0, 3.10, 3.30, 3.20, 0.50, 400)],
}


class _TK:
    def __init__(self, t):
        self.fast_info = types.SimpleNamespace(last_price=44.58)
        self.options = tuple(CHAIN)

    def option_chain(self, exp):
        rows = CHAIN[exp]
        df = pd.DataFrame(rows, columns=["strike", "bid", "ask", "lastPrice",
                                         "impliedVolatility", "openInterest"])
        return types.SimpleNamespace(calls=df, puts=df.iloc[0:0])

    def history(self, period="5d"):
        return pd.DataFrame({"Close": [44.58]})


sys.modules["yfinance"] = types.SimpleNamespace(Ticker=_TK)

from helm.cli import resell_cmd as R, diagonal as DG, check_cmd as CC
from helm import thesis as TH, posview as PV, exit_flags as EF
from helm.config import DB_PATH
for m in (R, DG, CC, TH, EF):
    print("LOADED %s  %s" % (m.__file__, h(m.__file__)))
print("DB %s" % DB_PATH)
assert "copy" in str(DB_PATH) or os.environ.get("VERIFY_ALLOW"), "point HELM_DB at a copy"

c = sqlite3.connect(str(DB_PATH))
AA = c.execute("select id from positions where ticker='AA' and book='REAL' "
               "and status='OPEN' and strategy='DIAGONAL'").fetchone()[0]
np0 = c.execute("select net_premium from positions where id=?", (AA,)).fetchone()[0]
nleg0 = c.execute("select count(*) from legs where position_id=?", (AA,)).fetchone()[0]
npos0 = c.execute("select count(*) from positions where ticker='AA'").fetchone()[0]


def run(*argv):
    sys.argv = ["helm", *argv]
    buf = io.StringIO()
    R.console.file = buf
    R.run()
    return buf.getvalue()


# ---- refusals: structural, and nothing written ---------------------------------
# (The SHORT ON refusal is second_resell_refused below, on AA after its own
# re-sell -- a live ticker's state moves: B was SHORT ON on 09-23 and harvested
# 09-24 11:56, and the first cut of this case booked a re-sell on the copy.)
out = run("AA", "--strike", "50", "--expiry", "2027-01-15", "--price", "0.5", "--yes")
case("refuse_after_long", "on or after the long" in out, True)
out = run("AA", "--strike", "50", "--expiry", "2026-11-20", "--price", "0.5",
          "--contracts", "6", "--yes")
case("refuse_naked", "naked short" in out, True)
out = run("AA", "--strike", "50", "--expiry", "2026-09-18", "--price", "0.5", "--yes")
case("refuse_expired", "already expired" in out, True)
out = run("AA", "--strike", "52.5", "--expiry", "2026-11-20", "--price", "0.5", "--yes")
case("refuse_off_chain", "no $52.5 CALL at 2026-11-20" in out, True)
out = run("AA", "--strike", "55", "--expiry", "2026-11-20", "--dry-run")
case("dry_run_panel", ("re-sell panel" in out, "nothing written" in out), (True, True))
case("dry_run_writes_none",
     c.execute("select count(*) from legs where position_id=?", (AA,)).fetchone()[0], nleg0)
case("panel_long_move", "-64% of its 8.98 debit" in out, True)

# ---- the re-sell, at the SAME strike as the short bought back -------------------
bare_open = c.execute("select count(*) from exit_flags where position_id=? and kind='bare' "
                      "and disposition is null", (AA,)).fetchone()[0]
out = run("AA", "--strike", "55", "--expiry", "2026-11-20", "--price", "0.48", "--yes")
case("ok_line", "short #2 SOLD at 0.48" in out and "position SHORT ON" in out, True)
c = sqlite3.connect(str(DB_PATH))
legs = [dict(zip(("id", "leg_role", "direction", "strike", "expiration", "status",
                  "open_price", "close_price", "contracts", "entry_delta", "option_type",
                  "multiplier"), r)) for r in c.execute(
    "select id, leg_role, direction, strike, expiration, status, open_price, close_price, "
    "contracts, entry_delta, option_type, multiplier from legs where position_id=? "
    "order by expiration", (AA,))]
new = [l for l in legs if l["status"] == "OPEN" and l["direction"] == "SHORT"]
case("one_new_leg", (len(legs), len(new)), (nleg0 + 1, 1))
case("new_leg_fields", (new[0]["leg_role"], new[0]["strike"], new[0]["expiration"],
                        new[0]["open_price"], new[0]["contracts"]),
     ("SHORT_CALL", 55.0, "2026-11-20", 0.48, 5))
case("one_position", c.execute("select count(*) from positions where ticker='AA'").fetchone()[0], npos0)
case("net_premium_kept", c.execute("select net_premium from positions where id=?", (AA,)).fetchone()[0], np0)
ev = c.execute("select event_type, narrative from lifecycle_events where leg_id=?",
               (new[0]["id"],)).fetchone()
case("event", (ev[0], ev[1].startswith("LEG_ADDED | SHORT_CALL SHORT $55 2026-11-20 | fill 0.48 x5 | short #2")),
     ("ADJUSTED", True))
case("snapshot_untouched",
     c.execute("select count(*) from entry_snapshots where position_id=?", (AA,)).fetchone()[0], 1)
case("state", PV.diagonal_state(legs), "SHORT ON")
case("open_short_is_new", PV.open_short_leg(legs)["id"], new[0]["id"])
case("rent_unchanged", PV.rent_collected(legs), 1080.0)
case("shorts_sold", PV.shorts_sold(legs), 2)
if bare_open:
    case("bare_settled", c.execute(
        "select disposition, decided_by from exit_flags where position_id=? and kind='bare' "
        "order by id desc", (AA,)).fetchone(), ("ACTED", "inferred-resell"))
out = run("AA", "--strike", "60", "--expiry", "2026-11-20", "--price", "0.1", "--yes")
case("second_resell_refused", "a short is already on" in out, True)

# ---- the readers a second short reaches -----------------------------------------
pos = dict(zip(("id", "ticker", "strategy", "net_premium"), c.execute(
    "select id, ticker, strategy, net_premium from positions where id=?", (AA,)).fetchone()))
marks = {l["id"]: {"SHORT": 0.17 if l["status"] == "CLOSED" else 0.30,
                   "LONG": 3.20}[l["direction"]] for l in legs}
a = CC.assess_position(pos, legs, 44.58, {"mid": 0.30}, {}, leg_marks=marks)
# closed 55C (2.33 -> 0.17) +1,080 · new 55C (0.48 -> 0.30) +90 · long (8.98 -> 3.20) -2,890
case("same_strike_pnl", a["pnl_mtm"], -1720.0)
case("buffer_open_only", (a["buffer_strike"], a["intrinsic_buffer"]), (55.0, 10.42))
legs60 = [dict(l, strike=60.0) if l["id"] == new[0]["id"] else l for l in legs]
marks60 = dict(marks)
a60 = CC.assess_position(pos, legs60, 44.58, {"mid": 0.30}, {}, leg_marks=marks60)
case("buffer_skips_dead_wall", (a60["buffer_strike"], a60["intrinsic_buffer"]), (60.0, 15.42))
case("thesis_open_wall", TH.buffer_pct(legs60, 44.58)[1], 60.0)
allc = [dict(l, status="CLOSED") for l in legs60]
case("thesis_history_kept", TH.buffer_pct(allc, 44.58)[1], 55.0)

# ---- exit flags follow the NEW short --------------------------------------------
rows = [{"checked_at": "2026-09-25T10:00", "spot_price": 44.6, "pnl_pct": -40,
         "pnl_unrealized": -1720, "marks": {new[0]["id"]: 0.20,
                                            [l for l in legs if l["direction"] == "LONG"][0]["id"]: 3.2}}]
s = EF.diag_series(legs, rows)[-1]
case("flags_on_new_short", (s[2].get("leg"), s[1]), (new[0]["id"], ["harvest50", "harvest25"]))

# ---- the chain_contract lift: the W163 pin returns what it returned before ------
spec = importlib.util.spec_from_file_location(
    "diag_before", os.path.join(os.environ.get("HELM_REPO", ROOT), "helm", "cli", "diagonal.py"))
before = importlib.util.module_from_spec(spec)
spec.loader.exec_module(before)
if hasattr(before, "chain_contract") and not os.environ.get("HELM_REPO"):
    print("NOTE  pin_equiv skipped: set HELM_REPO to the pre-change repo to compare")
else:
    got = DG.pin_diagonal_from_chain("AA", 50, "2026-11-20", 47, "2026-12-18")
    was = before.pin_diagonal_from_chain("AA", 50, "2026-11-20", 47, "2026-12-18")
    case("pin_equiv", got, was)
    try:
        DG.pin_diagonal_from_chain("AA", 52.5, "2026-11-20", 47, "2026-12-18")
        e1 = None
    except RuntimeError as e:
        e1 = str(e)
    try:
        before.pin_diagonal_from_chain("AA", 52.5, "2026-11-20", 47, "2026-12-18")
        e2 = None
    except RuntimeError as e:
        e2 = str(e)
    case("pin_refusal_equiv", e1, e2)

# ---- nothing the rest of HELM imports from diagonal.py went missing ---------------
# 2026-09-24: the chain_contract lift replaced a line range that also held
# evaluate_diagonal and display_diagonal. This harness never imported them, so it
# passed, and `helm open PFE DIAGONAL` failed live with ImportError. The case
# below reads every `from helm.cli.diagonal import ...` in the tree.
import re
want = set()
for dp, _dn, fns in os.walk(os.path.join(ROOT, "helm")):
    for fn in fns:
        if fn.endswith(".py"):
            for m in re.finditer(r"from helm\.cli\.diagonal import ([\w, ]+)",
                                 open(os.path.join(dp, fn)).read()):
                want |= {x.strip() for x in m.group(1).split(",") if x.strip()}
case("importers_satisfied", sorted(n for n in want if not hasattr(DG, n)), [])
case("importers_found", {"evaluate_diagonal", "display_diagonal"} <= want, True)

print("\nALL PASS" if not FAILS else "\n%d FAIL%s" % (len(FAILS), "" if len(FAILS) == 1 else "S"))
sys.exit(1 if FAILS else 0)
