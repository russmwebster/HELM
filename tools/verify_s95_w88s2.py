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
check("healed fray is remembered", b["state"] == T.HOLDS and "healed" in b["now"], b["now"][-70:])
b = strike_state(CSP, [])
check("no history = UNKNOWN, never invented", b["state"] == T.UNKNOWN)

print("== condor honesty ==")
c = T.evaluate(pos("IRON_CONDOR"), COND, [chk("2026-07-30", 110.0, pnl=120.0)])
check("condor card flags honesty", c["condor_honesty"] is True)
check("condor fine print disclaims prediction",
      "no predictive separation" in c["beliefs"][0]["fine_print"])
check("condor title is the corridor", "corridor" in c["beliefs"][0]["title"])
check("condor odds greyed (W27)", "not captured" in c["beliefs"][0]["extra"].get("odds",""))
check("condor gets expiry ladder", c["ladder"] is not None and len(c["ladder"]) == 5)
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

print("== %d passed, %d failed ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
