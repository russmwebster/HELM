#!/usr/bin/env python3
"""HELM-148/150 verify -- the exit-rules panel on long-family cards (v3).

Rewritten from the v2 version when the acting rules changed. Pure fixtures."""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from helm import thesis as th
from helm import long_exit as le

FAIL = 0
def ck(n, c):
    global FAIL
    print(("PASS  " if c else "FAIL  ") + n)
    if not c: FAIL += 1

LEG = [{"direction": "LONG", "option_type": "CALL", "strike": 375.0,
        "expiration": "2026-08-21", "contracts": 2, "multiplier": 100,
        "open_price": 20.41, "leg_role": "LONG_CALL"}]
def POS(**kw):
    d = {"id": "T", "ticker": "GOOG", "strategy": "LONG_CALL", "status": "OPEN",
         "net_premium": -4082.0, "book": "PAPER"}
    d.update(kw)
    return d
def C(day, pnl, dte=60):
    return {"checked_at": "2026-07-%02dT15:45:00" % day, "spot_price": 358.55,
            "pnl_unrealized": pnl, "dte_now": dte, "delta": 0.28}
def rules_of(card):
    return {r["key"]: r for r in (card.get("exit_rules") or {}).get("rows", [])}

# --- shape ---------------------------------------------------------------------
lc = th.evaluate(POS(), LEG, [C(30, -1000.0), C(31, -1000.0)])
ck("panel present on a long call", bool(lc.get("exit_rules")))
ck("panel lists the four v3 rules", len(lc["exit_rules"]["rows"]) == 4)
ck("precedence order is stop, give-back, hard close, calendar",
   [r["key"] for r in lc["exit_rules"]["rows"]] ==
   ["stop_loss", "give_back", "dte_7", "dte_21"])
ck("the retired direction rule is not a row",
   "thesis" not in rules_of(lc) and "profit_floor" not in rules_of(lc))
ck("fine print says the direction read closes nothing",
   "closes nothing" in lc["exit_rules"]["fine"])
csp = th.evaluate({"id": "C", "ticker": "CSCO", "strategy": "CSP", "status": "OPEN",
                   "net_premium": 2935.0, "book": "REAL"},
                  [{"direction": "SHORT", "option_type": "PUT", "strike": 115.0,
                    "expiration": "2026-08-21", "contracts": 5, "multiplier": 100,
                    "open_price": 5.87, "leg_role": "SHORT_PUT"}],
                  [{"checked_at": "2026-07-31T15:45:00", "spot_price": 116.64,
                    "pnl_unrealized": 310.0, "dte_now": 21, "delta": -0.43}])
ck("no long panel on a credit card", csp.get("exit_rules") is None)
closed = th.evaluate(POS(status="CLOSED", exit_reason="GIVE_BACK",
                         closed_at="2026-07-30"), LEG, [C(30, -500.0)])
ck("closed card carries no live panel", closed.get("exit_rules") is None)

# --- constants come from the engine --------------------------------------------
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "helm", "thesis.py"), encoding="utf-8").read()
ck("thesis.py imports the engine module", "from helm import long_exit" in src)
for bad in ("GIVE_BACK_BAND =", "STOP_LOSS_PCT =", "DTE_SOFT =", "DTE_HARD ="):
    ck("thesis.py does not re-declare %s" % bad.split()[0], bad not in src)
ck("panel quotes the engine's calendar value",
   str(int(le.DTE_SOFT)) in rules_of(lc)["dte_21"]["text"])
ck("panel quotes the engine's hard close", str(int(le.DTE_HARD)) in rules_of(lc)["dte_7"]["text"])

# --- the stop -------------------------------------------------------------------
st = th.evaluate(POS(), LEG, [C(29, 1000.0), C(30, -2500.0), C(31, -2500.0)])
rs = rules_of(st)
ck("stop fires past -50%", rs["stop_loss"]["state"] == "FIRES")
ck("stop outranks give-back even when both are true", st["exit_rules"]["firing"] == "stop_loss")
ck("stop names the depth", "61" in rs["stop_loss"]["text"])
ck("stop quiet above -50%", rules_of(lc)["stop_loss"]["state"] == "CLEAR")

# --- the give-back trail --------------------------------------------------------
gb = th.evaluate(POS(), LEG, [C(29, 1000.0), C(30, -100.0), C(31, -100.0)])
rg = rules_of(gb)
ck("give-back fires after a 20-point fall from the peak", rg["give_back"]["state"] == "FIRES")
ck("give-back is the verdict when nothing worse applies", gb["exit_rules"]["firing"] == "give_back")
ck("give-back quotes floor, best and now",
   "floor" in rg["give_back"]["text"] and "best was" in rg["give_back"]["text"])
ck("give-back holds inside the band", rules_of(lc)["give_back"]["state"] == "CLEAR")
ck("clear text states the band", "20 points" in rules_of(lc)["give_back"]["text"])
ck("no arming threshold language survives",
   "arms at" not in rules_of(lc)["give_back"]["text"])

# --- journal coverage -----------------------------------------------------------
blind = th.evaluate(POS(opened_at="2026-07-20T14:00:00"), LEG, [C(30, 500.0), C(31, 400.0)])
fr = rules_of(blind)["give_back"]
ck("coverage states the check-day count", "2 check days" in fr["text"])
ck("blind days after opening are named", "never journaled" in fr["text"])
one = rules_of(th.evaluate(POS(opened_at="2026-07-29T14:00:00"), LEG,
                           [C(30, 500.0), C(31, 400.0)]))["give_back"]
ck("one blind day reads as singular", "first 1 day after opening was never" in one["text"])
same = rules_of(th.evaluate(POS(opened_at="2026-07-30T14:00:00"), LEG,
                            [C(30, 500.0), C(31, 400.0)]))["give_back"]
ck("no blind clause when journalled from day one", "never journaled" not in same["text"])

# --- the calendar ---------------------------------------------------------------
hard = th.evaluate(POS(), LEG, [C(30, -100.0, dte=5), C(31, -100.0, dte=5)])
ck("inside 7 days the hard close fires", rules_of(hard)["dte_7"]["state"] == "FIRES")
ck("hard close is the verdict", hard["exit_rules"]["firing"] == "dte_7")
soft = th.evaluate(POS(), LEG, [C(30, -100.0, dte=15), C(31, -100.0, dte=15)])
ck("negative inside 21 days closes on the calendar",
   rules_of(soft)["dte_21"]["state"] == "FIRES")
pos = th.evaluate(POS(), LEG, [C(30, 100.0, dte=15), C(31, 100.0, dte=15)])
ck("positive inside 21 days is held", rules_of(pos)["dte_21"]["state"] == "CLEAR")
ck("held-positive text says the floor governs", "floor governs" in rules_of(pos)["dte_21"]["text"])
ck("a held positive position fires nothing", pos["exit_rules"]["firing"] is None)
ck("clear panel says so", "nothing would close" in pos["exit_rules"]["summary"])

# --- which book acts ------------------------------------------------------------
ck("paper card says the rules act", "acts on these" in lc["exit_rules"]["book_note"])
ck("real card says advisory",
   "advisory" in th.evaluate(POS(book="REAL"), LEG,
                             [C(30, -500.0), C(31, -500.0)])["exit_rules"]["book_note"])

# --- rendered copy carries no internal register references ----------------------
_REF = re.compile(r"W[0-9]{1,3}\b|HELM-[0-9]+|s9[0-9]\b|tier [ABC]")
def _strings(card):
    got = []
    def walk(v):
        if isinstance(v, str): got.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k != "position_id": walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v: walk(x)
    walk(card)
    return got
ck("no register references in a rendered card",
   not [s for s in _strings(th.evaluate(POS(), LEG, [C(30, -1000.0), C(31, -1000.0)]))
        if _REF.search(s)])

print()
print("RESULT: %s" % ("ALL PASS" if FAIL == 0 else "%d FAILED" % FAIL))
sys.exit(1 if FAIL else 0)
