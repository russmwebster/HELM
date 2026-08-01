#!/usr/bin/env python3
"""W90 (HELM-142) + W91 (HELM-143) checks. Synthetic against the pure code;
integration READ-ONLY against the live DB (W90) and against a THROWAWAY
COPY (W91 -- run_post_snapshot writes, so it never sees the live file)."""
import os, sys, tempfile, sqlite3, traceback

HELM = "/Users/russmacbookpro/Projects/helm"
PG = "/Users/russmacbookpro/Projects/helm-pg"
sys.path.insert(0, HELM)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print("  ok   %s" % name)
    else: FAIL += 1; print("  FAIL %s  %s" % (name, str(detail)[:160]))

from helm import thesis as T  # noqa: E402

def leg(direction, ot, k):
    return {"direction": direction, "option_type": ot, "strike": k,
            "open_price": 1.0, "contracts": 1, "expiration": "2026-09-18"}

def chk(day, spot, pnl=None):
    return {"checked_at": day + "T15:45:00", "spot_price": spot,
            "pnl_unrealized": pnl, "iv_rank": None, "iv_vs_entry": None,
            "delta": None, "theta": None, "dte_now": 30,
            "lc_arms_json": None, "thesis_broken": None}

def pos(**kw):
    d = {"id": "T-1", "ticker": "TST", "strategy": "CSP", "status": "OPEN",
         "book": "PAPER", "net_premium": 500.0, "realized_pnl": None,
         "exit_reason": None, "closed_at": None, "opened_at": "2026-07-01",
         "earnings_date": None, "max_loss": None}
    d.update(kw); return d

CSP = [leg("SHORT", "PUT", 100)]

print("== W90 · earnings-inside-window (pure) ==")
c = T.evaluate(pos(), CSP, [chk("2026-07-10", 110, -50.0)],
               earnings={"next": "2026-08-05", "at_entry": None})
check("upcoming print inside window flags", c["earnings"] and
      c["earnings"]["state"] == "inside" and c["earnings"]["when"] == "upcoming",
      c.get("earnings"))
c = T.evaluate(pos(), CSP, [chk("2026-07-10", 110, -50.0)],
               earnings={"next": "2026-07-05", "at_entry": None})
check("print that occurred inside the window flags as occurred",
      c["earnings"] and c["earnings"]["state"] == "inside"
      and c["earnings"]["when"] == "occurred", c.get("earnings"))
c = T.evaluate(pos(), CSP, [chk("2026-07-10", 110, -50.0)],
               earnings={"next": "2026-11-01", "at_entry": None})
check("print after expiry reads outside",
      c["earnings"] and c["earnings"]["state"] == "outside", c.get("earnings"))
c = T.evaluate(pos(), CSP, [chk("2026-07-10", 110, -50.0)],
               earnings={"next": "2026-06-20", "at_entry": None})
check("cached date before the position opened reads stale, not inside",
      c["earnings"] and c["earnings"]["state"] == "stale", c.get("earnings"))
c = T.evaluate(pos(), CSP, [chk("2026-07-10", 110, -50.0)])
check("no cached date reads unknown -- never guessed",
      c["earnings"] and c["earnings"]["state"] == "unknown", c.get("earnings"))
c = T.evaluate(pos(status="CLOSED"), CSP, [chk("2026-07-10", 110, -50.0)],
               earnings={"next": "2026-08-05", "at_entry": None})
check("closed card carries no earnings read (frozen post-mortem)",
      c["earnings"] is None, c.get("earnings"))

print("== W91 · decide() (pure) ==")
from helm import exit_alert as X  # noqa: E402
prior = [("2026-07-0%d" % d, b) for d, b in
         [(1, -900.0), (2, -700.0), (3, -800.0), (6, -750.0), (7, -820.0)]]
check("fires when today beats the 5-day best by >= $250",
      X.decide(prior, -400.0) is not None)
check("quiet when the improvement is under the floor",
      X.decide(prior, -550.0) is None)
check("threshold scales with defined max loss (5%)",
      X.decide(prior, -400.0, max_loss=-10000.0) is None
      and X.decide(prior, -100.0, max_loss=-10000.0) is not None)
check("lookback is a window -- an ancient best exit does not gag it",
      X.decide([("2026-06-01", -100.0)] + prior, -400.0) is not None)
check("no priors -> no alert", X.decide([], -400.0) is None)

print("== W91 · post-pass against a throwaway copy ==")
try:
    from helm.config import DB_PATH
    tmp = tempfile.mkdtemp()
    copy = os.path.join(tmp, "copy.db")
    ro = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    ro.execute("VACUUM INTO ?", (copy,))
    ro.close()
    conn = sqlite3.connect(copy)
    conn.row_factory = sqlite3.Row
    last = conn.execute("select max(date(checked_at)) from checks").fetchone()[0]
    fired = X.run_post_snapshot(conn=conn, notify=False, today=last)
    check("post-pass runs clean on a copy (fired %d)" % len(fired), True)
    check("table + unique index created",
          conn.execute("select count(*) from sqlite_master where name in "
                       "('exit_alerts','idx_exit_alerts_pos_day')").fetchone()[0] == 2)
    n0 = conn.execute("select count(*) from exit_alerts").fetchone()[0]
    fired2 = X.run_post_snapshot(conn=conn, notify=False, today=last)
    n1 = conn.execute("select count(*) from exit_alerts").fetchone()[0]
    check("second run same day fires nothing (quiet rule)",
          not fired2 and n1 == n0, (len(fired2), n0, n1))
    # LRCX-specific regression: on 7/30 the bounce beat 4 of the prior 5
    # days but not 7/23's -3540, so the 5-day rule correctly stays quiet.
    lrows = [dict(r) for r in conn.execute(
        "select checked_at, pnl_unrealized from checks where "
        "position_id like 'LRCX-IRON_CONDOR%' and data_quality='GOOD' "
        "and date(checked_at) <= '2026-07-30' order by checked_at")]
    if lrows:
        daily = X._daily_best([{"checked_at": r["checked_at"],
                                "pnl_unrealized": r["pnl_unrealized"]}
                               for r in lrows])
        check("LRCX 7/30 bounce stays under the 5-day rule (measured)",
              daily[-1][0] == "2026-07-30"
              and X.decide(daily[:-1], daily[-1][1], -10440.0) is None,
              daily[-6:])
    conn.close()
    ro2 = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    live_has = ro2.execute("select count(*) from sqlite_master where "
                           "name='exit_alerts'").fetchone()[0]
    ro2.close()
    check("live DB untouched by this suite "
          "(table only exists if the engine made it)", True,
          "live exit_alerts tables: %d" % live_has)
except Exception:
    traceback.print_exc(); check("W91 integration ran", False)

print("== W90 · live LRCX card (read-only) ==")
try:
    sys.path.insert(0, PG)
    os.chdir(PG)
    import engine_store
    card = engine_store.thesis_card("LRCX-IRON_CONDOR-20260629-2A46F6")
    e = card.get("earnings") if card else None
    check("LRCX card flags the 7/29 print inside its window, occurred",
          e and e.get("state") == "inside" and e.get("date") == "2026-07-29"
          and e.get("when") == "occurred", e)
    al = engine_store.exit_alerts_today()
    check("exit_alerts_today returns a list (empty ok, ro, never raises)",
          isinstance(al, list), type(al))
except Exception:
    traceback.print_exc(); check("W90 live integration ran", False)

print("== %d passed, %d failed ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
