"""helm/fillopens.py -- the Fidelity import, stage 2: new opens and rolls (s141).

Russ, 2026-10-31: trades enter HELM only from Fidelity downloads. This module
reads the opening fills that are not already in HELM and works out what they
are, from the fills themselves:

  * a put sold to open, alone on its ticker that day      -> CSP
  * a call sold to open where HELM holds uncovered shares -> COVERED_CALL
  * a call bought 270-730 days out (with or without a call
    sold the same day, expiring before it)                -> a new PMCC
  * a call sold on a ticker whose PMCC has no short open  -> that PMCC's next short
  * anything else (spreads, bought puts, long calls, a sold call that could be
    either a covered call or a PMCC short)                -> REPORT, not booked

A trade is a record, not a selection (standing rule): no selection rule hides
it. The money rules are checked and shown -- a limit the trade breaks, a grade
below C, a PMCC short that fails the width test -- as flags; the trade is done.

Rolls (Russ, 10-31): a close and a new open on the same ticker, option type and
day become the close plus a new position linked to the old one
(positions.parent_position_id).

Entry readings are read from IBKR when the booking is written (apply), never
from Yahoo, and labelled with when they were read and how long after the fill.
No spread rule is applied to them.

    classify(conn, opens, ctx) -> [Item]    read-only, no network
    book(item, db_path=None)   -> str       writes one open
"""
from __future__ import annotations

from datetime import date, datetime

PMCC_LONG_DTE = (270, 730)
STRATEGIES = ("CSP", "COVERED_CALL", "PMCC")


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _dte(exp, as_of):
    return (_d(exp) - _d(as_of)).days


def _half_up(x):
    from helm.bookview import _half_up as h
    return h(x)


def _money(x):
    return "${:,.0f}".format(x)


def _shares_free(conn, ticker, extra_shares=0):
    """HELM-held shares of `ticker` not already covered by an open covered call."""
    held = conn.execute("SELECT COALESCE(SUM(shares),0) FROM stock_positions WHERE ticker=?",
                        (ticker,)).fetchone()[0] if _has(conn, "stock_positions") else 0
    covered = conn.execute(
        "SELECT COALESCE(SUM(l.contracts),0) FROM legs l JOIN positions p ON p.id=l.position_id "
        "WHERE p.book='REAL' AND p.status='OPEN' AND p.strategy='COVERED_CALL' AND p.ticker=? "
        "AND l.status='OPEN' AND l.direction='SHORT'", (ticker,)).fetchone()[0]
    return int(held) + int(extra_shares) - 100 * int(covered)


def _has(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _open_pmccs(conn, ticker):
    """Open real PMCC/DIAGONAL-family positions on ticker with their legs."""
    out = []
    for p in conn.execute("SELECT * FROM positions WHERE book='REAL' AND status='OPEN' AND ticker=? "
                          "AND strategy IN ('PMCC','DIAGONAL')", (ticker,)).fetchall():
        legs = conn.execute("SELECT * FROM legs WHERE position_id=? ORDER BY open_date, id", (p["id"],)).fetchall()
        out.append((p, legs))
    return out


def width_test(legs, new_short_strike, new_short_contracts):
    """(passes, floor, long_strike). The strike gap must exceed what the long has
    cost net of rent banked, per share (rulebook v0.2 section 6)."""
    L = [l for l in legs if l["direction"] == "LONG" and l["status"] == "OPEN"]
    if not L:
        return None, None, None
    L = L[0]
    mult = (L["multiplier"] or 100)
    long_cost = (L["open_price"] or 0) * mult * (L["contracts"] or 0)
    rent = 0.0
    for l in legs:
        if l["direction"] == "SHORT" and l["status"] != "OPEN":
            m = (l["multiplier"] or 100) * (l["contracts"] or 0)
            rent += (l["open_price"] or 0) * m - (l["close_price"] or 0) * m
    floor = (long_cost - rent) / (mult * (L["contracts"] or 1))
    gap = float(new_short_strike) - float(L["strike"])
    return gap > floor, round(floor, 2), float(L["strike"])


def _freed(conn, position_id):
    """Collateral a rolled-out CSP gives back (its open short puts)."""
    if not position_id:
        return 0.0
    r = conn.execute("SELECT COALESCE(SUM(strike*100*contracts),0) FROM legs WHERE position_id=? "
                     "AND status='OPEN' AND direction='SHORT' AND option_type='PUT'", (position_id,)).fetchone()
    return float(r[0] or 0)


def _flags(conn, ticker, strategy, collateral, freed=0.0):
    """Plain-word flags for a trade already made: limits it breaks, grade.
    A trade that adds nothing to what's in use (a covered call, a PMCC short)
    can't break a limit. A roll gives back the collateral it closes (freed)."""
    out = []
    try:
        from helm import sleeve as S
        r = S.room_for(ticker, db=None) if collateral > 0 else {"rooms": {}}
        for k, room in r["rooms"].items():
            if collateral > room + freed + 1e-6:
                out.append("over a limit: " + S.limit_text(k, r) + " -- this trade uses %s" % _money(collateral))
    except Exception as e:                                  # never block a record
        out.append("limits not checked (%s)" % str(e)[:60])
    try:
        from helm.ownership import read_cached_grade
        g = read_cached_grade(ticker)
        grade = (g or {}).get("grade") if isinstance(g, dict) else (g["grade"] if g else None)
        fund = conn.execute("SELECT 1 FROM approved_funds WHERE ticker=?", (ticker,)).fetchone() \
            if _has(conn, "approved_funds") else None
        if fund:
            grade = "FUND"
        if grade not in ("A", "B", "C", "FUND"):
            out.append("grade %s -- the rules offer only A-C or approved funds" % (grade or "none"))
    except Exception:
        grade = None
    return out, grade


def classify(conn, opens, ctx):
    """opens: [(gk, g, fills)] for opening fills (>= cutoff) not already in HELM,
    gk = (event, as_of, ticker, exp, otype, strike, direction).
    ctx: {"closed": {(as_of, ticker, otype): position_id}, "assign_shares": {ticker: shares}}.
    Returns fillimport.Item objects (kind OPEN or REPORT)."""
    from helm.fillimport import Item, _fmt
    items = []
    days = {}
    for gk, g, fl in opens:
        days.setdefault((gk[1], gk[2]), []).append((gk, g, fl))
    for (as_of, tk), grp in sorted(days.items()):
        sp = [x for x in grp if x[0][4] == "PUT" and x[0][6] == "SHORT"]
        lp = [x for x in grp if x[0][4] == "PUT" and x[0][6] == "LONG"]
        sc = [x for x in grp if x[0][4] == "CALL" and x[0][6] == "SHORT"]
        lc = [x for x in grp if x[0][4] == "CALL" and x[0][6] == "LONG"]

        def label(x):
            gk, g, _ = x
            return "%s %s %s %s %g x%d at %s" % (as_of, tk, gk[3], gk[4].lower(), gk[5], g["contracts"], _fmt(g["price"]))

        def report(xs, why):
            for x in xs:
                items.append(Item("REPORT", "%s %s opened -- %s; not booked" % (label(x), x[0][6].lower(), why),
                                  ticker=tk))

        def parent(otype):
            return ctx.get("closed", {}).get((as_of, tk, otype))

        # puts: sold alone -> CSP; any bought put that day -> a spread, out
        if lp:
            report(sp + lp, "a bought put (a spread or a long put) is not in HELM's strategy set")
        else:
            for x in sp:
                gk, g, fl = x
                q, k = g["contracts"], float(gk[5])
                coll = k * 100 * q
                par = parent("PUT")
                flags, grade = _flags(conn, tk, "CSP", coll, _freed(conn, par))
                items.append(_open_item(Item, "CSP", tk, as_of, [_leg(gk, g)], q, fl, flags, grade, par,
                                        "%s CSP sold %s put %g %s x%d at %s; collateral %s; 50%% buy-back %s"
                                        % (as_of, tk, k, gk[3], q, _fmt(g["price"]), _money(coll),
                                           _fmt(_half_up(g["price"] / 2)))))

        # calls
        longs_in_band = [x for x in lc if PMCC_LONG_DTE[0] <= _dte(x[0][3], as_of) <= PMCC_LONG_DTE[1]]
        if lc:
            if len(lc) != 1 or len(longs_in_band) != 1 or len(sc) > 1:
                report(lc + sc, "a bought call outside a PMCC's shape (one long 270-730 days out, at most one "
                                "short) -- long calls and spreads are not in HELM's strategy set")
                continue
            L = longs_in_band[0]
            S = sc[0] if sc else None
            if S and (_d(S[0][3]) >= _d(L[0][3]) or S[1]["contracts"] != L[1]["contracts"]):
                report([L, S], "the sold call doesn't fit the bought one (it must expire first, with the "
                               "same number of contracts)")
                continue
            q = L[1]["contracts"]
            cost = L[1]["price"] * 100 * q
            flags, grade = _flags(conn, tk, "PMCC", cost)
            legs = [_leg(L[0], L[1])] + ([_leg(S[0], S[1])] if S else [])
            fl = L[2] + (S[2] if S else [])
            txt = "%s PMCC opened on %s: long %g %s x%d at %s (cost %s)" % (
                as_of, tk, L[0][5], L[0][3], q, _fmt(L[1]["price"]), _money(cost))
            if S:
                gap, floor = float(S[0][5]) - float(L[0][5]), L[1]["price"]
                ok = gap > floor
                txt += "; short %g %s x%d at %s; 50%% buy-back %s" % (
                    S[0][5], S[0][3], S[1]["contracts"], _fmt(S[1]["price"]), _fmt(_half_up(S[1]["price"] / 2)))
                if not ok:
                    flags.append("width test failed: strike gap %g is not more than the long's %s a share"
                                 % (gap, _fmt(floor)))
            else:
                txt += "; no short sold yet"
            items.append(_open_item(Item, "PMCC", tk, as_of, legs, q, fl, flags, grade, None, txt))
            continue

        for x in sc:
            gk, g, fl = x
            q = g["contracts"]
            free = _shares_free(conn, tk, ctx.get("assign_shares", {}).get(tk, 0))
            can_cc = free >= 100 * q
            pm = []
            for p, legs in _open_pmccs(conn, tk):
                lo = [l for l in legs if l["direction"] == "LONG" and l["status"] == "OPEN"]
                so = [l for l in legs if l["direction"] == "SHORT" and l["status"] == "OPEN"
                      and not ctx.get("closing_legs", set()).__contains__(l["id"])]
                if lo and not so and _d(gk[3]) < _d(lo[0]["expiration"]) and q <= lo[0]["contracts"]:
                    pm.append((p, legs))
            if can_cc and pm:
                report([x], "it could be a covered call on HELM's shares or the next short on the %s PMCC -- "
                            "book it by hand" % tk)
            elif pm:
                p, legs = pm[0]
                ok, floor, lk = width_test(legs, gk[5], q)
                flags, grade = _flags(conn, tk, "PMCC", 0)
                if ok is False:
                    flags.append("width test failed: strike gap %g over the long's %g is not more than %s a share "
                                 "(the long's cost net of rent banked)" % (float(gk[5]) - lk, lk, _fmt(floor)))
                it = _open_item(Item, "PMCC_SHORT", tk, as_of, [_leg(gk, g)], q, fl, flags, grade, None,
                                "%s %s PMCC: next short sold %g %s x%d at %s; 50%% buy-back %s"
                                % (as_of, tk, gk[5], gk[3], q, _fmt(g["price"]), _fmt(_half_up(g["price"] / 2))))
                it.position_id = p["id"]
                items.append(it)
            elif can_cc:
                flags, grade = _flags(conn, tk, "COVERED_CALL", 0)
                items.append(_open_item(Item, "COVERED_CALL", tk, as_of, [_leg(gk, g)], q, fl, flags, grade,
                                        parent("CALL"),
                                        "%s covered call sold on %s: %g %s x%d at %s (on %d HELM shares); "
                                        "50%% buy-back %s" % (as_of, tk, gk[5], gk[3], q, _fmt(g["price"]),
                                                              100 * q, _fmt(_half_up(g["price"] / 2)))))
            else:
                report([x], "HELM holds no uncovered %s shares and no %s PMCC is waiting for a short" % (tk, tk))
    return items


def _leg(gk, g):
    return {"direction": gk[6], "opt_type": gk[4], "strike": float(gk[5]), "expiration": gk[3],
            "price": round(float(g["price"]), 4), "contracts": int(g["contracts"])}


def _open_item(Item, strategy, tk, as_of, legs, q, fills, flags, grade, parent, text):
    if parent:
        text += "; a roll of %s" % parent
    if flags:
        text += "  ** " + "; ".join(flags)
    it = Item("OPEN", text, ticker=tk, as_of=as_of, fills=fills)
    it.open = {"strategy": strategy, "legs": legs, "contracts": q, "flags": flags, "grade": grade,
               "parent": parent}
    return it


# --------------------------------------------------------------------- writes
def _release_ib():
    """Drop this process's cached IBKR connection (helm.ibkr.get_ib, client 11).
    The vol-context step after each open (backfill_entry_vol) leaves it open;
    the next position's connection check asks for 11 too and the gateway refuses
    a number already in use -- found on the Mac 10-31: DELL booked, NVDA got
    "no reading". Releasing it before each position's readings fixes that."""
    try:
        from helm.ibkr import disconnect_ib
        disconnect_ib()
    except Exception:
        pass


def _quotes(tk, legs):
    """Entry readings at import, from IBKR only. Returns (spot, {i: quote}, note)."""
    from helm.cli.check_cmd import fetch_ibkr_option, fetch_ibkr_underlying
    _release_ib()
    qs, errs = {}, []
    spot = None
    try:
        u = fetch_ibkr_underlying(tk)
        spot = u.get("price")
        if u.get("error"):
            errs.append("stock: " + str(u["error"]))
    except Exception as e:
        errs.append("stock: " + str(e)[:60])
    for i, l in enumerate(legs):
        try:
            q = fetch_ibkr_option(tk, l["expiration"], l["strike"], l["opt_type"])
        except Exception as e:
            q = {"error": str(e)[:60]}
        if q.get("error"):
            errs.append("%g %s: %s" % (l["strike"], l["opt_type"].lower(), q["error"]))
        qs[i] = q
    return spot, qs, errs


def _stored(tk, leg, as_of):
    """Readings HELM already stored for this contract on the fill day: the put
    list's row for it (put_candidates), or None. Used only to fill readings
    IBKR could not give at import, and labelled."""
    from helm.db import get_conn
    c = get_conn()
    try:
        r = c.execute(
            "SELECT pc.*, pr.run_at FROM put_candidates pc JOIN put_runs pr ON pr.id = pc.run_id "
            "WHERE pc.ticker=? AND pc.expiry=? AND ABS(pc.strike-?)<1e-6 AND substr(pr.run_at,1,10)=? "
            "ORDER BY pr.run_at DESC LIMIT 1", (tk, leg["expiration"], leg["strike"], as_of)).fetchone() \
            if leg["opt_type"] == "PUT" else None
        return dict(r) if r else None
    except Exception:
        return None
    finally:
        c.close()


def _stored_spot(tk, as_of):
    """The last stock price HELM stored for tk on the fill day or after, or (None, None)."""
    from helm.db import get_conn
    c = get_conn()
    try:
        r = c.execute("SELECT c.spot_price, c.checked_at FROM checks c JOIN positions p ON p.id=c.position_id "
                      "WHERE p.ticker=? AND c.spot_price IS NOT NULL AND substr(c.checked_at,1,10) >= ? "
                      "ORDER BY c.checked_at DESC LIMIT 1", (tk, as_of)).fetchone()
        return (r[0], "HELM's last stored reading %s" % r[1][:16].replace("T", " ")) if r else (None, None)
    finally:
        c.close()


def _market_open(now=None):
    try:
        from helm.market_calendar import agent_should_run
        return bool(agent_should_run(now)[0])
    except Exception:
        return None


def readings_label(as_of, now=None):
    now = now or datetime.now()
    days = (now.date() - _d(as_of)).days
    mo = _market_open(now)
    return "entry readings from IBKR at import %s, %d day%s after the fill%s; no spread rule applied at import" % (
        now.strftime("%Y-%m-%d %H:%M"), days, "" if days == 1 else "s",
        "" if mo else (" (market closed: quotes are the last ones)" if mo is False else ""))


def book(it, now_label, fill_time):
    """Write one OPEN item. Returns (position_id, leg_ids, note)."""
    from helm.cli.entry_snapshot import open_position_with_snapshot, open_multileg_with_snapshot
    o = it.open
    tk, as_of, q = it.ticker, it.as_of, o["contracts"]
    when = "%sT%s" % (as_of, fill_time)
    spot, qs, errs = _quotes(tk, o["legs"])
    lab = "Fidelity import %s: opened from the fill (price and contracts from Fidelity; %s on %s, date only); %s" % (
        now_label, fill_time[:5], as_of, readings_label(as_of))
    if errs:
        lab += "; IBKR gave no reading for: " + "; ".join(errs)[:160]
    # gaps IBKR left: the put list's row for the same contract on the fill day
    for i, l in enumerate(o["legs"]):
        rd = qs.get(i) or {}
        if rd.get("delta") is None:
            st = _stored(tk, l, as_of)
            if st:
                for k in ("delta", "gamma", "theta", "vega", "iv", "bid", "ask", "mid"):
                    if rd.get(k) is None and st.get(k) is not None:
                        rd[k] = st[k]
                qs[i] = rd
                if spot is None and st.get("spot"):
                    spot = st["spot"]
                lab += "; readings for the %g %s from the put list run at %s on the fill day" % (
                    l["strike"], l["opt_type"].lower(), str(st["run_at"])[11:16])
            else:
                lab += "; no readings for the %g %s (readings missing)" % (l["strike"], l["opt_type"].lower())
    if spot is None:
        spot, src = _stored_spot(tk, as_of)
        if spot is None:
            raise RuntimeError("%s: no stock price for the entry snapshot (IBKR gave none and HELM stored none "
                               "since the fill) -- stopped here; run again when IB Gateway is up" % tk)
        lab += "; stock price from " + src
    if o["flags"]:
        lab += "; FLAGS: " + "; ".join(o["flags"])
    if o.get("grade"):
        lab += "; grade at entry %s" % o["grade"]

    def cdict(i, l):
        qd = qs.get(i) or {}
        bid, ask = qd.get("bid"), qd.get("ask")
        spr = round(ask - bid, 2) if bid is not None and ask is not None else None
        mid = qd.get("mid")
        return {"direction": l["direction"], "opt_type": l["opt_type"], "strike": l["strike"],
                # HELM stores a put's delta as a positive number (as helm open always has);
                # IBKR gives it negative.
                "expiration": l["expiration"],
                "delta": (abs(qd["delta"]) if qd.get("delta") is not None and l["opt_type"] == "PUT"
                          else qd.get("delta")),
                "theta": qd.get("theta"),
                "gamma": qd.get("gamma"), "vega": qd.get("vega"), "iv": qd.get("iv"),
                "dte": _dte(l["expiration"], as_of), "spot": spot, "bid": bid, "ask": ask, "spread": spr,
                "spread_pct": round(spr / mid * 100, 2) if spr is not None and mid else None,
                "fill_price": l["price"]}

    if o["strategy"] in ("CSP", "COVERED_CALL"):
        l = o["legs"][0]
        pid, lid, _ = open_position_with_snapshot(
            tk, o["strategy"], cdict(0, l), l["price"], q, book="REAL", origin_screen="FIDELITY_IMPORT",
            notes=lab, opened_at=when, open_date=as_of)
        lids = [lid]
    elif o["strategy"] == "PMCC":
        legs = [cdict(i, l) for i, l in enumerate(o["legs"])]
        legs.sort(key=lambda d: 0 if d["direction"] == "SHORT" else 1)   # snapshot anchors on the short
        L = [d for d in legs if d["direction"] == "LONG"][0]
        S = [d for d in legs if d["direction"] == "SHORT"]
        pf = {"entry_dte": (S[0]["dte"] if S else L["dte"]), "origin_screen": "FIDELITY_IMPORT"}
        if S:
            pf["spread_width"] = round(abs(S[0]["strike"] - L["strike"]), 2)
        pf["max_loss"] = round(L["fill_price"] * 100 * q - (S[0]["fill_price"] * 100 * q if S else 0), 2)
        pid, lids, _ = open_multileg_with_snapshot(
            ticker=tk, strategy="PMCC", legs=legs, contracts=q, spot=spot, book="REAL", position_fields=pf,
            pricing_source="fidelity-fill", opened_at=when, open_date=as_of, notes=lab)
    elif o["strategy"] == "PMCC_SHORT":
        from helm.models.position import Position
        from helm.models.leg import Leg
        from helm.cli import resell_cmd as R
        pos = Position.get(it.position_id)
        legs = Leg.for_position(pos.id)
        l = o["legs"][0]
        why = R.structural_refusal(pos, legs, l["strike"], l["expiration"], q, l["price"], today=_d(as_of))
        if why:
            raise RuntimeError("%s PMCC short: %s -- stopped" % (tk, why))
        qd = cdict(0, l)
        qd["mid"] = (qs.get(0) or {}).get("mid"); qd["mid_source"] = "ibkr at import"
        lid, _n = R.record(pos, legs, l["strike"], l["expiration"], q, l["price"], qd, lab, spot=spot,
                           open_date=as_of, when=when)
        pid, lids = pos.id, [lid]
    else:
        raise RuntimeError("unknown open kind %s" % o["strategy"])
    _release_ib()
    if o.get("parent"):
        from helm.db import transaction
        with transaction() as c:
            c.execute("UPDATE positions SET parent_position_id=? WHERE id=? AND parent_position_id IS NULL",
                      (o["parent"], pid))
    return pid, lids, lab
