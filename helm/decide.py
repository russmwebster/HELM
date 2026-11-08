"""s153 (Russ, 2026-11-07): "Deciding from here" -- the questions at the top of a put's card.

The questions Russ and the session used to decide on CSCO (11-07), answered from what HELM already
holds, READ-ONLY, nothing new computed beyond plain arithmetic on stored readings:
  1. HELM's advice   -- helm.advice.advise, the same call Today makes (word for word)
  2. Would I own it? -- the latest ownership grade and its date; HELM's default if assigned
  3. If assigned     -- the cash, the shares at their effective basis vs today's price, the covered-call check
  4. Keep vs switch  -- time value left (dollars, a yearly rate on the collateral, per unit of delta)
                        beside the best put that fits from the latest `helm puts` run
  5. In the book     -- sector total vs the 30% limit; this position's share of the book's delta
  6. The choices     -- close now at the ask, the latest `helm roll`, take the shares
The entry price and the loss so far are deliberately not factors (Russ, 2026-11-06).
CSPs only for now. `decide(position_id)` never writes; each part that can't be read says so.
"""
import sqlite3
from datetime import date

from helm import sleeve as S

DEFAULT_TAKE = ("A", "B", "C")          # advice: assignment accepted by default while the name grades A-C


def _usd(x, sign=False):
    if x is None:
        return "--"
    s = "{:,.0f}".format(abs(x))
    return ("-" if x < 0 else ("+" if sign else "")) + "$" + s


def _rc_cmd(t):
    return "cd ~/Projects/helm && helm recovery %s" % t


def recovery_view(conn, position_id, ticker, today):
    """s154: the card's numbers from the latest recorded `helm recovery` result (helm.recovery.latest_for):
    the 3-month and one-year odds and the year's rent, both cases. None when no run has recorded the put."""
    from helm import recovery as RC
    lt = RC.latest_for(conn, position_id)
    if lt is None:
        return {"none": True, "cmd": _rc_cmd(ticker)}
    day = lt["run_at"][:10]
    base = {"run_day": day, "stale": day != today.isoformat(), "cmd": _rc_cmd(ticker)}
    if lt["error"] or not lt["result"]:
        return dict(base, error=lt["error"] or "no result recorded")
    r = lt["result"]
    if "p_assign" not in r:                        # recorded before the run-up to expiry was modelled
        return dict(base, error="made by an earlier version of the model -- rerun")
    H = r["horizons"]
    i90, iy = H.index(90), H.index(365)

    def case(c):
        x = r[c]
        return {"touch90": x["touch"][i90], "touchY": x["touch"][iy], "whole90": x["whole"][i90],
                "wholeY": x["whole"][iy], "called": x["called"], "gate_day": x["gate_day"],
                "rentY": x["rent"][iy], "rentY_usd": x["rent"][iy] * r["shares"]}
    f = r["fit"]
    return dict(base, spot=r["spot"], basis=r["basis"], shares=r["shares"], start=r["start"], strike=r.get("strike"),
                p_assign=r.get("p_assign"), assigned_price=r.get("assigned_price"), pre_earn=r.get("pre_earn") or [],
                modelled=r.get("now") is not None,
                now=case("now") if r.get("now") else None, calm=case("calm") if r.get("calm") else None,
                vol_now=f["start_vol"], vol_lr=f["long_run_vol"], half=f["half_life_days"],
                capped=f.get("half_life_capped"), earn_note=r["earn_note"], short_history=f["short_history"])


def opportunity_view(conn, position_id, ticker, today, card_target=None):
    """s154: keep vs switch from the latest recorded `helm recovery` result (helm.opportunity.compare). Flags a
    run that compared a different name than the card's best put that fits now."""
    from helm import recovery as RC
    lt = RC.latest_for(conn, position_id)
    if lt is None:
        return {"none": True, "cmd": _rc_cmd(ticker)}
    day = lt["run_at"][:10]
    base = {"run_day": day, "stale": day != today.isoformat(), "cmd": _rc_cmd(ticker)}
    r = lt["result"] or {}
    if lt["error"] or r.get("opportunity_error") or not r.get("opportunity"):
        return dict(base, error=lt["error"] or r.get("opportunity_error") or "keep vs switch was not part of that run")
    o = r["opportunity"]
    sw = o["switch"]
    rows = []
    for hz, name in ((90, "3 months"), (365, "a year")):
        rows.append({"name": name, "keep": o["keep_%d" % hz], "switch": o["switch_%d" % hz], "gap": o["gap_%d" % hz]})
    return dict(base, base_value=o["base"], ask=o["ask"], target=sw["ticker"], sector=sw["sector"], grade=sw["grade"],
                list_run=sw["run_at"], rows=rows, mismatch=bool(card_target) and card_target != sw["ticker"],
                card_target=card_target, keep_earn=o.get("keep_earn") or [])


def put_numbers(strike, n, premium, bid, ask, mid, spot, delta, dte):
    """Plain arithmetic for one short put (per-share inputs; n contracts). Pure; the self-test drives it."""
    out = {"strike": strike, "n": n, "collateral": strike * 100 * n,
           "eff_basis": strike - (premium or 0.0)}
    out["intrinsic"] = max(strike - spot, 0.0) if spot is not None else None
    out["tv"] = (max(mid - out["intrinsic"], 0.0) if (mid is not None and out["intrinsic"] is not None) else None)
    out["tv_usd"] = out["tv"] * 100 * n if out["tv"] is not None else None
    out["rate"] = (out["tv"] / strike * 365.0 / dte) if (out["tv"] is not None and dte and dte > 0) else None
    d = abs(delta) if delta is not None else None
    out["delta"] = d
    out["rate_per_delta"] = (out["rate"] / d) if (out["rate"] is not None and d) else None
    out["close_ask_usd"] = ask * 100 * n if ask is not None else None
    out["close_mid_usd"] = mid * 100 * n if mid is not None else None   # s155: the board's one close figure (mid)
    out["spread_usd"] = (ask - mid) * 100 * n if (ask is not None and mid is not None) else None
    out["shares"] = 100 * n
    out["shares_value"] = spot * 100 * n if spot is not None else None
    out["paper_if_assigned"] = ((spot - out["eff_basis"]) * 100 * n) if spot is not None else None
    out["below_basis_pct"] = ((out["eff_basis"] - spot) / out["eff_basis"] * 100.0) if spot is not None else None
    return out


def decide(position_id, account_id=None, db=None, today=None):
    """The card's top section for one open CSP, or None for anything else. Never raises past a part:
    a part that fails carries {"error": ...} and the rest still renders."""
    today = today or date.today()
    conn = S._conn(db)
    try:
        pos = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        if pos is None or str(pos["strategy"]).upper() != "CSP" or pos["status"] != "OPEN":
            return None
        pos = dict(pos)
        t = pos["ticker"]
        leg = conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN' AND direction='SHORT' "
                           "AND option_type='PUT'", (position_id,)).fetchone()
        if leg is None:
            return None
        lc = conn.execute("SELECT checked_at, current_bid, current_ask, current_price, delta, theta, iv_current "
                          "FROM leg_checks WHERE leg_id=? ORDER BY checked_at DESC LIMIT 1", (leg["id"],)).fetchone()
        ck = conn.execute("SELECT spot_price, checked_at FROM checks WHERE position_id=? AND data_quality='GOOD' "
                          "ORDER BY checked_at DESC LIMIT 1", (position_id,)).fetchone()
        spot = ck["spot_price"] if ck else None
        exp = date.fromisoformat(str(leg["expiration"])[:10])
        dte = (exp - today).days
        num = put_numbers(leg["strike"], leg["contracts"], leg["open_price"],
                          lc["current_bid"] if lc else None, lc["current_ask"] if lc else None,
                          lc["current_price"] if lc else None, spot, lc["delta"] if lc else None, dte)
        out = {"ticker": t, "position_id": position_id, "num": num, "dte": dte, "spot": spot,
               "iv": (lc["iv_current"] if lc else None),     # s155: the facts row's IV (the leg's latest reading)
               "asof": (lc["checked_at"][:16].replace("T", " ") if lc else None), "expiry": str(leg["expiration"])[:10]}

        # 1. HELM's advice -- the same engine call Today makes
        try:
            from helm import advice
            rec = next((r for r in advice.advise(db=db, account_id=account_id or pos.get("account_id"))
                        if r.get("position_id") == position_id), None)
            out["advice"] = ({"headline": rec["headline"], "reason": rec.get("reason") or "",
                              "act": rec.get("act") or "", "lean": rec.get("lean") or ""} if rec
                             else {"error": "no advice line for this position"})
            out["rules"] = list(rec.get("rules") or []) if rec else []   # s155: the same rows, as data
        except Exception as e:                     # noqa: BLE001
            out["advice"] = {"error": "%s: %s" % (type(e).__name__, e)}

        # 2. Would I own it?
        g = conn.execute("SELECT grade, date FROM ownership_quality WHERE ticker=? ORDER BY date DESC, updated_at DESC "
                         "LIMIT 1", (t,)).fetchone()
        fund = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='approved_funds'").fetchone() and \
            conn.execute("SELECT 1 FROM approved_funds WHERE ticker=?", (t,)).fetchone()
        grade = "FUND" if fund else (g["grade"] if g else None)
        out["own"] = {"grade": grade, "graded": (g["date"] if g and not fund else None),
                      "default_take": bool(fund) or grade in DEFAULT_TAKE}

        # 3. If assigned -- the covered-call rule: strike >= effective basis (helm calls)
        out["assigned"] = {"cost": num["collateral"], "shares": num["shares"], "eff_basis": num["eff_basis"],
                           "value": num["shares_value"], "paper": num["paper_if_assigned"],
                           "below_basis_pct": num["below_basis_pct"]}

        # 3b. s154 (Russ, 2026-11-08): the odds if assigned -- the latest `helm recovery` run, read only
        try:
            out["assigned"]["recovery"] = recovery_view(conn, position_id, t, today)
        except Exception as e:                     # noqa: BLE001
            out["assigned"]["recovery"] = {"error": "%s: %s" % (type(e).__name__, e), "cmd": _rc_cmd(t)}

        # 4. Keep vs switch -- the best put that fits from the latest `helm puts` run
        try:
            from helm import puttool as P
            run = P.latest(conn)
            best = None
            if run:
                best = next((c for c in run["candidates"] if c.get("size_n") and c["ticker"] != t), None)
            held = {r[0] for r in conn.execute("SELECT ticker FROM positions WHERE book='REAL' AND status='OPEN'")}
            out["switch"] = ({"run_at": run["run_at"][:16], "ticker": best["ticker"], "grade": best["grade"],
                              "held": best["ticker"] in held,
                              "same_sector": best["sector"] == S.sector_of(conn, t)[0],
                              "sector": best["sector"], "expiry": best["expiry"], "strike": best["strike"],
                              "rank": best["rank"], "rate": best["ann_return"], "delta": best["delta"],
                              "rate_per_delta": (best["ann_return"] / best["delta"]) if best["delta"] else None}
                             if best else {"nothing": True, "run_at": run["run_at"][:16] if run else None})
        except Exception as e:                     # noqa: BLE001
            out["switch"] = {"error": "%s: %s" % (type(e).__name__, e)}
        # 4b. s154 (Russ, 2026-11-08): keep vs switch, modelled -- the latest `helm recovery` run, read only
        try:
            out["switch_model"] = opportunity_view(conn, position_id, t, today, (out.get("switch") or {}).get("ticker"))
        except Exception as e:                     # noqa: BLE001
            out["switch_model"] = {"error": "%s: %s" % (type(e).__name__, e), "cmd": _rc_cmd(t)}

        # 5. In the book -- sector vs 30%; share of the book's beta-weighted delta (helm.bookview)
        try:
            b = S.book(account_id=account_id or pos.get("account_id"), db=db)
            sec = b["sector_of_name"].get(t) or S.sector_of(conn, t)[0]
            sec_used = b["by_sector"].get(sec, 0.0)
            out["book"] = {"sector": sec, "sector_used": sec_used, "sector_cap": b["sector_cap"],
                           "sector_over": sec_used > b["sector_cap"] + 1e-6}
            try:
                from helm import bookview
                v = bookview.view(account_id or pos.get("account_id"))
                mine = next((p for p in v["positions"] if p.get("position_id") == position_id), None)
                tot = v["totals"]["bw_delta"]
                lim = (v.get("limits") or {}).get("delta_cap") or (b["share"] / 2.0)
                mbw = mine.get("bw_delta") if mine else None   # s154: None until the put's first reading
                out["book"].update({"bw_delta": mbw, "book_bw_delta": tot, "delta_limit": lim,
                                    "delta_share": (mbw / tot * 100.0) if (mbw is not None and tot) else None})
            except Exception as e:                 # noqa: BLE001
                out["book"]["delta_error"] = "%s: %s" % (type(e).__name__, e)
        except Exception as e:                     # noqa: BLE001
            out["book"] = {"error": "%s: %s" % (type(e).__name__, e)}

        # 6. The choices -- close at the ask; the latest `helm roll`; take the shares
        roll = None
        try:
            from helm import rolltool
            r = rolltool.latest_for(conn, [position_id]).get(position_id)
            if r:
                first = r["rolls"][0] if r["rolls"] else None
                roll = {"run_at": r["run_at"][:16], "old": r["run_at"][:10] < today.isoformat(),
                        "why_none": r.get("why_none"),
                        "first": ({"expiry": first["expiry"], "strike": first["strike"],
                                   "net_credit": first.get("net_credit"), "delta": first.get("delta")} if first else None)}
        except Exception as e:                     # noqa: BLE001
            roll = {"error": "%s: %s" % (type(e).__name__, e)}
        out["choices"] = {"close_ask": num["close_ask_usd"], "spread_usd": num["spread_usd"], "roll": roll,
                          "take_cost": num["collateral"], "close_mid": num["close_mid_usd"]}
        # s155: the book's delta limit as a row beside the advice's rules (helm.advice.limit_row -- one comparison)
        bk = out.get("book") or {}
        if bk.get("book_bw_delta") is not None and bk.get("delta_limit"):
            from helm import advice as _A
            out.setdefault("rules", []).append(_A.limit_row("book_delta", "Book delta limit (half the sleeve)",
                                                            abs(bk["book_bw_delta"]), bk["delta_limit"]))
        return out
    finally:
        conn.close()


def _selftest():
    fails = []

    def ok(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)
    # CSCO 11/20 105 x3, sold at 2.28; 11-06 15:15: bid 11.24 ask 11.93 mid 11.585, spot 94.39, delta -0.78, 14 days
    n = put_numbers(105.0, 3, 2.28, 11.24, 11.93, 11.585, 94.39, -0.78, 14)
    ok(n["collateral"] == 31500.0 and abs(n["eff_basis"] - 102.72) < 1e-9, "collateral and effective basis")
    ok(abs(n["intrinsic"] - 10.61) < 1e-9 and abs(n["tv"] - 0.975) < 1e-9, "intrinsic and time value per share")
    ok(abs(n["tv_usd"] - 292.5) < 1e-6, "time value left in dollars (x100 x contracts)")
    ok(abs(n["rate"] - 0.975 / 105 * 365 / 14) < 1e-12, "yearly rate on the collateral")
    ok(abs(n["rate_per_delta"] - n["rate"] / 0.78) < 1e-12 and n["delta"] == 0.78, "rate per unit of delta, delta positive")
    ok(abs(n["close_ask_usd"] - 3579.0) < 1e-6 and abs(n["spread_usd"] - 103.5) < 1e-6, "close at the ask; giving up the spread")
    ok(abs(n["paper_if_assigned"] - (94.39 - 102.72) * 300) < 1e-6 and n["shares"] == 300, "paper result if assigned")
    ok(abs(n["below_basis_pct"] - (102.72 - 94.39) / 102.72 * 100) < 1e-9, "how far below the basis")
    o = put_numbers(165.0, 3, 9.51, 9.0, 9.4, 9.2, 183.0, -0.29, 41)
    ok(o["intrinsic"] == 0.0 and abs(o["tv"] - 9.2) < 1e-9 and o["paper_if_assigned"] > 0, "out of the money: all time value")
    z = put_numbers(100.0, 1, 1.0, None, None, None, None, None, 0)
    ok(z["tv"] is None and z["rate"] is None and z["close_ask_usd"] is None, "no reading: nothing invented")
    print("decide selftest:", "PASS (%d)" % 10 if not fails else "%d FAIL" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
