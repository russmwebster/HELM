"""s156 (Russ, 2026-11-08; board cleanup step 5): the Details and Watchlist pages' tables, as data, built to the
display rules. Details reads helm.bookview (stored readings) and helm.sleeve; the near / over states are
helm.advice.limit_row's -- the same comparison the advice and Today use. The Watchlist reads the watchlist,
the grades, earnings_history and the open positions. READ-ONLY. The board renders; it computes nothing.
"""
from datetime import date

from helm import advice as A
from helm import sleeve as S


def share_text(used, cap):
    """A use of a limit as a percentage: whole numbers, one decimal when rounding would cross 90% or 100%."""
    if not cap:
        return "—"
    x = used / cap * 100.0
    r = round(x)
    if (x < 90 <= r) or (x < 100 <= r) or (x > 100 >= r and x != 100):
        return "%.1f%%" % x
    return "%d%%" % r


def details(account_id=None, db=None, today=None):
    from helm import bookview
    v = bookview.view(account_id, db)
    s = v["sleeve"]
    L = v["limits"]
    out = {"asof": v["as_of"], "market": v["market"], "stress": v["stress"], "totals": v["totals"], "limits": L}
    lo, hi = (v["as_of"] or (None, None))
    out["clock"] = ("%s %s" % (date.fromisoformat(hi[:10]).strftime("%a %m-%d"), hi[11:16])) if hi else None
    out["older"] = (lo[:10] if (lo and hi and lo[:10] != hi[:10]) else None)
    out["figures"] = {"sleeve": s["share"], "realized": s["realized"], "fees": s.get("fees"), "start": s["start"],
                      "in_use": s["used"], "room": s["free"]}

    def lim(key, label, used, cap):
        r = A.limit_row(key, label, used, cap)
        return {"name": label, "used": used, "share": share_text(used, cap), "state": r["state"]}
    out["names"] = sorted((lim("name_limit", t, u, s["name_cap"]) for t, u in s["by_name"].items()),
                          key=lambda r: (-r["used"], r["name"]))
    out["sectors"] = sorted((lim("sector_limit", t, u, s["sector_cap"]) for t, u in s["by_sector"].items()),
                            key=lambda r: (-r["used"], r["name"]))
    out["name_cap"], out["sector_cap"] = s["name_cap"], s["sector_cap"]
    out["delta_state"] = A.limit_row("book_delta", "", abs(L["bw_delta"]), L["delta_cap"])["state"]
    out["theta_state"] = L["theta_state"]
    acct = account_id
    out["positions"] = A.in_order(v.get("positions") or [], A.position_keys(db=db, account_id=acct))
    out["orders"] = sorted(v.get("orders") or [],
                           key=lambda o: A.order_key(o.get("ticker"), o.get("strategy"), o.get("expiration")))
    exps = []
    for e in v.get("expiries") or []:
        legs = sorted(e["legs"], key=lambda l: A.order_key(l["ticker"], l.get("strategy")))
        what = " · ".join("%s %g %s ×%d" % (l["ticker"], l["strike"] or 0, "C" if "CALL" in (l["role"] or "") else "P",
                                           l["contracts"]) for l in legs)
        longs = all("LONG" in (l["role"] or "") for l in legs)
        exps.append({"date": e["date"], "frees": e["frees"], "what": what + (" (long)" if longs and len(legs) == 1 else
                                                                            " (longs)" if longs else "")})
    out["expiries"] = exps
    curve = {round(c["move"] * 100): c["pnl"] for c in v["market"].get("curve") or []}
    out["moves"] = [(m, curve.get(m)) for m in (-10, -5, -2)] + [("typical", None)] + [(m, curve.get(m)) for m in (2, 5, 10)]
    return out


def watchlist(db=None, today=None):
    from helm import puttool as P
    today = today or date.today()
    conn = S._conn(db)
    try:
        wl = [dict(r) for r in conn.execute("SELECT ticker, company_name, sector, next_earnings FROM watchlist "
                                            "WHERE COALESCE(active, 1)=1 ORDER BY ticker")]
        funds = {r["ticker"]: r["bucket"] for r in conn.execute("SELECT * FROM approved_funds")} \
            if S._has_table(conn, "approved_funds") else {}
        grades = {}
        for r in conn.execute("SELECT q.ticker, q.grade, q.date FROM ownership_quality q JOIN (SELECT ticker, MAX(date) d "
                              "FROM ownership_quality GROUP BY ticker) m ON m.ticker=q.ticker AND m.d=q.date "
                              "ORDER BY q.updated_at"):
            grades[r["ticker"]] = (r["grade"], r["date"])
        hist = {}
        if S._has_table(conn, "earnings_history"):
            for r in conn.execute("SELECT ticker, MAX(earn_date) d FROM earnings_history WHERE earn_date <= ? GROUP BY ticker",
                                  (today.isoformat(),)):
                hist[r["ticker"]] = r["d"]
        held = {}
        for r in conn.execute("SELECT ticker, strategy FROM positions WHERE book='REAL' AND status='OPEN' ORDER BY strategy"):
            held.setdefault(r["ticker"], []).append(r["strategy"])
        if S._has_table(conn, "stock_positions"):
            for r in conn.execute("SELECT ticker FROM stock_positions WHERE shares > 0"):
                held.setdefault(r["ticker"], []).append("shares")
    finally:
        conn.close()
    rows = []
    for w in wl:
        t = w["ticker"]
        fund = t in funds
        g, gd = ("FUND", None) if fund else grades.get(t, (None, None))
        ne = (w["next_earnings"] or "")[:10] or None
        nxt = ne if (ne and ne >= today.isoformat()) else None
        last = max([d for d in (hist.get(t), ne if (ne and not nxt) else None) if d] or [None]) if not fund else None
        days = (date.fromisoformat(nxt) - today).days if nxt else None
        rows.append({"ticker": t, "company": w["company_name"] or None, "sector": funds.get(t) if fund else w["sector"],
                     "grade": g, "graded": gd, "offered": g in A.PASSING, "fund": fund,
                     "last_report": last, "next_report": nxt, "report_soon": days is not None and days <= P.EARN_VETO_DAYS,
                     "held": " · ".join(sorted(set(held.get(t, [])), key=lambda x: (x == "shares", x))) or None})
    counts = {"names": len(rows), "offered": sum(r["offered"] for r in rows),
              "graded_ac": sum(1 for r in rows if r["grade"] in ("A", "B", "C")), "funds": sum(r["fund"] for r in rows),
              "held": sum(1 for r in rows if r["held"]),
              "not_known": sum(1 for r in rows if not r["fund"] and not r["next_report"])}
    return {"rows": rows, "counts": counts, "asof": today.strftime("%a %m-%d"),
            "sectors": sorted({r["sector"] for r in rows if r["sector"]})}


def _selftest():
    fails = []

    def ok(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)
    ok(share_text(79500, 88713.117) == "89.6%" and share_text(80790, 88713.117) == "91%", "Industrials 89.6%, Technology 91%")
    ok(share_text(99.6, 100) == "99.6%" and share_text(100.4, 100) == "100.4%" and share_text(50, 100) == "50%",
       "one decimal only where rounding crosses 90% or 100%")
    print("boardview selftest:", "PASS (2)" if not fails else "%d FAIL" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
