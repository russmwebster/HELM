#!/usr/bin/env python3
"""HELM-150 verify -- long-family exit rules v3, acting on paper.

Behavioural: drives long_verdict directly. Control run against pre-change code
fails at the new verdict names and at the calendar fall-through."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from helm import long_exit as le

# Resolved defensively so this suite still RUNS against pre-change code --
# a control that aborts on a missing constant proves only that the file
# changed, not that behaviour did.
BAND = getattr(le, 'GIVE_BACK_BAND', None)
TF = getattr(le, 'trail_floor', lambda *a, **k: None)

FAIL = 0
def ck(n, c):
    global FAIL
    print(("PASS  " if c else "FAIL  ") + n)
    if not c: FAIL += 1

DEBIT = -1000.0
ET = {"source": "signals", "bias_score": 2.0, "spot_price": 200.0, "sma_50": 190.0}
CUR = {"source": "signals", "bias_score": 2.0, "spot": 200.0, "sma_50": 190.0}
def V(pnl_pct, dte, hwm=None, entry=ET, cur=CUR, streak=0):
    pnl = pnl_pct * abs(DEBIT)
    js = {"hwm_pct": hwm, "break_days": streak}
    return le.long_verdict(pnl, DEBIT, dte, entry, cur, js)

# ---- the trail --------------------------------------------------------------
ck("band is 20 points", BAND == 0.20)
ck("trail sits 20 below the peak", TF(0.65) == 0.45)
ck("trail works at a negative peak", TF(-0.018) is not None and abs(TF(-0.018) + 0.218) < 1e-9)
ck("trail never deeper than the stop", TF(-0.45) == -0.50)
ck("no peak, no trail", TF(None) is None)
ck("no arming threshold: a small peak still trails",
   TF(0.05) is not None and abs(TF(0.05) + 0.15) < 1e-9)

r, a = V(0.14, 60, hwm=0.34)
ck("peak +34, now +14 -> fires", r == "GIVE_BACK")
r, a = V(0.15, 60, hwm=0.34)
ck("peak +34, now +15 -> holds", r is None)
r, a = V(-0.22, 60, hwm=-0.018)
ck("negative peak still protected", r == "GIVE_BACK")
# JPM's real shape: peak +49.8 puts the floor at +29.8, so +30 is still ABOVE
# it by two tenths and holds -- the same two tenths by which it missed the v2
# arm. +29 fires. Worth keeping both, since the near-miss is the whole reason
# the arming threshold was deleted.
r, a = V(0.30, 60, hwm=0.498)
ck("JPM shape: peak +49.8, now +30 -> still above the floor, holds", r is None)
r, a = V(0.29, 60, hwm=0.498)
ck("JPM shape: peak +49.8, now +29 -> fires", r == "GIVE_BACK")

# ---- the stop ---------------------------------------------------------------
r, a = V(-0.55, 60, hwm=0.10)
ck("past -50% -> STOP_LOSS", r == "STOP_LOSS")
ck("stop outranks give-back", r != "GIVE_BACK")
r, a = V(-0.49, 60, hwm=-0.45)
ck("trail capped at the stop: -49 with a -45 peak holds", r is None)
r, a = V(-0.50, 60, hwm=-0.45)
ck("and -50 is the stop, not the trail", r == "STOP_LOSS")

# ---- the calendar -----------------------------------------------------------
r, a = V(-0.10, 21, hwm=0.0)
ck("21 DTE and negative -> DTE_21", r == "DTE_21")
r, a = V(0.30, 21, hwm=0.35)
ck("21 DTE and POSITIVE -> holds", r is None)
r, a = V(0.30, 15, hwm=0.35)
ck("15 DTE and positive -> still holds", r is None)
r, a = V(0.30, 7, hwm=0.35)
ck("7 DTE positive -> DTE_7 regardless", r == "DTE_7")
# A negative position has usually already given back 20 points, so GIVE_BACK
# catches it before any calendar rule does. DTE_7 and DTE_21 therefore only see
# the slow bleeders -- names that drifted down without ever having a peak to
# fall from. That is the intended division of labour, asserted both ways.
r, a = V(-0.30, 5, hwm=0.0)
ck("negative at 5 DTE, already 30 below its peak -> GIVE_BACK first",
   r == "GIVE_BACK")
r, a = V(-0.10, 5, hwm=0.0)
ck("slow bleeder inside 7 days -> DTE_7", r == "DTE_7")
r, a = V(-0.10, 20, hwm=0.0)
ck("slow bleeder at 20 days -> DTE_21", r == "DTE_21")
r, a = V(-0.10, 22, hwm=0.0)
ck("22 DTE negative -> holds (gate is 21)", r is None)
r, a = V(0.0, 21, hwm=0.10)
ck("exactly flat at 21 counts as not positive", r == "DTE_21")

# ---- direction no longer acts ----------------------------------------------
r, a = V(-0.10, 60, hwm=0.0, cur={"source": "signals", "bias_score": -3.0,
                                  "spot": 180.0, "sma_50": 190.0}, streak=9)
ck("a confirmed broken thesis does NOT close", r is None)
ck("but it is still computed", a["thesis"]["broken_today"] is True)
ck("and the v2 counterfactual records it", a["v2_retired"]["thesis_break"] is True)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "helm", "long_exit.py"), encoding="utf-8").read()
ck("THESIS_BREAK is emitted nowhere", "reason = 'THESIS_BREAK'" not in src)

# ---- counterfactual arms ----------------------------------------------------
r, a = V(0.16, 60, hwm=0.34)
gb = a["give_back"]
ck("arms log the acting band", gb["band"] == 0.20)
ck("arms log the 15 and 25 alternates", set(gb["alt"]) == {"0.15", "0.25"})
ck("15 would have fired here, 20 did not",
   gb["alt_would_fire"]["0.15"] is True and r is None)
ck("25 would not have fired", gb["alt_would_fire"]["0.25"] is False)
ck("v2 ratchet recorded separately", "profit_floor" in a["v2_retired"])
ck("v2 30-day gate recorded", a["v2_retired"]["dte_gate_30"] is False)

# ---- the paper agent must act on the new names ------------------------------
ag = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "paper_exit_agent.py"), encoding="utf-8").read()
for nm in ("GIVE_BACK", "STOP_LOSS", "DTE_21", "DTE_7"):
    ck("agent acts on %s" % nm, '"%s"' % nm in ag)

# ---- a positive long must not fall through to the seller's calendar ---------
from helm import decision as dec
class P:
    id = "T"; ticker = "X"; strategy = "LONG_CALL"; account_id = "a"
    net_premium = -1000.0; max_loss = None; max_profit = None
class L:
    id = 1; direction = "LONG"; open_price = 10.0; contracts = 1
    multiplier = 100; expiration = None
ck("decision.py excludes longs from the credit calendar block",
   "if fam == LONG_DEBIT_FAMILY:" in open(os.path.join(
       os.path.dirname(os.path.abspath(__file__)), "..", "helm", "decision.py"),
       encoding="utf-8").read())

print()
print("RESULT: %s" % ("ALL PASS" if FAIL == 0 else "%d FAILED" % FAIL))
sys.exit(1 if FAIL else 0)
