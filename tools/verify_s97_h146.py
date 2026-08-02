#!/usr/bin/env python3
"""HELM-146 verify -- card sentences must read the facts they assert.

Pure fixtures only; no DB. Control run: against pre-change thesis.py the
sentence checks fail (worst-of-day present tense, the false green badge,
the gross-debit headline, the signed convergence figure)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from helm import thesis as th

FAIL = 0
def ck(name, cond):
    global FAIL
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        FAIL += 1

csp_pos = {"id": "T-CSP", "ticker": "CSCO", "strategy": "CSP", "status": "OPEN",
           "net_premium": 2935.0, "book": "REAL"}
csp_legs = [{"direction": "SHORT", "option_type": "PUT", "strike": 115.0,
             "expiration": "2026-08-21", "contracts": 5, "multiplier": 100,
             "open_price": 5.87, "leg_role": "SHORT_PUT"}]
def chk(ts, spot, pnl):
    return {"checked_at": ts, "spot_price": spot, "pnl_unrealized": pnl,
            "dte_now": 21, "delta": -0.43}

csco_checks = []
for d in range(1, 13):
    csco_checks.append(chk("2026-07-%02dT15:45:00" % (10 + d), 112.0, -500.0))
csco_checks += [chk("2026-07-31T10:02:00", 114.71, -100.0),
                chk("2026-07-31T12:32:00", 115.53, 125.0),
                chk("2026-07-31T15:47:00", 116.64, 310.0)]

card = th.evaluate(csp_pos, csp_legs, csco_checks)
sb = card["beliefs"][0]
ck("state still broken (worst-of-day governs)", sb["state"] in (th.BROKEN, th.BROKEN_LOUD))
ck("Now leads with the latest check, above the strike",
   "above the $115 strike at the latest check" in sb["now"])
ck("Now names the intraday dip", "worst reading of that day" in sb["now"])
ck("misleading streak phrase gone", "consecutive daily checks" not in sb["now"])
ck("streak restated as worst-of-day mechanism", "day's worst reading on" in sb["now"])
ck("recovery clause present", "break state clears" in sb["now"])
ck("cue acknowledges the bounce", "back on the right side" in card["read"])

lr_checks = [chk("2026-07-%02dT15:45:00" % d, 108.0, -2000.0) for d in range(20, 32)]
card2 = th.evaluate(csp_pos, csp_legs, lr_checks)
sb2 = card2["beliefs"][0]
ck("still-breached Now leads with latest, below",
   "below the $115 strike at the latest check" in sb2["now"])
ck("no recovery clause when still breached", "break state clears" not in sb2["now"])

lc_pos = {"id": "T-LC", "ticker": "GOOG", "strategy": "LONG_CALL", "status": "OPEN",
          "net_premium": -4082.0, "book": "REAL"}
lc_legs = [{"direction": "LONG", "option_type": "CALL", "strike": 375.0,
            "expiration": "2026-08-21", "contracts": 2, "multiplier": 100,
            "open_price": 20.41, "leg_role": "LONG_CALL"}]
lc_card = th.evaluate(lc_pos, lc_legs, [chk("2026-07-31T15:47:00", 358.55, -3082.0)])
ck("ungraded lead in the read", "ungraded" in lc_card["read"])
ck("unarmed cue when direction never armed",
   "never" in lc_card["read"] and "armed" in lc_card["read"])
ck("no 'Every graded belief holds' on an unarmed card",
   "Every graded belief holds" not in lc_card["read"])
ck("badge is not a bare green check", lc_card["summary"]["label"] != "✓ 1/1")

own_card = th.evaluate(dict(csp_pos), csp_legs, [chk("2026-07-31T15:47:00", 120.0, 310.0)])
ck("badge carries 'not graded' when some beliefs are ungraded",
   "not graded" in own_card["summary"]["label"] or own_card["summary"]["unknown"] == 0)

t_credit_win = th.close_series({"net_premium": 2935.0},
                               [{"checked_at": "2026-07-31T15:47:00", "pnl_unrealized": 310.0}])
h = th.close_headline(t_credit_win)
ck("credit winner: keeps-the-credit form", "keeps $310 of the $2,935 credit" in h)
t_credit_loss = th.close_series({"net_premium": 9560.0},
                                [{"checked_at": "2026-07-31T15:51:00", "pnl_unrealized": -7460.0}])
h2 = th.close_headline(t_credit_loss)
ck("credit loser: new-money leads, gross follows",
   "takes $7,460 of new money" in h2 and "$17,020" in h2)
t_debit = th.close_series({"net_premium": -1260.0},
                          [{"checked_at": "2026-07-30T15:53:00", "pnl_unrealized": -415.0}])
h3 = th.close_headline(t_debit, closed=True)
ck("closed card speaks past tense", "would have" in h3 and "today" not in h3)
ck("headline None passthrough", th.close_headline(None) is None)

ic_legs = [
    {"direction": "LONG", "option_type": "PUT", "strike": 350.0, "expiration": "2026-08-21",
     "contracts": 20, "multiplier": 100, "open_price": 25.2, "leg_role": "LONG_PUT"},
    {"direction": "SHORT", "option_type": "PUT", "strike": 360.0, "expiration": "2026-08-21",
     "contracts": 20, "multiplier": 100, "open_price": 29.47, "leg_role": "SHORT_PUT"},
    {"direction": "SHORT", "option_type": "CALL", "strike": 560.0, "expiration": "2026-08-21",
     "contracts": 20, "multiplier": 100, "open_price": 14.81, "leg_role": "SHORT_CALL"},
    {"direction": "LONG", "option_type": "CALL", "strike": 570.0, "expiration": "2026-08-21",
     "contracts": 20, "multiplier": 100, "open_price": 14.3, "leg_role": "LONG_CALL"},
]
ic_pos = {"id": "T-IC", "ticker": "LRCX", "strategy": "IRON_CONDOR", "status": "OPEN",
          "net_premium": 9560.0, "book": "REAL"}
_rows, conv = th.expiry_ladder(ic_pos, ic_legs, 298.67, -7460.0, 21.0)
ck("against-you convergence says costing", conv is not None and "costing" in conv)
ck("no signed +$ in the per-week figure",
   conv is not None and "+$" not in conv.split("costing")[-1])
_rows2, conv2 = th.expiry_ladder(ic_pos, ic_legs, 460.0, -12000.0, 21.0)
ck("for-you convergence says adding", conv2 is not None and "adding" in conv2)

ic_card = th.evaluate(ic_pos, ic_legs, [chk("2026-07-31T15:51:00", 298.67, -7460.0)])
note = (ic_card.get("close_track") or {}).get("itm_note")
ck("deep-ITM caveat fires when mids sit below expiry value",
   note is not None and "$20,000" in note)
otm_card = th.evaluate(ic_pos, ic_legs, [chk("2026-07-31T15:51:00", 460.0, 2000.0)])
ck("no caveat when not below expiry value",
   (otm_card.get("close_track") or {}).get("itm_note") is None)

ds = th.deal_sentence(lc_pos, lc_legs)
ck("long deal sentence carries the break-even", "395.41" in ds and "break-even" in ds)
ck("long deal sentence keeps the strike", "$375" in ds)

print()
print("RESULT: %s" % ("ALL PASS" if FAIL == 0 else "%d FAILED" % FAIL))
sys.exit(1 if FAIL else 0)
