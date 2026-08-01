#!/usr/bin/env python3
"""W88 slice 2 checks — helm/thesis.py (pure evaluator) + PG thesis_card assembly.

Synthetic checks run against the pure module; the integration checks read the
LIVE DB READ-ONLY through engine_store.thesis_card. Nothing writes anywhere.
"""
import os, sys, json, traceback

HELM = "/Users/russmacbookpro/Projects/helm"
PG = "/Users/russmacbookpro/Projects/helm-pg"
sys.path.insert(0, HELM)

from helm import thesis as T  # noqa: E402

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print("  ok   %s" % name)
    else: FAIL += 1; print("  FAIL %s  %s" % (name, str(detail)[:160]))

def leg(direction, ot, k, open_price=1.0, contracts=1):
    return {"direction": direction, "option_type": ot, "strike": k,
            "open_price": open_price, "contracts": contracts, "expiration": "2026-09-18"}

def chk(day, spot, pnl=None, **kw):
    d = {"checked_at": day + "T15:45:00", "spot_price": spot, "pnl_unrealized": pnl,
         "iv_rank": None, "iv_vs_entry": None, "delta": None, "theta": None,
         "dte_now": 30, "lc_arms_json": None, "thesis_broken": None}
    d.update(kw); return d

def pos(strategy, status="OPEN", **kw):
    d = {"id": "T-1", "ticker": "TST", "strategy": strategy, "status": status,
         "book": "PAPER", "net_premium": 500.0, "realized_pnl": None,
         "exit_reason": None, "closed_at": None}
    d.update(kw); return d

CSP = [leg("SHORT", "PUT", 100)]
COND = [leg("SHORT","PUT",100), leg("LONG","PUT",95), leg("SHORT","CALL",120), leg("LONG","CALL",125)]
for _c in (COND[0], COND[2]):
    _c["open_price"] = 1.5   # net credit 1.0 -> break-evens 99 / 121, not a degenerate zero-credit payoff

print("== purity ==")
src = open(os.path.join(HELM, "helm", "thesis.py")).read()
check("thesis.py is DB-free", "sqlite3" not in src and "DB_PATH" not in src)

print("== strike belief states (slice-1 calibration) ==")
def strike_state(legs, days):
    c = T.evaluate(pos("CSP" if len(legs) == 1 else "IRON_CONDOR"), legs,
                   [chk(d, s) for d, s in days])
    return c["beliefs"][0]

b = strike_state(CSP, [("2026-07-29", 105.0), ("2026-07-30", 105.5)])
check("holds at 5%", b["state"] == T.HOLDS, b["state"])
b = strike_state(CSP, [("2026-07-29", 105.0), ("2026-07-30", 100.6)])
check("amber band 0-1% frays", b["state"] == T.FRAYING, b["state"])
b = strike_state(CSP, [("2026-07-29", 105.0), ("2026-07-30", 99.0)])
check("one breached day frays (not broken)", b["state"] == T.FRAYING, b["state"])
b = strike_state(CSP, [("2026-07-28", 105.0), ("2026-07-29", 99.5), ("2026-07-30", 99.0)])
check("breach x2 = broken", b["state"] == T.BROKEN, b["state"])
b = strike_state(CSP, [("2026-07-28", 99.9), ("2026-07-29", 99.5), ("2026-07-30", 99.0)])
check("breach x3 = loud", b["state"] == T.BROKEN_LOUD, b["state"])
b = strike_state(CSP, [("2026-07-30", 97.0)])
check("<-2% = loud on one day", b["state"] == T.BROKEN_LOUD, b["state"])
b = strike_state(CSP, [("2026-07-28", 100.5), ("2026-07-30", 106.0)])
check("recovered dip is remembered", b["state"] == T.HOLDS and "recovered" in b["now"], b["now"][-70:])
b = strike_state(CSP, [])
check("no history = UNKNOWN, never invented", b["state"] == T.UNKNOWN)

print("== condor honesty ==")
c = T.evaluate(pos("IRON_CONDOR"), COND, [chk("2026-07-30", 110.0, pnl=120.0)])
check("condor card flags honesty", c["condor_honesty"] is True)
check("condor fine print disclaims prediction",
      "no predictive separation" in c["beliefs"][0]["fine_print"])
check("condor title states both strikes", "between" in c["beliefs"][0]["title"], c["beliefs"][0]["title"])
check("condor odds greyed (W27)", "not captured" in c["beliefs"][0]["extra"].get("odds",""))
check("condor gets expiry ladder", c["ladder"] is not None and len(c["ladder"]) >= 5)
check("ladder carries break-even rows",
      sum(1 for r in (c["ladder"] or []) if r["where"] == "break-even") == 2)
check("card carries breakevens 99/121", c.get("breakevens") == [99.0, 121.0], c.get("breakevens"))
_z = [dict(l, open_price=1.0) for l in COND]
check("zero-credit degenerate payoff yields no break-even noise",
      len(T.breakevens(_z)) <= 2, T.breakevens(_z))
check("convergence line present", c["convergence"] is not None and "/week" in c["convergence"])
bad = T.evaluate(pos("IRON_CONDOR"), [leg("SHORT","PUT",100), leg("SHORT","CALL",None)],
                 [chk("2026-07-30", 110.0)])
check("unreadable wall = UNKNOWN", bad["beliefs"][0]["state"] == T.UNKNOWN)

print("== longs / direction ==")
arms = json.dumps({"thesis": {"armed": True, "broken_today": True, "streak": 2,
                              "confirm_days": 2, "entry_bias": 2.0, "cur_bias": 0.0}})
et = {"bias_score": 2.0, "spot_price": 100.0, "sma_50": 98.0}
c = T.evaluate(pos("LONG_CALL"), [leg("LONG","CALL",110)],
               [chk("2026-07-30", 100.0, lc_arms_json=arms)], entry_thesis_row=et)
check("confirmed break = BROKEN", c["beliefs"][0]["state"] == T.BROKEN, c["beliefs"][0]["state"])
check("read cues a decision", "decision today" in c["read"])
arms1 = json.dumps({"thesis": {"armed": True, "broken_today": True, "streak": 1,
                               "confirm_days": 2, "entry_bias": 2.0, "cur_bias": 0.5}})
c = T.evaluate(pos("LONG_CALL"), [leg("LONG","CALL",110)],
               [chk("2026-07-30", 100.0, lc_arms_json=arms1)], entry_thesis_row=et)
check("streak 1 of 2 = FRAYING", c["beliefs"][0]["state"] == T.FRAYING, c["beliefs"][0]["state"])
c = T.evaluate(pos("LONG_CALL"), [leg("LONG","CALL",110)], [chk("2026-07-30", 100.0)])
check("no entry thesis = never armed UNKNOWN", c["beliefs"][0]["state"] == T.UNKNOWN)

print("== premium / OWN ==")
snap = {"iv_rank": 67.7, "iv_current": 20.0, "hv_30d": 24.2}
c = T.evaluate(pos("CSP"), CSP, [chk("2026-07-30", 105.0)], entry_snap=snap)
check("contested at entry (JNJ shape)", c["beliefs"][1]["state"] == T.CONTESTED,
      c["beliefs"][1]["state"])
c = T.evaluate(pos("CSP"), CSP, [chk("2026-07-30", 105.0, iv_vs_entry=-5.0)],
               entry_snap={"iv_rank": 60.0, "iv_current": 30.0, "hv_30d": 25.0})
check("falling IV vindicates a sold premium", c["beliefs"][1]["state"] == T.VINDICATED)
c = T.evaluate(pos("CSP"), CSP, [chk("2026-07-30", 105.0)])
check("no snapshot = PARTIAL, not guessed", c["beliefs"][1]["state"] == T.PARTIAL)
c = T.evaluate(pos("CSP"), CSP, [chk("2026-07-30", 105.0)],
               ownership={"grade": "A", "confidence": "high", "updated_at": "2026-07-01"})
check("OWN grade A holds", c["beliefs"][2]["state"] == T.HOLDS)
c = T.evaluate(pos("CSP"), CSP, [chk("2026-07-30", 105.0)])
check("OWN absent = UNKNOWN", c["beliefs"][2]["state"] == T.UNKNOWN)

print("== closed positions freeze ==")
c = T.evaluate(pos("CSP", status="CLOSED", realized_pnl=-395.0, exit_reason="THESIS_BREAK",
                   closed_at="2026-07-30T15:59:00"), CSP, [chk("2026-07-30", 99.0, pnl=-380.0)])
check("closed card is a post-mortem", c["closed"] and "post-mortem" in c["read"])
check("closed card carries realized", c["realized"] == -395.0)
check("closed card gets no ladder", c["ladder"] is None)

print("== quiet card ==")
c = T.evaluate(pos("CSP"), CSP, [chk("2026-07-30", 108.0, pnl=-50.0)],
               entry_snap={"iv_rank": 60.0, "iv_current": 30.0, "hv_30d": 25.0},
               ownership={"grade": "A", "updated_at": "2026-07-01"})
check("all-holds card says sitting still is a decision",
      "sitting still" in c["read"] and c["summary"]["bad"] == 0)

print("== integration: live DB read-only via engine_store.thesis_card ==")
sys.path.insert(0, PG)
os.chdir(PG)
try:
    import engine_store
    import sqlite3
    from helm.config import DB_PATH
    ro = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    ids = {}
    ids["condor"] = ro.execute("select id from positions where strategy='IRON_CONDOR' and status='OPEN' limit 1").fetchone()
    ids["csp"] = ro.execute("select id from positions where strategy='CSP' and status='OPEN' and book='REAL' limit 1").fetchone()
    ids["thesis_break"] = ro.execute("select id from positions where exit_reason='THESIS_BREAK' limit 1").fetchone()
    ro.close()
    for label, row in ids.items():
        if not row:
            print("  (no %s position to test)" % label); continue
        card = engine_store.thesis_card(row[0])
        check("card builds for live %s (%s)" % (label, row[0]),
              card is not None and card["beliefs"] and card["summary"]["label"],
              card and card["summary"])
        if label == "thesis_break" and card:
            check("PNC-class card is frozen post-mortem", card["closed"] and card["exit_reason"] == "THESIS_BREAK")
    check("thesis_card returns None for junk id", engine_store.thesis_card("NOPE-123") is None)
except Exception:
    traceback.print_exc(); check("integration ran", False)


print("== exit tracking (s95 addendum) ==")
xchecks = [chk("2026-07-0%d" % d, s, pnl=p) for d, s, p in
           [(1, 105, -100.0), (2, 98, -400.0), (3, 97, -900.0),
            (6, 99, -300.0), (7, 96, -1200.0)]]
xc = T.evaluate(pos("CSP"), CSP, xchecks)
xt = xc.get("exit_track")
check("broken CSP carries exit_track", xt is not None, xc["summary"])
check("break confirmed on 2nd consecutive breach day",
      xt and xt["confirm_date"] == "2026-07-03", xt)
check("best exit since break is best journaled mark, dated",
      xt and xt["best"] == -300.0 and xt["best_date"] == "2026-07-06", xt)
check("latest and prior check days tracked",
      xt and xt["today"] == -1200.0 and xt["prev"] == -300.0, xt)
check("holding position has no exit_track",
      T.evaluate(pos("CSP"), CSP,
                 [chk("2026-07-01", 110, pnl=-50.0)]).get("exit_track") is None)
check("closed position has no exit_track",
      T.evaluate(pos("CSP", status="CLOSED"), CSP, xchecks).get("exit_track") is None)
check("broken but no journaled pnl -> None (not guessed)",
      T.evaluate(pos("CSP"), CSP,
                 [chk("2026-07-0%d" % d, 97) for d in (1, 2, 3)]).get("exit_track") is None)
try:
    ro3 = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    lrow = ro3.execute("select id from positions where ticker='LRCX' and "
                       "strategy='IRON_CONDOR' and status='OPEN' limit 1").fetchone()
    ro3.close()
    if lrow:
        lc = engine_store.thesis_card(lrow[0])
        lxt = lc.get("exit_track") if lc else None
        check("live LRCX broken card carries dated exit_track",
              bool(lxt) and lxt.get("best_date") and lxt.get("best") is not None, lxt)
    else:
        print("  (no open LRCX condor to test)")
except Exception:
    traceback.print_exc(); check("live exit_track ran", False)

print("== %d passed, %d failed ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
