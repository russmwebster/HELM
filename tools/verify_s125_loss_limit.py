"""s125 rule change (Russ, 2026-10-01): the diagonal's position stop becomes the
LOSS LIMIT -- the whole trade's dollar P&L at or below −half of what the long
cost -- and runs in BOTH states (short on and long only). Until now it was
pnl_pct <= −50, a percent of the OPENING net debit, which mixed bases once a
short had been re-sold.

Checks, against the live database read-only:
  * on every journaled diagonal check, `pos_stop` is in exit_flags.diag_series'
    kinds exactly when pnl_unrealized <= −0.5 × (long open × contracts × 100),
    computed here independently from the legs table -- in both states;
  * the kinds other than pos_stop are identical to the baseline module
    (helm/exit_flags.py.bak-20261001-s125d): nothing else moved;
  * on the REAL book, prints any difference in the flags the next scan would emit
    (none on the day of the switch, 2026-10-01).
Usage: python3 tools/verify_s125_loss_limit.py   (device VM, HELM_ROOT set)
Perturbation run 2026-10-01: LOSS_LIMIT_FRAC 0.5 -> 0.4 -> FAIL.
"""
import os, sqlite3, sys
from importlib.machinery import SourceFileLoader
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from helm import exit_flags as EF
print("candidate:", EF.__file__)
bak = os.path.join(HERE, "helm", "exit_flags.py.bak-20261001-s125d")
if not os.path.exists(bak):
    sys.exit("baseline %s is gone (removed under the .bak rule) -- this harness cannot run" % bak)
OLD = SourceFileLoader("ef_old", bak).load_module()
print("baseline :", bak)
c = sqlite3.connect("file:" + os.path.join(HERE, "data", "helm.db") + "?mode=ro", uri=True)
P = F = 0
def ok(n, cond, det=""):
    global P, F
    P += bool(cond); F += (not cond)
    if not cond and F <= 25: print("FAIL", n, det)
rows_n = both = 0
for pid, book, st in c.execute("select id, book, status from positions where strategy in "
                               "('DIAGONAL','PMCC','DIAGONAL_PUT')").fetchall():
    legs = EF._legs(c, pid); rows = EF._diag_rows(c, pid, legs)
    lg = [l for l in legs if str(l["direction"]).upper() == "LONG"]
    if len(lg) != 1:
        continue
    cost = lg[0]["open_price"] * lg[0]["contracts"] * 100
    new = EF.diag_series(legs, rows); old = OLD.diag_series(OLD._legs(c, pid), OLD._diag_rows(c, pid))
    for (r, k, info), (r2, k2, i2) in zip(new, old):
        rows_n += 1
        want = r["pnl_unrealized"] is not None and r["pnl_unrealized"] <= -0.5 * cost
        ok("pos_stop %s %s" % (pid, r["checked_at"][:16]), ("pos_stop" in k) == want,
           (r["pnl_unrealized"], cost, info.get("state")))
        if want and info.get("state") == "LONG ONLY":
            both += 1
        ok("other kinds unchanged %s %s" % (pid, r["checked_at"][:16]),
           [x for x in k if x != "pos_stop"] == [x for x in k2 if x != "pos_stop"], (k, k2))
    if book == "REAL" and st == "OPEN" and rows:
        day = rows[-1]["checked_at"][:10]
        o, a = EF._state(c, pid)
        n1 = [x[0] for x in EF.diag_new_flags(legs, rows, o, a, only_day=day)]
        o2, a2 = OLD._state(c, pid)
        n2 = [x[0] for x in OLD.diag_new_flags(OLD._legs(c, pid), OLD._diag_rows(c, pid), o2, a2, only_day=day)]
        if n1 != n2:   # informational: legitimately differs on later days
            print("NOTE real flags the next scan would emit differ for %s: new %s, old %s" % (pid, n1, n2))
        else:
            P += 1
print("rows checked %d · loss limit firing in LONG ONLY on %d rows" % (rows_n, both))
print("PASS %d  FAIL %d" % (P, F))
sys.exit(1 if F else 0)
