#!/usr/bin/env python3
"""HELM-147 verify -- the close track states its direction (trend sentence,
delta chip, noise guard). Pure fixtures; no DB. Control: pre-change code has
no trend key and no trend_sentence -- early checks fail, then AttributeError."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from helm import thesis as th

FAIL = 0
def ck(name, cond):
    global FAIL
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        FAIL += 1

def C(ts, pnl):
    return {"checked_at": ts, "pnl_unrealized": pnl}

pos = {"net_premium": 2935.0}
imp = th.close_series(pos, [
    C("2026-07-29T10:00:00", -900.0), C("2026-07-29T15:45:00", -775.0),
    C("2026-07-30T10:00:00", -550.0), C("2026-07-30T15:45:00", -365.0),
    C("2026-07-31T10:00:00", -100.0), C("2026-07-31T15:45:00", 310.0),
])
tr = imp.get("trend") or {}
ck("trend attached", bool(tr))
ck("d1 is the day-over-day move", abs((tr.get("d1") or 0) + 675.0) < 0.01)
ck("streak counts consecutive improving days", tr.get("streak") == 2)
ck("credit getting cheaper is better", tr.get("better") is True)
s = th.trend_sentence(imp)
ck("sentence says getting cheaper", (s or "").startswith("getting cheaper"))
ck("sentence carries delta and streak", "$675" in s and "improving 2 check days running" in s)
ck("sentence carries the from value", "$3,710" in s)

wor = th.close_series(pos, [C("2026-07-30T15:45:00", -365.0),
                            C("2026-07-31T15:45:00", -1365.0)])
ck("worsening credit says dearer", (th.trend_sentence(wor) or "").startswith("getting dearer"))
ck("worsening flagged not-better", (wor.get("trend") or {}).get("better") is False)

nz = th.close_series(pos, [
    C("2026-07-30T10:00:00", -65.0), C("2026-07-30T12:30:00", -865.0), C("2026-07-30T15:45:00", -565.0),
    C("2026-07-31T10:00:00", -265.0), C("2026-07-31T12:30:00", -665.0), C("2026-07-31T15:45:00", -465.0),
])
ck("move inside overlapping ranges is not clear", (nz.get("trend") or {}).get("clear") is False)
ck("noise sentence says not a clear move", "not a clear move" in (th.trend_sentence(nz) or ""))

big = th.close_series(pos, [
    C("2026-07-30T10:00:00", -945.0), C("2026-07-30T15:45:00", -1445.0),
    C("2026-07-31T10:00:00", -1365.0), C("2026-07-31T15:45:00", -4085.0),
])
ck("a move larger than the overlap is clear", (big.get("trend") or {}).get("clear") is True)

dl = th.close_series({"net_premium": -4082.0},
                     [C("2026-07-30T15:45:00", -3282.0), C("2026-07-31T15:45:00", -3082.0)])
ck("debit improving says fetching more", "fetching more" in (th.trend_sentence(dl) or ""))
ck("debit rising is better", (dl.get("trend") or {}).get("better") is True)
ck("closed cards speak past tense", "was getting" in (th.trend_sentence(wor, closed=True) or ""))

one = th.close_series(pos, [C("2026-07-31T15:45:00", 310.0)])
ck("single day: no trend, sentence None",
   one.get("trend") is None and th.trend_sentence(one) is None)
prov = th.close_series(pos, [C("2026-07-30T15:45:00", -365.0),
                             C("2026-07-31T10:00:00", -100.0)], today="2026-07-31")
ck("provisional day flagged in the sentence", "checks to come" in (th.trend_sentence(prov) or ""))

svg = th.close_svg(imp)
ck("svg carries the delta chip", "▼" in svg and "$675" in svg)
svg2 = th.close_svg(nz)
ck("no chip when the move is not clear", "▼" not in svg2 and "▲" not in svg2)

card = th.evaluate(
    {"id": "X", "ticker": "CSCO", "strategy": "CSP", "status": "OPEN",
     "net_premium": 2935.0, "book": "REAL"},
    [{"direction": "SHORT", "option_type": "PUT", "strike": 115.0,
      "expiration": "2026-08-21", "contracts": 5, "multiplier": 100,
      "open_price": 5.87, "leg_role": "SHORT_PUT"}],
    [{"checked_at": "2026-07-30T15:45:00", "spot_price": 113.49, "pnl_unrealized": -365.0,
      "dte_now": 22, "delta": -0.5},
     {"checked_at": "2026-07-31T15:45:00", "spot_price": 116.64, "pnl_unrealized": 310.0,
      "dte_now": 21, "delta": -0.43}])
ck("evaluate exposes close_trend", "cheaper" in (card.get("close_trend") or ""))

print()
print("RESULT: %s" % ("ALL PASS" if FAIL == 0 else "%d FAILED" % FAIL))
sys.exit(1 if FAIL else 0)
