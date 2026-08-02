#!/usr/bin/env python3
"""HELM-148 verify -- the long-family exit rules render on the card.

The panel must reuse the ENGINE's constants (never re-declare them) and must
agree with long_exit.long_verdict on which rule fires. Pure fixtures; no DB."""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from helm import thesis as th
from helm import long_exit as le

FAIL = 0
def ck(name, cond):
    global FAIL
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        FAIL += 1

LEG = [{"direction": "LONG", "option_type": "CALL", "strike": 375.0,
        "expiration": "2026-08-21", "contracts": 2, "multiplier": 100,
        "open_price": 20.41, "leg_role": "LONG_CALL"}]
def POS(**kw):
    d = {"id": "T", "ticker": "GOOG", "strategy": "LONG_CALL", "status": "OPEN",
         "net_premium": -4082.0, "book": "PAPER"}
    d.update(kw)
    return d
def C(day, pnl, dte=60, broken=0):
    return {"checked_at": "2026-07-%02dT15:45:00" % day, "spot_price": 358.55,
            "pnl_unrealized": pnl, "dte_now": dte, "delta": 0.28,
            "thesis_broken": broken}

def rules_of(card):
    return {r["key"]: r for r in (card.get("exit_rules") or {}).get("rows", [])}

# --- the panel exists on longs, not on credit ---------------------------------
lc = th.evaluate(POS(), LEG, [C(30, -1000.0), C(31, -1000.0)])
ck("panel present on a long call", bool(lc.get("exit_rules")))
ck("panel lists four rules", len((lc["exit_rules"] or {}).get("rows", [])) == 4)
ck("rules are in precedence order",
   [r["key"] for r in lc["exit_rules"]["rows"]] ==
   ["thesis", "profit_floor", "dte_gate", "catastrophe"])
csp = th.evaluate({"id": "C", "ticker": "CSCO", "strategy": "CSP", "status": "OPEN",
                   "net_premium": 2935.0, "book": "REAL"},
                  [{"direction": "SHORT", "option_type": "PUT", "strike": 115.0,
                    "expiration": "2026-08-21", "contracts": 5, "multiplier": 100,
                    "open_price": 5.87, "leg_role": "SHORT_PUT"}],
                  [{"checked_at": "2026-07-31T15:45:00", "spot_price": 116.64,
                    "pnl_unrealized": 310.0, "dte_now": 21, "delta": -0.43}])
ck("no long panel on a credit card", csp.get("exit_rules") is None)

# --- constants come from the engine, not re-declared --------------------------
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "helm", "thesis.py"), encoding="utf-8").read()
ck("thesis.py imports the engine constants", "from helm import long_exit" in src)
for bad in ("PROFIT_FLOOR_ARM =", "DTE_GATE_DAYS =", "CATASTROPHE_PCT =", "RATCHET_STEP ="):
    ck("thesis.py does not re-declare %s" % bad.split()[0], bad not in src)
ck("panel quotes the engine's gate value",
   str(le.DTE_GATE_DAYS) in rules_of(lc)["dte_gate"]["text"])

# --- catastrophe ---------------------------------------------------------------
cata = th.evaluate(POS(), LEG, [C(30, -2500.0, dte=60), C(31, -2500.0, dte=60)])
r = rules_of(cata)
ck("catastrophe fires past -50%", r["catastrophe"]["state"] == "FIRES")
ck("catastrophe firing is the panel verdict", cata["exit_rules"]["firing"] == "catastrophe")
ck("catastrophe names the depth", "61" in r["catastrophe"]["text"])
ok = th.evaluate(POS(), LEG, [C(30, -500.0, dte=60), C(31, -500.0, dte=60)])
ck("catastrophe quiet above -50%", rules_of(ok)["catastrophe"]["state"] == "CLEAR")
ck("nothing fires when all clear", ok["exit_rules"]["firing"] is None)
ck("clear panel says so", "nothing would close" in ok["exit_rules"]["summary"])

# --- rendered copy carries no internal register references ----------------------
_REF = re.compile(r"W[0-9]{1,3}\b|HELM-[0-9]+|s9[0-9]\b|tier [ABC]")
def _strings(card):
    got = []
    def walk(v):
        if isinstance(v, str):
            got.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k != "position_id":
                    walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(card)
    return got
bare = th.evaluate(POS(), LEG, [C(30, -1000.0), C(31, -1000.0)])
hits = [s for s in _strings(bare) if _REF.search(s)]
ck("no register references in a card with nothing captured", not hits)
snapless = th.evaluate(POS(), LEG, [C(30, -1000.0), C(31, -1000.0)], entry_snap=None)
ck("the ungradable premium line is clean",
   not [s for s in _strings(snapless) if _REF.search(s)])

# --- journal coverage behind the high-water mark (HELM-149) ---------------------
def CJ(day, pnl, armed=None, dte=60, broken=0):
    r = C(day, pnl, dte=dte, broken=broken)
    if armed is not None:
        r["lc_arms_json"] = json.dumps({"thesis": {"armed": armed, "ctx_source":
                                                   "signals" if armed else None}})
    return r

blindp = POS(opened_at="2026-07-20T14:00:00")
bc = th.evaluate(blindp, LEG, [C(30, 500.0), C(31, 400.0)])
fr = rules_of(bc)["profit_floor"]
ck("floor row states how many check days it rests on", "2 check days" in fr["text"])
ck("blind days after opening are named", "were never journaled" in fr["text"])
ck("blind-day text says which way it errs", "may be understated" in fr["text"])
one_blind = rules_of(th.evaluate(POS(opened_at="2026-07-29T14:00:00"), LEG,
                                 [C(30, 500.0), C(31, 400.0)]))["profit_floor"]
ck("one blind day reads as singular", "first 1 day after opening was never" in one_blind["text"])
ck("many blind days read as plural", "days after opening were never" in fr["text"])
sameday = POS(opened_at="2026-07-30T14:00:00")
fr2 = rules_of(th.evaluate(sameday, LEG, [C(30, 500.0), C(31, 400.0)]))["profit_floor"]
ck("no blind clause when journalled from day one", "never journaled" not in fr2["text"])
ck("coverage still stated when nothing was missed", "2 check days" in fr2["text"])
noopen = rules_of(th.evaluate(POS(), LEG, [C(30, 500.0), C(31, 400.0)]))["profit_floor"]
ck("no blind clause without an open date", "never journaled" not in noopen["text"])

# --- a rule that was never evaluated must not read as reassurance ---------------
ET = {"bias_score": 2.0, "spot_price": 200.0, "sma_50": 190.0}
ne = th.evaluate(POS(), LEG, [CJ(30, -500.0, armed=False), CJ(31, -500.0, armed=False)],
                 entry_thesis_row=ET)
tr = rules_of(ne)["thesis"]
ck("unevaluated thesis gets its own state", tr["state"] == "NOT_EVALUATED")
ck("unevaluated text says it was not tested", "not tested at the latest check" in tr["text"])
ck("unevaluated does not claim the direction holds", "still holds" not in tr["text"])
ck("unevaluated is not counted as firing", ne["exit_rules"]["firing"] != "thesis")
evd = th.evaluate(POS(), LEG, [CJ(30, -500.0, armed=True), CJ(31, -500.0, armed=True)],
                  entry_thesis_row=ET)
ck("an evaluated clear thesis still reads as holding",
   rules_of(evd)["thesis"]["state"] == "CLEAR")
ck("no entry thesis still outranks the unevaluated state",
   rules_of(th.evaluate(POS(), LEG, [CJ(30, -500.0, armed=False)]))["thesis"]["state"]
   == "CANNOT_ARM")
ck("the market read's source is stated",
   "came from the day's scan" in evd["exit_rules"]["fine"])

# --- dte gate -------------------------------------------------------------------
gate = th.evaluate(POS(), LEG, [C(30, -500.0, dte=19), C(31, -500.0, dte=19)])
rg = rules_of(gate)
ck("gate fires inside 30 days", rg["dte_gate"]["state"] == "FIRES")
ck("gate names days left", "19 days left" in rg["dte_gate"]["text"])
ck("gate outranks catastrophe when both live",
   th.evaluate(POS(), LEG, [C(30, -2500.0, dte=19), C(31, -2500.0, dte=19)]
               )["exit_rules"]["firing"] == "dte_gate")

# --- profit floor ---------------------------------------------------------------
nf = th.evaluate(POS(), LEG, [C(29, 2030.0, dte=60), C(30, 1500.0, dte=60), C(31, 700.0, dte=60)])
rn = rules_of(nf)
ck("floor not armed below +50% peak", rn["profit_floor"]["state"] == "NOT_ARMED")
ck("not-armed text names the peak and the arming level",
   "49" in rn["profit_floor"]["text"] and "50%" in rn["profit_floor"]["text"])
armed = th.evaluate(POS(), LEG, [C(29, 2500.0, dte=60), C(30, 2400.0, dte=60), C(31, 2400.0, dte=60)])
ra = rules_of(armed)
ck("floor arms past +50% peak", ra["profit_floor"]["state"] == "ARMED")
ck("armed floor quotes the floor level", "+50%" in ra["profit_floor"]["text"])
fell = th.evaluate(POS(), LEG, [C(29, 2500.0, dte=60), C(30, 2400.0, dte=60), C(31, 1600.0, dte=60)])
ck("floor fires when P&L falls back to it", rules_of(fell)["profit_floor"]["state"] == "FIRES")
ck("floor outranks the gate",
   th.evaluate(POS(), LEG, [C(29, 2500.0, dte=19), C(30, 2400.0, dte=19), C(31, 1600.0, dte=19)]
               )["exit_rules"]["firing"] == "profit_floor")
ck("floor agrees with the engine's own floor_for",
   le.floor_for(0.6125) == 0.5)

# --- thesis -----------------------------------------------------------------------
brk = th.evaluate(POS(), LEG, [C(30, -500.0, dte=60, broken=1), C(31, -500.0, dte=60, broken=1)],
                  entry_thesis_row={"bias_score": 2.0, "spot_price": 200.0, "sma_50": 190.0})
rb = rules_of(brk)
ck("thesis fires on a confirmed break", rb["thesis"]["state"] == "FIRES")
ck("thesis outranks everything", brk["exit_rules"]["firing"] == "thesis")
ck("thesis text states the confirmation is in checks, not days",
   "checks" in rb["thesis"]["text"])
one = th.evaluate(POS(), LEG, [C(30, -500.0, dte=60, broken=0), C(31, -500.0, dte=60, broken=1)],
                  entry_thesis_row={"bias_score": 2.0, "spot_price": 200.0, "sma_50": 190.0})
ck("a single broken check does not fire", rules_of(one)["thesis"]["state"] != "FIRES")
unarmed = rules_of(lc)["thesis"]
ck("no entry thesis means the rule cannot arm", unarmed["state"] == "CANNOT_ARM")
ck("cannot-arm says why", "no entry thesis" in unarmed["text"])

# --- book note --------------------------------------------------------------------
ck("paper card says the rules act", "acts on these" in lc["exit_rules"]["book_note"])
real = th.evaluate(POS(book="REAL"), LEG, [C(30, -500.0), C(31, -500.0)])
ck("real card says advisory", "advisory" in real["exit_rules"]["book_note"])
closed = th.evaluate(POS(status="CLOSED", exit_reason="THESIS_BREAK", closed_at="2026-07-30"),
                     LEG, [C(30, -500.0), C(31, -500.0)])
ck("closed card carries no live rules panel", closed.get("exit_rules") is None)

print()
print("RESULT: %s" % ("ALL PASS" if FAIL == 0 else "%d FAILED" % FAIL))
sys.exit(1 if FAIL else 0)
