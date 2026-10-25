"""helm/advice.py -- one plain recommendation per real position (Russ, 2026-10-24, s135).

Standing rule: HELM advises in plain words -- each real position gets one
recommendation (hold, take the profit, close or roll, decide on assignment,
heads-up), with its reason and the number behind it. Russ decides and places
every trade; HELM never acts. A recommendation without its reason is a defect.

The rules are rulebook v0.2 (claude/HELM-rulebook-v0.2.md, sections 4-6) for
CSPs, covered calls and PMCCs; the three run-off diagonals keep the rules they
were opened under (helm/exit_flags.py: loss limit, short breach / harvest, the
long's own rules, open flags). HELM leans only where a rule gives a lean;
otherwise it says "decide" and shows both choices.

Rule flags (Russ, s135): whenever a position or the book oversteps one of
Russ's rules, HELM flags it in Needs you with the rule, the number and the
choices, and never resolves it: the sleeve over its share, a name over 20%, a
sector over 30% (helm/sleeve.py's own lists), a held name whose latest grade
is below C, and earnings inside a covered call. Earnings on a put or diagonal
are a mark on the holding line, not an item (Russ, s135: no rule asks for
anything then).

READ-ONLY, from the readings the snapshot stored (leg_checks, checks), like
helm/bookview.py. Nothing is fetched live. A position whose latest reading is
old or not GOOD says so instead of advising from it.

Not yet possible: the covered-call ex-dividend flag (HELM stores no ex-dividend
dates). Said in the output, never guessed.

    helm advise                       python3 -m helm.advice --selftest
"""
import os
import sqlite3
from datetime import date, datetime

from helm import sleeve as S

PROFIT_TAKE = 0.50          # rulebook 4/5/6: 50% resting order
TIME_EXIT_DTE = 21          # rulebook 4/5/6: 21 DTE
TESTED_DELTA = 0.50         # rulebook 4: delta >= 0.50 inside 21 days
PMCC_LONG_REVIEW_DTE = 90   # rulebook 6
LOSS_LIMIT_FRAC = 0.50      # rulebook 6 / exit_flags pos_stop: half the long's cost
EARNINGS_SOON_DAYS = 7      # Russ, s135: inside 7 days the mark is highlighted ("in N days")
SHORT_EXPIRY_SOON_DAYS = 7  # a run-off diagonal's short expiring this week
STALE_DAYS = 4              # a reading older than this (calendar days) is not advised from
PASSING = ("A", "B", "C", "FUND")

# verb -> (needs_you, priority; lower = more urgent)
VERBS = {
    "STALE":       (True, 0, "No fresh reading"),
    "LOSS_LIMIT":  (True, 1, "Loss limit reached"),
    "TESTED":      (True, 2, "Tested -- roll or take the shares?"),
    "DECIDE":      (True, 2, "Decide"),
    "OVER_RULE":   (True, 2, "Over one of your rules -- you decide"),
    "TAKE_PROFIT": (True, 3, "Take the profit"),
    "TIME_21":     (True, 4, "Close or roll -- 21 days left"),
    "LONG_RULE":   (True, 4, "The long's own rule fired"),
    "REVIEW_LONG": (True, 5, "Review the long -- 90 days left"),
    "SELL_CALL":   (True, 5, "Sell the next call"),
    "OPEN_FLAG":   (True, 6, "An earlier flag is still open"),
    "HEADS_UP":    (True, 7, "Heads-up"),
    "HOLD":        (False, 9, "Hold"),
}


def _m(x, sign=False):
    if x is None:
        return "--"
    s = "-" if x < 0 else ("+" if sign and x > 0 else "")
    v = abs(float(x))
    return "%s$%s" % (s, format(v, ",.0f"))


def _p(x):
    return "$%.2f" % float(x)


def _half_up(x):
    from decimal import Decimal, ROUND_HALF_UP
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _dte(exp, today):
    if not exp:
        return None
    return (date.fromisoformat(str(exp)[:10]) - today).days


def _grade(conn, ticker):
    if S._has_table(conn, "approved_funds") and conn.execute(
            "SELECT 1 FROM approved_funds WHERE ticker=?", (ticker,)).fetchone():
        return "FUND"
    if not S._has_table(conn, "ownership_quality"):
        return None
    r = conn.execute("SELECT grade FROM ownership_quality WHERE ticker=? "
                     "ORDER BY date DESC, updated_at DESC LIMIT 1", (ticker,)).fetchone()
    return r[0] if r else None


def _earnings(conn, ticker, today):
    r = conn.execute("SELECT next_earnings FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
    if not r or not r[0]:
        return None, None
    try:
        d = date.fromisoformat(str(r[0])[:10])
    except ValueError:
        return None, None
    return d, (d - today).days


def _leg_mark(conn, leg, n_legs, pos_id):
    """(mark, checked_at, quality) from the leg's latest reading; a single-leg
    position with no leg row falls back to its check (as bookview does)."""
    r = conn.execute("SELECT checked_at, current_price, delta, data_quality FROM leg_checks "
                     "WHERE leg_id=? AND current_price IS NOT NULL ORDER BY checked_at DESC LIMIT 1",
                     (leg["id"],)).fetchone()
    if r is None and n_legs == 1:
        r = conn.execute("SELECT checked_at, current_price, delta, data_quality FROM checks "
                         "WHERE position_id=? AND current_price IS NOT NULL ORDER BY checked_at DESC LIMIT 1",
                         (pos_id,)).fetchone()
    return dict(r) if r else None


def _spot(conn, pos_id):
    r = conn.execute("SELECT checked_at, spot_price, data_quality FROM checks WHERE position_id=? "
                     "AND spot_price IS NOT NULL ORDER BY checked_at DESC LIMIT 1", (pos_id,)).fetchone()
    return dict(r) if r else None


def _rec(p, verb, headline, reason, numbers=None, act=None, lean=None, marks=None, as_of=None):
    needs, prio, label = VERBS[verb]
    return {"position_id": p["id"], "ticker": p["ticker"], "strategy": p["strategy"],
            "verb": verb, "label": label, "needs_you": needs, "priority": prio,
            "headline": headline, "reason": reason, "numbers": numbers or {},
            "act": act, "lean": lean, "marks": marks or [], "as_of": as_of}


def _stale(rd, today):
    if rd is None:
        return "no stored reading"
    age = (today - date.fromisoformat(rd["checked_at"][:10])).days
    if age > STALE_DAYS:
        return "latest reading is from %s (%d days old)" % (rd["checked_at"][:10], age)
    if (rd.get("data_quality") or "GOOD") != "GOOD":
        return "latest reading (%s) is marked %s" % (rd["checked_at"][:16].replace("T", " "), rd["data_quality"])
    return None


# ---------------------------------------------------------------- single short

def _single_short(conn, p, legs, today):
    """CSP or covered call: one open short option (a covered call also has stock)."""
    is_cc = p["strategy"] == "COVERED_CALL"
    shorts = [l for l in legs if l["direction"] == "SHORT" and l["option_type"] in ("PUT", "CALL")]
    if len(shorts) != 1:
        return _rec(p, "STALE", "%s: can't read this position" % p["ticker"],
                    "expected one open short option, found %d" % len(shorts))
    l = shorts[0]
    rd = _leg_mark(conn, l, len([x for x in legs if x["option_type"] in ("PUT", "CALL")]), p["id"])
    sp = _spot(conn, p["id"])
    why = _stale(rd, today) or (_stale(sp, today) if sp else "no stored stock price")
    k, n, credit = l["strike"], l["contracts"] or 0, l["open_price"] or 0.0
    dte = _dte(l["expiration"], today)
    exp_s = str(l["expiration"])[:10]
    what = "%s %s %g %s x%d" % (p["ticker"], "call" if is_cc else "put", k, exp_s[5:], n)
    if why:
        return _rec(p, "STALE", "%s -- no advice from an old reading" % what, why)
    mark, spot, delta = rd["current_price"], sp["spot_price"], rd.get("delta")
    kept = (credit - mark) / credit if credit else None
    pnl = (credit - mark) * 100 * n
    buy_half = _half_up(credit * (1 - PROFIT_TAKE))
    as_of = rd["checked_at"][:16].replace("T", " ")
    nums = {"credit": credit, "mark": mark, "kept_pct": None if kept is None else round(kept * 100, 1),
            "pnl": round(pnl, 2), "dte": dte, "spot": spot, "strike": k, "delta": delta,
            "resting_buy_back": buy_half}
    marks = []
    e_day, e_in = _earnings(conn, p["ticker"], today)
    e_inside = e_day is not None and e_in is not None and 0 <= e_in and dte is not None and e_in <= dte
    if e_inside:
        marks.append("earnings %s%s" % (e_day.isoformat()[5:],
                                       (" (in %d days)" % e_in) if e_in <= EARNINGS_SOON_DAYS else ""))
    if kept is not None and kept >= 0:
        status = "%.0f%% of the profit taken (%s), %d days left" % (kept * 100, _m(pnl, sign=True), dte)
    else:
        status = "%s on a %s credit, %d days left" % (_m(pnl, sign=True), _m(credit * 100 * n), dte)

    if is_cc:
        tested = spot >= k or (delta is not None and abs(delta) >= TESTED_DELTA and dte <= TIME_EXIT_DTE)
    else:
        tested = spot <= k or (delta is not None and abs(delta) >= TESTED_DELTA and dte <= TIME_EXIT_DTE)
    if tested:
        through = abs(spot - k)
        if is_cc:
            return _rec(p, "TESTED", "%s -- tested: roll up and out, or let the shares go?" % what,
                        "%s is at %s, %s %s the %g strike%s." % (
                            p["ticker"], _p(spot), _p(through), "above" if spot >= k else "below", k,
                            "" if delta is None else " (delta %.2f)" % abs(delta)),
                        nums, act="buy back now at about %s; or let the shares go at %g" % (_p(mark), k),
                        lean=None, marks=marks, as_of=as_of)
        g = _grade(conn, p["ticker"])
        basis = k - credit
        lean = ("HELM leans: take the shares -- %s grades %s, so assignment is the default "
                "(%d shares at %g, effective basis %s)." % (p["ticker"], g, 100 * n, k, _p(basis))
                if g in PASSING else
                "No default: %s grades %s, so taking the shares is your call -- rolling out and down "
                "for a net credit is the alternative." % (p["ticker"], g or "ungraded"))
        return _rec(p, "TESTED", "%s -- tested: roll out and down, or take the shares?" % what,
                    "%s is at %s, %s %s the %g strike%s." % (
                        p["ticker"], _p(spot), _p(through), "below" if spot <= k else "above", k,
                        "" if delta is None else " (delta %.2f)" % abs(delta)),
                    nums, act="buy back now at about %s (%s); assignment would cost %s" % (
                        _p(mark), _m(pnl, sign=True), _m(k * 100 * n)),
                    lean=lean, marks=marks, as_of=as_of)
    if kept is not None and kept >= PROFIT_TAKE:
        return _rec(p, "TAKE_PROFIT", "%s -- take the profit" % what,
                    "%.0f%% of the credit is taken (%s); the rule is 50%%." % (kept * 100, _m(pnl, sign=True)),
                    nums, act="your resting order at %s should fill; if it isn't placed, buy back at %s"
                    % (_p(buy_half), _p(buy_half)), marks=marks, as_of=as_of)
    if dte is not None and dte <= TIME_EXIT_DTE:
        return _rec(p, "TIME_21", "%s -- close or roll: %d days left" % (what, dte),
                    "The rule closes at 21 days; %s." % status, nums,
                    act="buy back at about %s (%s)" % (_p(mark), _m(pnl, sign=True)), marks=marks, as_of=as_of)
    return _rec(p, "HOLD", "%s -- hold" % what, status + ("; " + ", ".join(marks) if marks else "") + ".",
                nums, act="resting buy-back at %s" % _p(buy_half), marks=marks, as_of=as_of)


# ---------------------------------------------------------------- long + short

def _diag_family(conn, p, legs_all, today):
    """PMCC (rulebook 6) and the run-off DIAGONALs (their own exit_flags rules)."""
    from helm import exit_flags as EF
    is_pmcc = p["strategy"] == "PMCC"
    opts = [l for l in legs_all if l["option_type"] in ("PUT", "CALL")]
    open_ = [l for l in opts if l["status"] == "OPEN"]
    longs = [l for l in open_ if l["direction"] == "LONG"]
    shorts = [l for l in open_ if l["direction"] == "SHORT"]
    if len(longs) != 1:
        return _rec(p, "STALE", "%s: can't read this position" % p["ticker"],
                    "expected one open long, found %d" % len(longs))
    L = longs[0]
    sp = _spot(conn, p["id"])
    reads = {l["id"]: _leg_mark(conn, l, len(open_), p["id"]) for l in open_}
    why = next((w for w in [_stale(reads[l["id"]], today) for l in open_] if w), None) or \
        (_stale(sp, today) if sp else "no stored stock price")
    long_cost = (L["open_price"] or 0) * (L["multiplier"] or 100) * (L["contracts"] or 0)
    what = "%s %s (long %g %s" % (p["ticker"], "PMCC" if is_pmcc else "diagonal", L["strike"],
                                  str(L["expiration"])[:10][5:])
    what += (", short %g %s)" % (shorts[0]["strike"], str(shorts[0]["expiration"])[:10][5:])) if shorts else ", no short)"
    if why:
        return _rec(p, "STALE", "%s -- no advice from an old reading" % what, why)
    spot = sp["spot_price"]
    # whole-trade P&L: closed legs at their close, open legs at their mark
    pnl, rent = 0.0, 0.0
    for l in opts:
        mult = (l["multiplier"] or 100) * (l["contracts"] or 0)
        px = l["close_price"] if l["status"] != "OPEN" else reads[l["id"]]["current_price"]
        if px is None:
            continue
        pnl += ((l["open_price"] - px) if l["direction"] == "SHORT" else (px - l["open_price"])) * mult
        if l["direction"] == "SHORT":
            rent += (l["open_price"] or 0) * mult - ((l["close_price"] or 0) * mult if l["status"] != "OPEN" else 0)
    l_dte = _dte(L["expiration"], today)
    as_of = min(reads[l["id"]]["checked_at"] for l in open_)[:16].replace("T", " ")
    nums = {"pnl": round(pnl, 2), "long_cost": round(long_cost, 2), "rent_banked": round(rent, 2),
            "loss_limit": round(-LOSS_LIMIT_FRAC * long_cost, 2), "long_dte": l_dte, "spot": spot}
    marks = []
    e_day, e_in = _earnings(conn, p["ticker"], today)
    status = "whole trade %s; the long cost %s and has %d days left" % (_m(pnl, sign=True), _m(long_cost), l_dte)

    if pnl <= -LOSS_LIMIT_FRAC * long_cost:
        return _rec(p, "LOSS_LIMIT", "%s -- loss limit reached" % what,
                    "The whole trade is %s, past the limit of %s (half the long's cost)." % (
                        _m(pnl, sign=True), _m(-LOSS_LIMIT_FRAC * long_cost)),
                    nums, act="close the whole trade, or log why you're holding it", as_of=as_of)

    firing = set() if is_pmcc else EF.diag_firing_now(conn, p["id"])
    S0 = shorts[0] if shorts else None
    if S0 is not None:
        s_mark = reads[S0["id"]]["current_price"]
        s_dte = _dte(S0["expiration"], today)
        itm = (spot > S0["strike"]) if S0["option_type"] == "CALL" else (spot < S0["strike"])
        n = S0["contracts"] or 0
        nums.update({"short_strike": S0["strike"], "short_dte": s_dte, "short_mark": s_mark,
                     "short_credit": S0["open_price"]})
        s_what = "short %g %s x%d" % (S0["strike"], "call" if S0["option_type"] == "CALL" else "put", n)
        if s_dte is not None and s_dte <= SHORT_EXPIRY_SOON_DAYS and itm:
            return _rec(p, "DECIDE", "%s -- decide before %s: the %s is in the money" % (
                            what, str(S0["expiration"])[:10][5:], s_what),
                        "%s is at %s, %s through the %g strike, with %d days left. No rule picks for you." % (
                            p["ticker"], _p(spot), _p(abs(spot - S0["strike"])), S0["strike"], s_dte),
                        nums, act="buy it back at about %s (%s for %d), or roll it out; if it's left, "
                        "expect assignment -- %d shares called away at %g against your long" % (
                            _p(s_mark), _m(s_mark * 100 * n), n, 100 * n, S0["strike"]),
                        as_of=as_of)
        if "breach" in firing:
            return _rec(p, "DECIDE", "%s -- the short is breached" % what,
                        "%s at %s is through the %g short for two days running. %s." % (
                            p["ticker"], _p(spot), S0["strike"], status.capitalize()),
                        nums, act="roll the short, buy it back at about %s, or close the whole trade"
                        % _p(s_mark), as_of=as_of)
        s_kept = (S0["open_price"] - s_mark) / S0["open_price"] if S0["open_price"] else None
        if s_kept is not None and s_kept >= PROFIT_TAKE:
            bb = _half_up(S0["open_price"] * (1 - PROFIT_TAKE))
            return _rec(p, "TAKE_PROFIT", "%s -- take the profit on the short" % what,
                        "The short has given up %.0f%% of its %s credit." % (s_kept * 100, _p(S0["open_price"])),
                        nums, act="buy the short back at %s%s" % (
                            _p(bb) if is_pmcc else _p(s_mark),
                            " (your resting order)" if is_pmcc else ""), as_of=as_of)
        if is_pmcc and s_dte is not None and s_dte <= TIME_EXIT_DTE:
            return _rec(p, "TIME_21", "%s -- close or roll the short: %d days left" % (what, s_dte),
                        "The rule closes the short at 21 days.", nums,
                        act="buy it back at about %s, or roll up and out for a credit" % _p(s_mark), as_of=as_of)
    long_kinds = [k for k in ("long_stop", "long_giveback", "long_dte7", "long_dte21") if k in firing]
    if long_kinds:
        return _rec(p, "LONG_RULE", "%s -- %s" % (what, EF.KIND_LABEL[long_kinds[0]]),
                    "The long's own rule fired (%s). %s." % (EF.KIND_LABEL[long_kinds[0]], status.capitalize()),
                    nums, act="sell the long (or the whole trade), or log why you're holding", as_of=as_of)
    if is_pmcc and l_dte is not None and l_dte <= PMCC_LONG_REVIEW_DTE:
        return _rec(p, "REVIEW_LONG", "%s -- review the long: %d days left" % (what, l_dte),
                    "The rule reviews the long at 90 days. %s." % status.capitalize(), nums, as_of=as_of)
    if S0 is None:
        width = (long_cost - rent) / ((L["multiplier"] or 100) * (L["contracts"] or 1))
        nums["width_floor"] = round(width, 2)
        return _rec(p, "SELL_CALL" if is_pmcc else "DECIDE",
                    "%s -- %s" % (what, "sell the next call" if is_pmcc else "the long has no short on it"),
                    "No short is open. %s." % status.capitalize(), nums,
                    act="pick a 30-45 day, 0.20-0.30 delta call whose strike sits more than %s above the "
                    "long's %g strike (the width test)" % (_p(width), L["strike"]), as_of=as_of)
    pend = [d for d in EF.pending(conn, ensure=False) if d["position_id"] == p["id"]] if not is_pmcc else []
    if pend:
        d = pend[0]
        return _rec(p, "OPEN_FLAG", "%s -- a flag from %s is still open" % (what, d["flag_date"][5:]),
                    "'%s' fired on %s and has no keep or close logged%s. %s." % (
                        d["label"], d["flag_date"], "" if d["still_firing"] else " (not firing now)",
                        status.capitalize()), nums, act="log a keep reason, or close", as_of=as_of)
    if e_day is not None and e_in is not None and e_in >= 0 and S0 is not None and e_in <= (_dte(S0["expiration"], today) or 0):
        marks.append("earnings %s%s" % (e_day.isoformat()[5:],
                                       (" (in %d days)" % e_in) if e_in <= EARNINGS_SOON_DAYS else ""))
    return _rec(p, "HOLD", "%s -- hold" % what, status + ("; " + ", ".join(marks) if marks else "") + ".",
                nums, marks=marks, as_of=as_of)


# ---------------------------------------------------------------- rule flags

def _flag(key, headline, reason, act, numbers=None):
    needs, prio, label = VERBS["OVER_RULE"]
    return {"position_id": None, "ticker": key, "strategy": None, "verb": "OVER_RULE", "label": label,
            "needs_you": needs, "priority": prio, "headline": headline, "reason": reason,
            "numbers": numbers or {}, "act": act, "lean": None, "marks": [], "as_of": None}


_PLAIN = {"CSP": "put", "COVERED_CALL": "covered call", "DIAGONAL": "diagonal", "PMCC": "PMCC",
          "DIAGONAL_PUT": "diagonal put"}


def _what_frees(w):
    w = w.lower()
    if w.startswith("csp"):
        return "closing the put"
    if "share" in w:
        return "selling the shares"
    for k in ("pmcc", "diagonal put", "diagonal"):
        if w.startswith(k):
            return "closing the %s" % k.replace("pmcc", "PMCC")
    return "closing the " + w.replace(" at cost", "")


def _frees(items):
    return "; ".join("%s frees %s" % (_what_frees(it["what"]), _m(it["dollars"]))
                     for it in sorted(items, key=lambda i: i["dollars"]))


def rule_flags(conn, b, today):
    out = []
    if b["used"] > b["share"] + 1e-6:
        out.append(_flag("SLEEVE", "The sleeve is over its share",
                         "%s in use of %s, %s over. Nothing is forced; every new put or PMCC is declined."
                         % (_m(b["used"]), _m(b["share"]), _m(b["used"] - b["share"])),
                         "hold until expiries free room, or close something to make room",
                         {"used": b["used"], "share": b["share"]}))
    for t in b["over_name"]:
        v = b["by_name"][t]
        its = [it for it in b["items"] if it["ticker"] == t]
        out.append(_flag(t, "%s is over the name limit" % t,
                         "%s uses %s, %s over the %s limit (20%% of the sleeve). Nothing is forced; new %s "
                         "trades are declined." % (t, _m(v), _m(v - b["name_cap"]), _m(b["name_cap"]), t),
                         "hold, or reduce: " + _frees(its), {"in_use": v, "limit": b["name_cap"]}))
    for sec in b["over_sector"]:
        v = b["by_sector"][sec]
        its = [it for it in b["items"] if it["sector"] == sec]
        out.append(_flag(sec, "%s is over the sector limit" % sec,
                         "%s uses %s, %s over the %s limit (30%% of the sleeve), across %s. Nothing is forced; new "
                         "trades in it are declined." % (sec, _m(v), _m(v - b["sector_cap"]), _m(b["sector_cap"]),
                                                         ", ".join(sorted({it["ticker"] for it in its}))),
                         "hold, or reduce: " + _frees(its), {"in_use": v, "limit": b["sector_cap"]}))
    held = {}
    for p in conn.execute("SELECT ticker, strategy FROM positions WHERE book='REAL' AND status='OPEN'"):
        held.setdefault(p["ticker"], []).append(p["strategy"])
    for t in sorted(held):
        g = _grade(conn, t)
        if g not in PASSING:
            out.append(_flag(t, "%s grades %s -- below C" % (t, g or "ungraded"),
                             "Your rules offer only A-C names (or approved funds). You hold %s (%s); its latest "
                             "grade is %s, so taking shares there is not the default." % (
                                 t, ", ".join(sorted({_PLAIN.get(x, x.lower()) for x in held[t]})), g or "none"),
                             "keep it and let it run, or close it"))
    for p in conn.execute("SELECT * FROM positions WHERE book='REAL' AND status='OPEN' AND strategy='COVERED_CALL'"):
        e_day, e_in = _earnings(conn, p["ticker"], today)
        for l in conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN' AND direction='SHORT' "
                              "AND option_type='CALL'", (p["id"],)):
            dte = _dte(l["expiration"], today)
            if e_in is not None and dte is not None and 0 <= e_in <= dte:
                out.append(_flag(p["ticker"], "%s covered call has earnings inside the trade" % p["ticker"],
                                 "Earnings %s fall before the %s expiry; your rule is no earnings inside a "
                                 "covered call." % (e_day.isoformat(), str(l["expiration"])[:10]),
                                 "keep it through the report, or buy the call back before it"))
    return out


# ---------------------------------------------------------------- the book

def advise(db=None, today=None, account_id=None):
    today = today or date.today()
    conn = S._conn(db)
    try:
        q = "SELECT * FROM positions WHERE book='REAL' AND status='OPEN'" + (" AND account_id=?" if account_id else "")
        out = []
        for p in conn.execute(q, ((account_id,) if account_id else ())).fetchall():
            legs_all = [dict(r) for r in conn.execute("SELECT * FROM legs WHERE position_id=?", (p["id"],))]
            legs = [l for l in legs_all if l["status"] == "OPEN"]
            if p["strategy"] in ("CSP", "COVERED_CALL"):
                out.append(_single_short(conn, p, legs, today))
            elif p["strategy"] in S.DIAG_FAMILY:
                out.append(_diag_family(conn, p, legs_all, today))
            else:
                out.append(_rec(p, "DECIDE", "%s %s -- a retired structure" % (p["ticker"], p["strategy"]),
                                "HELM no longer advises on %s; it finishes under the rules it was opened under."
                                % p["strategy"]))
        out.extend(rule_flags(conn, S.book(account_id, db), today))
        out.sort(key=lambda r: (r["priority"], r["ticker"]))
        return out
    finally:
        conn.close()


def status(db=None, account_id=None, root=None):
    """The page's one status line: sleeve, freshness, and the latest audit verdict."""
    b = S.book(account_id, db)
    conn = S._conn(db)
    try:
        r = conn.execute("SELECT MAX(k.checked_at) FROM checks k JOIN positions p ON p.id=k.position_id "
                         "WHERE p.book='REAL' AND p.status='OPEN'").fetchone()
        marks_at = r[0][:16].replace("T", " ") if r and r[0] else None
    finally:
        conn.close()
    verdict = None
    try:
        import glob
        root = root or os.environ.get("HELM_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files = sorted(glob.glob(os.path.join(root, "logs", "audit_eod_*.txt")))
        if files:
            for line in open(files[-1], errors="replace"):
                if line.startswith("VERDICT:"):
                    verdict = {"file": os.path.basename(files[-1]), "text": line.strip()[len("VERDICT:"):].strip()}
                    break
    except Exception:
        verdict = None
    return {"sleeve": b["share"], "in_use": b["used"], "free": b["share"] - b["used"],
            "marks_at": marks_at, "audit": verdict, "gross": True}


def _report():
    from rich.console import Console
    con = Console()
    st = status()
    free = st["free"]
    con.print("[bold]HELM -- today[/bold]   sleeve %s, in use %s, %s   marks %s"
              % (_m(st["sleeve"]), _m(st["in_use"]),
                 ("room %s" % _m(free)) if free > 0 else "[yellow]full[/yellow] (%s over)" % _m(-free),
                 st["marks_at"] or "--"))
    if st["audit"]:
        con.print("[dim]last audit (%s): %s[/dim]" % (st["audit"]["file"], st["audit"]["text"]))
    recs = advise()
    flags = [r for r in recs if r["verb"] == "OVER_RULE"]
    need = [r for r in recs if r["needs_you"] and r["verb"] != "OVER_RULE"]
    if flags:
        con.print("\n[bold]Over your rules[/bold] -- you decide")
        for r in flags:
            con.print("  [bold]%s[/bold]\n    %s\n    -> %s" % (r["headline"], r["reason"], r["act"]))
    con.print("\n[bold]Needs you[/bold]" + ("" if need else ": nothing today."))
    for r in need:
        con.print("  [bold]%s[/bold]\n    %s" % (r["headline"], r["reason"]))
        if r["act"]:
            con.print("    -> %s" % r["act"])
        if r["lean"]:
            con.print("    %s" % r["lean"])
    hold = [r for r in recs if not r["needs_you"]]
    if hold:
        con.print("\n[bold]Holding[/bold]")
        for r in hold:
            con.print("  %s: %s" % (r["headline"].replace(" -- hold", ""), r["reason"]))
    con.print("\n[dim]From stored readings (as of each line's reading); advice only -- you decide and place every trade. "
              "Not yet available: ex-dividend dates (none stored).[/dim]")


def run():
    import sys
    if "--selftest" in sys.argv:
        return _selftest()
    _report()


def _selftest():
    import tempfile
    fails = []

    def ok(c, m):
        if not c:
            fails.append(m)
            print("FAIL", m)

    d = tempfile.mkdtemp()
    db = os.path.join(d, "t.db")
    c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE positions (id TEXT, account_id TEXT, strategy TEXT, ticker TEXT, status TEXT, book TEXT, max_loss REAL);
      CREATE TABLE legs (id TEXT, position_id TEXT, leg_role TEXT, option_type TEXT, direction TEXT, strike REAL,
                         expiration TEXT, contracts INT, multiplier INT, open_price REAL, close_price REAL,
                         close_date TEXT, status TEXT, open_date TEXT, created_at TEXT);
      CREATE TABLE leg_checks (leg_id TEXT, checked_at TEXT, current_price REAL, delta REAL, data_quality TEXT);
      CREATE TABLE checks (position_id TEXT, checked_at TEXT, spot_price REAL, current_price REAL, delta REAL, data_quality TEXT);
      CREATE TABLE watchlist (ticker TEXT, sector TEXT, beta REAL, next_earnings TEXT);
      CREATE TABLE ownership_quality (ticker TEXT, date TEXT, grade TEXT, updated_at TEXT);
      CREATE TABLE stock_positions (account_id TEXT, ticker TEXT, shares INT, cost_basis REAL);
      INSERT INTO watchlist VALUES ('HLD','Tech',1,'2026-12-01'),('TP','Tech',1,NULL),('T21','Tech',1,NULL),
        ('TB','Tech',1,NULL),('TD','Tech',1,NULL),('ER','Tech',1,'2026-10-28'),('OLD','Tech',1,NULL),
        ('DG','Tech',1,NULL),('LL','Tech',1,NULL),('PM','Tech',1,NULL),('PMX','Tech',1,NULL),
        ('BIG','Bank',1,NULL),('CC','Food',1,'2026-11-05');
      INSERT INTO ownership_quality VALUES ('TB','2026-10-24','B',''),('TD','2026-10-24','D',''),('TD','2026-07-01','A',''),
        ('BIG','2026-10-24','A',''),('CC','2026-10-24','B','');
    """)
    T = "2026-10-23T15:15"
    def csp(t, k, exp, credit, mark, spot, delta, when=T, q="GOOD"):
        c.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?)", (t, 'a', 'CSP', t, 'OPEN', 'REAL', None))
        c.execute("INSERT INTO legs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (t + 'L', t, 'SHORT_PUT', 'PUT', 'SHORT', k, exp, 2, 100, credit, None, None, 'OPEN', '', ''))
        c.execute("INSERT INTO leg_checks VALUES (?,?,?,?,?)", (t + 'L', when, mark, delta, q))
        c.execute("INSERT INTO checks VALUES (?,?,?,?,?,?)", (t, when, spot, mark, delta, q))
    csp('HLD', 100, '2026-11-20', 2.00, 1.40, 110, -0.20)          # 30% kept, 28 days -> hold, earnings after expiry
    csp('TP', 100, '2026-11-20', 2.00, 0.90, 112, -0.10)           # 55% -> take profit
    csp('T21', 100, '2026-11-13', 2.00, 1.50, 108, -0.25)          # 20 days -> close or roll
    csp('TB', 100, '2026-11-20', 2.00, 3.50, 98, -0.62)            # spot under strike, grade B -> lean assign
    csp('TD', 100, '2026-11-20', 2.00, 3.50, 98, -0.62)            # grade D (latest) -> no default
    csp('ER', 100, '2026-11-20', 2.00, 1.60, 109, -0.22)           # earnings in 4 days inside trade -> heads-up
    csp('OLD', 100, '2026-11-20', 2.00, 1.60, 109, -0.22, when='2026-10-12T15:15')  # stale
    csp('BIG', 400, '2026-11-20', 8.00, 6.00, 440, -0.20)          # 2 x 400 = $80,000 > $60,000 name limit
    # covered call with earnings inside the trade -> a rule flag
    c.execute("INSERT INTO positions VALUES ('CC','a','COVERED_CALL','CC','OPEN','REAL',NULL)")
    c.executemany("INSERT INTO legs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ('CCs', 'CC', 'SHORT_CALL', 'CALL', 'SHORT', 60, '2026-11-20', 1, 100, 1.00, None, None, 'OPEN', '', ''),
        ('CCk', 'CC', 'LONG_STOCK', 'STOCK', 'LONG', None, None, 100, 1, 55.0, None, None, 'OPEN', '', '')])
    c.execute("INSERT INTO leg_checks VALUES ('CCs',?,?,?,?)", (T, 0.80, 0.25, 'GOOD'))
    c.execute("INSERT INTO checks VALUES ('CC',?,?,?,?,?)", (T, 57.0, 0.80, 0.25, 'GOOD'))
    # run-off diagonal: short 235C ITM expiring in 6 days -> DECIDE, no lean
    c.execute("INSERT INTO positions VALUES ('DG','a','DIAGONAL','DG','OPEN','REAL',NULL)")
    c.executemany("INSERT INTO legs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ('DGl', 'DG', 'LONG_CALL', 'CALL', 'LONG', 220, '2026-12-18', 2, 100, 29.80, None, None, 'OPEN', '', ''),
        ('DGs', 'DG', 'SHORT_CALL', 'CALL', 'SHORT', 235, '2026-10-30', 2, 100, 8.75, None, None, 'OPEN', '', '')])
    c.executemany("INSERT INTO leg_checks VALUES (?,?,?,?,?)", [('DGl', T, 35.91, 0.8, 'GOOD'), ('DGs', T, 18.90, 0.9, 'GOOD')])
    c.execute("INSERT INTO checks VALUES ('DG',?,?,?,?,?)", (T, 253.0, None, None, 'GOOD'))
    # loss-limit diagonal: long cost 3050, now 1000; short OTM; pnl ~ -2050+ < -1525
    c.execute("INSERT INTO positions VALUES ('LL','a','DIAGONAL','LL','OPEN','REAL',NULL)")
    c.executemany("INSERT INTO legs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ('LLl', 'LL', 'LONG_CALL', 'CALL', 'LONG', 52.5, '2026-12-18', 5, 100, 6.10, None, None, 'OPEN', '', ''),
        ('LLs', 'LL', 'SHORT_CALL', 'CALL', 'SHORT', 60, '2026-11-20', 5, 100, 0.50, None, None, 'OPEN', '', '')])
    c.executemany("INSERT INTO leg_checks VALUES (?,?,?,?,?)", [('LLl', T, 2.00, 0.4, 'GOOD'), ('LLs', T, 0.20, 0.1, 'GOOD')])
    c.execute("INSERT INTO checks VALUES ('LL',?,?,?,?,?)", (T, 48.0, None, None, 'GOOD'))
    # PMCC with no short -> sell the next call, width floor = (long cost - rent)/100/contracts
    c.execute("INSERT INTO positions VALUES ('PM','a','PMCC','PM','OPEN','REAL',NULL)")
    c.executemany("INSERT INTO legs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ('PMl', 'PM', 'LONG_CALL', 'CALL', 'LONG', 80, '2027-12-17', 1, 100, 30.00, None, None, 'OPEN', '', ''),
        ('PMo', 'PM', 'SHORT_CALL', 'CALL', 'SHORT', 115, '2026-10-16', 1, 100, 2.00, 0.50, '2026-10-10', 'CLOSED', '', '')])
    c.execute("INSERT INTO leg_checks VALUES ('PMl',?,?,?,?)", (T, 31.0, 0.8, 'GOOD'))
    c.execute("INSERT INTO checks VALUES ('PM',?,?,?,?,?)", (T, 105.0, None, None, 'GOOD'))
    # PMCC long within 90 days, short fine -> review the long
    c.execute("INSERT INTO positions VALUES ('PMX','a','PMCC','PMX','OPEN','REAL',NULL)")
    c.executemany("INSERT INTO legs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ('PXl', 'PMX', 'LONG_CALL', 'CALL', 'LONG', 80, '2027-01-15', 1, 100, 30.00, None, None, 'OPEN', '', ''),
        ('PXs', 'PMX', 'SHORT_CALL', 'CALL', 'SHORT', 115, '2026-12-04', 1, 100, 2.00, None, None, 'OPEN', '', '')])
    c.executemany("INSERT INTO leg_checks VALUES (?,?,?,?,?)", [('PXl', T, 31.0, 0.8, 'GOOD'), ('PXs', T, 1.80, 0.25, 'GOOD')])
    c.execute("INSERT INTO checks VALUES ('PMX',?,?,?,?,?)", (T, 105.0, None, None, 'GOOD'))
    c.commit(); c.close()

    c = sqlite3.connect(db)
    c.execute("INSERT INTO stock_positions VALUES ('a','CC',100,55.0)")
    c.commit(); c.close()
    import helm.exit_flags as EF
    _orig_fire, _orig_pend = EF.diag_firing_now, EF.pending
    EF.diag_firing_now = lambda conn, pid: set()          # the diagonal rule's own tests live in exit_flags
    EF.pending = lambda conn, as_of=None, ensure=True: []
    try:
        ALL = advise(db=db, today=date(2026, 10, 24))
        R = {r["ticker"]: r for r in ALL if r["verb"] != "OVER_RULE"}
        F = [r for r in ALL if r["verb"] == "OVER_RULE"]
    finally:
        EF.diag_firing_now, EF.pending = _orig_fire, _orig_pend
    v = lambda t: R[t]["verb"]
    ok(v("HLD") == "HOLD" and not R["HLD"]["needs_you"] and "30% of the profit taken" in R["HLD"]["reason"]
       and R["HLD"]["act"] == "resting buy-back at $1.00", "hold: %s / %s" % (R["HLD"]["reason"], R["HLD"]["act"]))
    ok(R["HLD"]["marks"] == [], "earnings after expiry is not a mark")
    ok(v("TP") == "TAKE_PROFIT" and "$1.00" in R["TP"]["act"], "take profit at half the credit: %s" % R["TP"]["act"])
    ok(v("T21") == "TIME_21" and "20 days" in R["T21"]["headline"], "21-day rule: %s" % R["T21"]["headline"])
    ok(v("TB") == "TESTED" and R["TB"]["lean"].startswith("HELM leans: take the shares") and "grades B" in R["TB"]["lean"],
       "tested, grade B leans to assignment")
    ok(v("TD") == "TESTED" and R["TD"]["lean"].startswith("No default") and "grades D" in R["TD"]["lean"],
       "tested, latest grade D: no default (older A ignored)")
    ok(v("ER") == "HOLD" and R["ER"]["marks"] == ["earnings 10-28 (in 4 days)"] and not R["ER"]["needs_you"],
       "earnings inside 7 days -> a highlighted mark on the holding line: %s" % R["ER"]["marks"])
    FH = {f["headline"]: f for f in F}
    ok("BIG is over the name limit" in FH and "$20,000 over the $60,000 limit" in FH["BIG is over the name limit"]["reason"]
       and "closing the put frees $80,000" in FH["BIG is over the name limit"]["act"], "name-limit flag with the choices")
    ok("Tech is over the sector limit" in FH, "sector-limit flag")
    ok("TD grades D -- below C" in FH and "TB grades B -- below C" not in FH and "BIG grades A -- below C" not in FH
       and "HLD grades ungraded -- below C" in FH, "grade flags: D and ungraded flagged, A-C not")
    ok("CC covered call has earnings inside the trade" in FH, "covered call with earnings inside -> flag")
    ok(all(f["needs_you"] and f["reason"] and f["act"] for f in F), "every flag carries its reason and choices")
    ok(v("OLD") == "STALE" and "12 days old" in R["OLD"]["reason"], "stale reading not advised from: %s" % R["OLD"]["reason"])
    ok(v("DG") == "DECIDE" and R["DG"]["lean"] is None and "$18.00 through" in R["DG"]["reason"]
       and "200 shares called away at 235" in R["DG"]["act"], "ITM short expiring this week: decide, no lean")
    ok(v("LL") == "LOSS_LIMIT" and R["LL"]["numbers"]["loss_limit"] == -1525.0, "loss limit at half the long's cost")
    ok(v("PM") == "SELL_CALL" and R["PM"]["numbers"]["width_floor"] == 28.5 and "$28.50 above" in R["PM"]["act"],
       "PMCC: sell the next call; width floor = (3000 - 150)/100: %s" % R["PM"]["numbers"].get("width_floor"))
    ok(v("PMX") == "REVIEW_LONG", "PMCC long at 83 days -> review")
    ok(all(R[t]["reason"] for t in R), "every recommendation carries a reason")
    pr = sorted(R.values(), key=lambda r: (r["priority"], r["ticker"]))
    ok(pr[0]["verb"] == "STALE" and pr[-1]["verb"] == "HOLD", "sorted most urgent first")
    print("advice selftest:", "PASS (%d checks)" % 19 if not fails else "%d FAIL" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else (_report() or 0))
