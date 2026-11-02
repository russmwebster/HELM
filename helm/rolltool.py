"""helm/rolltool.py -- rolls, priced side by side (s146, the PMCC plan step 2).

`helm roll TICKER` reads every open real position on TICKER that has a short
option -- a cash-secured put, a covered call or a PMCC's short -- and shows:

  * up to three ROLLS: buy the current short back at its ASK, sell a new one at
    its BID (never the mid: a roll that is a credit at the mid can be a debit at
    the bid/ask -- GOOGL, 2026-10-30). Only a NET CREDIT after both legs' fees.
      - new short 30-45 days, spread < 10% of mid, a live IBKR two-sided quote,
        delta up to 0.50 (Russ 2026-11-01: a tested short rarely rolls back into
        its band for a credit) -- outside the strategy's band is FLAGGED;
      - put: same or LOWER strike; the 10-day earnings veto and fail-closed rule
        of the put tool; the sleeve/name/sector limits with the freed collateral
        counted; a name graded below C is shown, flagged;
      - covered call: same or HIGHER strike, at or above the effective basis, no
        earnings inside the trade (calltool's rule);
      - PMCC short: same or HIGHER strike, expiring before the long, and THE
        WIDTH TEST at the new strike with the current short counted as bought
        back at its ask (fillopens.width_test -- the one function); earnings
        inside allowed, flagged.
    Ranked by the most room: calls highest strike first, puts lowest first;
    then the larger credit.
  * the ALTERNATIVES in dollars: put -- take the shares (HELM's lean by grade) or
    buy back; covered call -- let the shares go or buy back; PMCC -- buy back the
    short only, close the whole trade, or let the short be assigned.

HELM informs, never acts. Writes only roll_runs / roll_items / roll_contracts.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

from helm import puttool as P

RULE_VERSION = ("rolls-v1 (s146: net credit at the bid/ask after fees; 30-45 days; new delta <= 0.50, "
                "flagged outside the band; each strategy's strike, earnings and width rules)")
NEW_DELTA_MAX = 0.50
NET_DELTA = (0.05, 0.60)              # the net cast before the rules, so reasons are true
BANDS = {"put": (0.25, 0.30), "cc": (0.20, 0.35), "pmcc": (0.20, 0.30)}
CHOICES = 3
FEE_FALLBACK = 0.66                   # $ a contract a leg, when the downloads give none
PASSING = ("A", "B", "C", "FUND")
KIND_OF = {"CSP": "put", "COVERED_CALL": "cc", "PMCC": "pmcc"}

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS roll_runs (
    id TEXT PRIMARY KEY, run_at TEXT NOT NULL, rule_version TEXT NOT NULL, market_open INTEGER,
    n_positions INTEGER, fee_per_contract REAL, note TEXT
);
CREATE TABLE IF NOT EXISTS roll_items (
    run_id TEXT NOT NULL REFERENCES roll_runs(id), position_id TEXT NOT NULL, ticker TEXT NOT NULL,
    kind TEXT NOT NULL, short_strike REAL, short_expiry TEXT, contracts INTEGER, credit REAL,
    cur_bid REAL, cur_ask REAL, spot REAL, why_none TEXT, flags_json TEXT, alternatives_json TEXT,
    PRIMARY KEY (run_id, position_id)
);
CREATE TABLE IF NOT EXISTS roll_contracts (    -- every contract looked at; choice NULL = left off
    run_id TEXT NOT NULL REFERENCES roll_runs(id), position_id TEXT NOT NULL,
    expiry TEXT NOT NULL, strike REAL NOT NULL, dte INTEGER, delta REAL, bid REAL, ask REAL, mid REAL,
    spread_pct REAL, net_credit REAL, fees REAL, choice INTEGER, reason TEXT, flags TEXT,
    gamma REAL, theta REAL, vega REAL, iv REAL, extra_json TEXT,
    PRIMARY KEY (run_id, position_id, expiry, strike)
);
"""


def _half_up(x):
    from helm.bookview import _half_up as h
    return h(x)


# ---------------------------------------------------------------- pure rules
def roll_reason(c, pos, cur_ask, ctx):
    """None if contract c is a roll that meets every rule, else the first broken.
    pos: {kind, strike, contracts, dte}; ctx: today, fee, latest_expiry (cc),
    long_expiry + width_ok (pmcc)."""
    kind, k0, n = pos["kind"], pos["strike"], pos["contracts"]
    d, dte, sp = c.get("delta"), c.get("dte"), c.get("spread_pct")
    if c.get("source") not in ("ibkr", "ibkr-live"):
        return "no live IBKR quote"
    if not c.get("bid") or not c.get("ask") or c["bid"] <= 0 or c["ask"] <= 0:
        return "no two-sided market"
    if dte is None or not (P.DTE_MIN <= dte <= P.DTE_MAX):
        return "outside 30-45 days"
    if kind == "put" and c["strike"] > k0 + 1e-9:
        return "strike above the current %g" % k0
    if kind != "put" and c["strike"] < k0 - 1e-9:
        return "strike below the current %g" % k0
    if net_credit(c["bid"], cur_ask, n, ctx["fee"]) <= 0:
        return "no net credit after fees"
    if d is None or abs(d) > NEW_DELTA_MAX + 1e-9:
        return "delta above 0.50"
    if kind == "cc":
        if c["strike"] < ctx["eff_basis"] - 1e-9:
            return "strike below the effective basis"
        if ctx.get("latest_expiry") is None or P._d(c["expiration"]) > ctx["latest_expiry"]:
            return "earnings inside the trade"
    if kind == "pmcc":
        if P._d(c["expiration"]) >= P._d(ctx["long_expiry"]):
            return "expires on or after the long"
        if not ctx["width_ok"](c["strike"]):
            return "fails the width test (counting the buy-back)"
    if sp is None or sp >= P.SPREAD_MAX_PCT:
        return "spread 10% of mid or wider"
    return None


def net_credit(new_bid, cur_ask, n, fee):
    """Dollars: sell the new at its bid, buy the old back at its ask, both legs' fees."""
    return round((new_bid - cur_ask) * 100 * n - 2 * fee * n, 2)


ORDER = ("no net credit after fees", "fails the width test (counting the buy-back)", "strike below the effective basis",
         "earnings inside the trade", "expires on or after the long", "delta above 0.50",
         "spread 10% of mid or wider", "outside 30-45 days", "no two-sided market", "no live IBKR quote")


def choose(rows, pos, cur_ask, ctx, n=CHOICES):
    """(picks, why_none, reasons{id: reason}). Calls: highest strike first; puts: lowest first;
    then the larger credit, then the nearer expiry."""
    why = {id(c): roll_reason(c, pos, cur_ask, ctx) for c in rows}
    ok = [c for c in rows if why[id(c)] is None]
    sgn = 1 if pos["kind"] == "put" else -1
    ok.sort(key=lambda c: (sgn * c["strike"], -net_credit(c["bid"], cur_ask, pos["contracts"], ctx["fee"]), c["dte"]))
    if ok:
        return ok[:n], None, why
    if not rows:
        return [], "IBKR returned no two-sided quotes 30-45 days out", why
    seen = {w for w in why.values() if w}
    side = [w for w in seen if w.startswith("strike above the current") or w.startswith("strike below the current")]
    lead = [w for w in ORDER if w in seen]
    if "no net credit after fees" in seen:
        best = max((net_credit(c["bid"], cur_ask, pos["contracts"], ctx["fee"]) for c in rows
                    if why[id(c)] == "no net credit after fees"), default=None)
        lead[0:1] = ["no roll earns a net credit after fees (the best is %s)" % _usd(best)]
    return [], "no roll passes: " + "; ".join(lead + side), why


def roll_flags(c, pos, ctx):
    out = []
    lo, hi = BANDS[pos["kind"]]
    d = abs(c["delta"]) if c.get("delta") is not None else None
    if d is not None and not (lo - 1e-9 <= d <= hi + 1e-9):
        out.append("delta %.2f -- outside the %.2f-%.2f band" % (d, lo, hi))
    e = ctx.get("next_report")
    if pos["kind"] in ("pmcc", "put") and e and e <= P._d(c["expiration"]):
        out.append("earnings %s inside the trade%s" % (e, " -- allowed for a PMCC" if pos["kind"] == "pmcc" else ""))
    return out


def _usd(x):
    return "--" if x is None else ("-" if x < 0 else "") + "$" + format(int(round(abs(float(x)))), ",d")


def alternatives(pos, cur, ctx):
    """The non-roll choices, in dollars. cur: the current short's quote (or None)."""
    n, k, credit, fee = pos["contracts"], pos["strike"], pos["credit"], ctx["fee"]
    ask = cur["ask"] if cur else None
    bb = None if ask is None else round((credit - ask) * 100 * n - fee * n, 2)
    out = []
    buy = {"key": "buy_back", "cost": None if ask is None else round(ask * 100 * n + fee * n, 2), "result": bb}
    if pos["kind"] == "put":
        g = ctx.get("grade")
        out.append({"key": "take_shares", "label": "Take the shares",
                    "text": "%d shares at %g (%s), effective basis %s a share" % (
                        100 * n, k, _usd(k * 100 * n), "$%.2f" % (k - credit)),
                    "lean": ("HELM leans: take the shares -- %s grades %s" % (pos["ticker"], g)) if g in PASSING
                    else "No default: %s grades %s" % (pos["ticker"], g or "ungraded"),
                    "then": "cd ~/Projects/helm && helm calls --if-assigned %s" % pos["ticker"]})
        buy.update({"label": "Buy back and close", "text": "buy at about %s: %s on the trade" % (
            "--" if ask is None else "$%.2f" % ask, _usd(bb))})
        out.append(buy)
    elif pos["kind"] == "cc":
        eb = ctx["eff_basis"]
        res = round((k - eb) * 100 * n + credit * 100 * n, 2)
        out.append({"key": "let_go", "label": "Let the shares go", "result": res,
                    "text": "%d shares out at %g: %s with the call's premium (effective basis $%.2f)" % (
                        100 * n, k, _usd(res), eb)})
        buy.update({"label": "Buy the call back", "text": "buy at about %s: %s on the call; the shares stay" % (
            "--" if ask is None else "$%.2f" % ask, _usd(bb))})
        out.append(buy)
    else:
        buy.update({"label": "Buy back the short only", "text": "buy at about %s: %s on the short; the long stays "
                    "and `cd ~/Projects/helm && helm calls` offers the next short" % (
                        "--" if ask is None else "$%.2f" % ask, _usd(bb))})
        out.append(buy)
        lb = ctx.get("long_bid")
        whole = None
        if lb is not None and ask is not None:
            whole = round(ctx["closed_result"] + (lb - ctx["long_price"]) * 100 * n + (credit - ask) * 100 * n
                          - 2 * fee * n, 2)
        out.append({"key": "close_all", "label": "Close the whole trade", "result": whole,
                    "text": "long at its bid %s, short at its ask %s: %s on the whole trade, rent banked included" % (
                        "--" if lb is None else "$%.2f" % lb, "--" if ask is None else "$%.2f" % ask, _usd(whole))})
        least = round((k - ctx["long_strike"] - ctx["floor_if_assigned"]) * 100 * n, 2)
        out.append({"key": "let_assigned", "label": "Let the short be assigned", "result": least,
                    "text": "at least %s on the whole trade (the long sold for no more than its intrinsic value; "
                            "its time value left is extra)" % _usd(least)})
    return out


# ---------------------------------------------------------------- database reads
def fee_per_contract(conn):
    """Median fee a contract a leg from the downloads' option rows; else FEE_FALLBACK."""
    try:
        from helm import netpnl as N
        rows = N.fee_rows(conn, "symbol LIKE '-%%' AND qty != 0")
        per = sorted((float(r["commission"] or 0) + float(r["fees"] or 0)) / abs(float(r["qty"]))
                     for r in rows if (r["commission"] or r["fees"]))
        if per:
            return round(per[len(per) // 2], 2)
    except Exception:
        pass
    return FEE_FALLBACK


def positions(conn, ticker, account_id=None):
    """Open real positions on ticker with exactly one open short option."""
    out = []
    q = ("SELECT * FROM positions WHERE book='REAL' AND status='OPEN' AND ticker=? AND strategy IN "
         "('CSP','COVERED_CALL','PMCC')" + (" AND account_id=?" if account_id else "") + " ORDER BY id")
    for p in conn.execute(q, (ticker,) + ((account_id,) if account_id else ())).fetchall():
        legs = [dict(l) for l in conn.execute("SELECT * FROM legs WHERE position_id=? ORDER BY id", (p["id"],))]
        opts = [l for l in legs if l["option_type"] in ("CALL", "PUT")]
        shorts = [l for l in opts if l["status"] == "OPEN" and l["direction"] == "SHORT"]
        out.append({"p": dict(p), "legs": opts, "shorts": shorts})
    return out


def fetch_roll(ticker, opt_type, strike, expiry, long_pin=None, today=None):
    """IBKR quotes: the current short (pinned, any delta) and every contract 30-45
    days out in the wide net; for a PMCC also its long (pinned). Spot and the listed
    expiries come from Yahoo, as in the put and call tools.
    -> {rows, cur, long_q, spot, exdiv, err}"""
    from helm.cli import open_cmd as oc
    today = today or date.today()
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        spot = float(tk.fast_info.last_price)
        exps = tuple(tk.options)
    except Exception as e:
        return {"rows": [], "cur": None, "long_q": None, "spot": None, "exdiv": None,
                "err": "price or expiries not read: %s" % str(e)[:80]}
    exdiv = None
    try:
        v = (tk.info or {}).get("exDividendDate")
        if v:
            exdiv = datetime.utcfromtimestamp(int(v)).date() if not isinstance(v, str) else P._d(v)
    except Exception:
        exdiv = None
    exps_in, why = P.target_expiries(exps, today)
    atr = spot * 0.05
    try:
        h = tk.history(period="30d", interval="1d")
        if not h.empty and len(h) >= 14:
            a = float((h["High"] - h["Low"]).rolling(14).mean().iloc[-1])
            if a == a and a > 0:
                atr = a
    except Exception:
        pass
    exp = str(expiry)[:10]
    targets = list(exps_in)
    if exp not in [e for e, _ in targets]:
        targets.append((exp, (P._d(exp) - today).days))
    rows = oc.fetch_chain_from_ibkr(ticker, opt_type, targets, spot, atr, NET_DELTA[0], NET_DELTA[1],
                                    BANDS["pmcc"], spread_threshold=P.NET_SPREAD, pin=(float(strike), exp))
    cur = next((r for r in rows if r["expiration"] == exp and abs(r["strike"] - float(strike)) < 1e-6), None)
    new = [r for r in rows if r["expiration"] in {e for e, _ in exps_in}]
    long_q = None
    if long_pin:
        lk, le = float(long_pin[0]), str(long_pin[1])[:10]
        lr = oc.fetch_chain_from_ibkr(ticker, "CALL", [(le, (P._d(le) - today).days)], spot, atr, 0.0, 1.0,
                                      BANDS["pmcc"], spread_threshold=P.NET_SPREAD, pin=(lk, le))
        long_q = next((r for r in lr if abs(r["strike"] - lk) < 1e-6), None)
    return {"rows": new, "cur": cur, "long_q": long_q, "spot": spot, "exdiv": exdiv,
            "err": None if exps_in else why}


# ---------------------------------------------------------------- build
def build_one(conn, pos_row, today, fee, fetch=fetch_roll):
    """One position -> an item dict (not recorded)."""
    from helm import advice as Adv
    from helm.fillopens import width_floor, width_test
    p, legs, shorts = pos_row["p"], pos_row["legs"], pos_row["shorts"]
    kind = KIND_OF[p["strategy"]]
    item = {"position_id": p["id"], "ticker": p["ticker"], "kind": kind, "strategy": p["strategy"],
            "flags": [], "rolls": [], "contracts_seen": [], "alternatives": [], "why_none": None,
            "cur": None, "spot": None}
    if len(shorts) != 1:
        item["why_none"] = "%d open short legs -- nothing to roll" % len(shorts) if shorts else "no short open"
        return item
    S = shorts[0]
    n = int(S["contracts"] or 0)
    pos = {"kind": kind, "ticker": p["ticker"], "strike": float(S["strike"]), "contracts": n,
           "credit": float(S["open_price"] or 0), "expiry": str(S["expiration"])[:10],
           "dte": (P._d(S["expiration"]) - today).days, "opt_type": S["option_type"]}
    item.update({k: pos[k] for k in ("strike", "contracts", "credit", "expiry", "dte", "opt_type")})
    g = Adv._grade(conn, p["ticker"])
    is_fund = g == "FUND"
    ctx = {"today": today, "fee": fee, "grade": g}
    long_pin = None
    if kind == "pmcc":
        L = [l for l in legs if l["status"] == "OPEN" and l["direction"] == "LONG"]
        if len(L) != 1:
            item["why_none"] = "can't read this PMCC: %d open long legs" % len(L)
            return item
        L = L[0]
        long_pin = (L["strike"], L["expiration"])
        rest_closed = [l for l in legs if l["status"] != "OPEN"]
        ctx.update({"long_expiry": str(L["expiration"])[:10], "long_strike": float(L["strike"]),
                    "long_price": float(L["open_price"] or 0),
                    "closed_result": round(sum(((l["open_price"] or 0) - (l["close_price"] or 0)) *
                                               (l["multiplier"] or 100) * (l["contracts"] or 0)
                                               * (1 if l["direction"] == "SHORT" else -1) for l in rest_closed), 2),
                    "floor_if_assigned": width_floor([dict(l, status="CLOSED", close_price=0.0) if l["id"] == S["id"]
                                                      else l for l in legs])[0]})
    if kind == "cc":
        from helm import calltool as C
        hs = {h["ticker"]: h for h in C.holdings(conn)}
        h = hs.get(p["ticker"])
        ctx["eff_basis"] = h["effective_basis"] if h else None
        if ctx["eff_basis"] is None:
            item["why_none"] = "HELM holds no %s shares to read the effective basis from" % p["ticker"]
            return item
        latest, eline = C.earnings_cutoff(is_fund, P._next_report(conn, p["ticker"], today),
                                          P._last_report(conn, p["ticker"], today), today)
        ctx["latest_expiry"] = latest
        item["earnings"] = eline
    else:
        nr = None if is_fund else P._next_report(conn, p["ticker"], today)
        ctx["next_report"] = nr
    if kind == "put":
        ok, eline, _ = P.earnings_check(is_fund, P._next_report(conn, p["ticker"], today),
                                        P._last_report(conn, p["ticker"], today), today)
        item["earnings"] = eline
        ctx["earn_ok"] = ok
        if g not in PASSING:
            item["flags"].append("%s grades %s -- the rules wouldn't open this put today" % (p["ticker"], g or "ungraded"))
    q = fetch(p["ticker"], pos["opt_type"], pos["strike"], pos["expiry"], long_pin)
    item["spot"] = q.get("spot")
    item["cur"] = q.get("cur")
    if kind == "pmcc":
        ctx["long_bid"] = (q.get("long_q") or {}).get("bid")
    if item["cur"] is None:
        item["why_none"] = q.get("err") or "IBKR gave no two-sided quote for the current short"
        item["alternatives"] = alternatives(pos, None, ctx)
        return item
    ask = item["cur"]["ask"]
    if kind == "pmcc":
        legs_after = [dict(l, status="CLOSED", close_price=ask) if l["id"] == S["id"] else l for l in legs]
        ctx["width_ok"] = lambda k, la=legs_after: bool(width_test(la, k, n)[0])
        ctx["floor_after"] = width_floor(legs_after)[0]
    item["alternatives"] = alternatives(pos, item["cur"], ctx)
    if kind == "put" and not ctx["earn_ok"]:
        item["why_none"] = "no put roll: " + item["earnings"]
        return item
    if q.get("err"):
        item["why_none"] = q["err"]
        return item
    rows = q["rows"]
    picks, why_none, why = choose(rows, pos, ask, ctx)
    item["why_none"] = why_none
    chosen = {id(c): i for i, c in enumerate(picks, 1)}
    for c in rows:
        nc = net_credit(c["bid"], ask, n, fee) if c.get("bid") else None
        fl = roll_flags(c, pos, ctx) if id(c) in chosen else []
        extra = {}
        if kind == "put" and id(c) in chosen:
            from helm.fillopens import _flags
            lim, _g = _flags(conn, p["ticker"], "CSP", c["strike"] * 100 * n, pos["strike"] * 100 * n)
            fl += [x for x in lim if not x.startswith("grade ")]
            extra["collateral_change"] = round((c["strike"] - pos["strike"]) * 100 * n, 2)
        if kind == "pmcc" and id(c) in chosen:
            extra["floor_strike_after"] = round(ctx["long_strike"] + ctx["floor_after"], 2)
        rec = {"expiry": c["expiration"], "strike": c["strike"], "dte": c["dte"], "delta": c.get("delta"),
               "bid": c.get("bid"), "ask": c.get("ask"), "mid": c.get("mid"), "spread_pct": c.get("spread_pct"),
               "net_credit": nc, "fees": round(2 * fee * n, 2), "choice": chosen.get(id(c)),
               "reason": why[id(c)], "flags": "; ".join(fl) or None, "gamma": c.get("gamma"),
               "theta": c.get("theta"), "vega": c.get("vega"), "iv": c.get("iv"),
               "days_added": c["dte"] - pos["dte"], "rest_price": _half_up(c["bid"] * 0.5) if c.get("bid") else None,
               "extra": extra}
        item["contracts_seen"].append(rec)
        if rec["choice"]:
            item["rolls"].append(rec)
    item["rolls"].sort(key=lambda r: r["choice"])
    if q.get("exdiv") and kind != "put" and today <= q["exdiv"]:
        item["flags"].append("ex-dividend %s (Yahoo) -- a call in the money before it can be assigned early" % q["exdiv"])
    return item


def build(conn, today, tickers, fetch=fetch_roll, account_id=None):
    fee = fee_per_contract(conn)
    items, missing = [], []
    for t in tickers:
        rows = positions(conn, t.upper(), account_id)
        if not rows:
            missing.append(t.upper())
        for r in rows:
            items.append(build_one(conn, r, today, fee, fetch))
    return {"id": "rolls_" + uuid.uuid4().hex[:12], "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rule_version": RULE_VERSION, "market_open": P._market_open(), "fee_per_contract": fee,
            "items": items, "missing": missing}


def ensure_tables(conn):
    conn.executescript(TABLES_SQL)


def record(conn, run, note=None):
    ensure_tables(conn)
    conn.execute("INSERT INTO roll_runs VALUES (?,?,?,?,?,?,?)",
                 (run["id"], run["run_at"], run["rule_version"], run["market_open"], len(run["items"]),
                  run["fee_per_contract"], note))
    for it in run["items"]:
        cur = it.get("cur") or {}
        conn.execute("INSERT INTO roll_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (run["id"], it["position_id"], it["ticker"], it["kind"], it.get("strike"), it.get("expiry"),
                      it.get("contracts"), it.get("credit"), cur.get("bid"), cur.get("ask"), it.get("spot"),
                      it.get("why_none"), json.dumps(it["flags"]), json.dumps(it["alternatives"], sort_keys=True)))
        for c in it["contracts_seen"]:
            conn.execute("INSERT OR IGNORE INTO roll_contracts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (run["id"], it["position_id"], c["expiry"], c["strike"], c["dte"], c["delta"], c["bid"],
                          c["ask"], c["mid"], c["spread_pct"], c["net_credit"], c["fees"], c["choice"], c["reason"],
                          c["flags"], c["gamma"], c["theta"], c["vega"], c["iv"],
                          json.dumps(dict(c["extra"], days_added=c["days_added"], rest_price=c["rest_price"]))))
    conn.commit()


# ---------------------------------------------------------------- self-test
def _selftest():
    import sqlite3
    fails = []

    def ok(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)

    T = date(2026, 11, 2)
    fee = 0.66
    ok(net_credit(4.00, 3.50, 2, fee) == round(0.50 * 200 - 2.64, 2), "net credit = (bid - ask) x 100 x n - two legs' fees")

    def q(k, bid, ask, d, exp="2026-12-11", dte=39):
        return {"source": "ibkr", "strike": k, "bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 2),
                "delta": d, "dte": dte, "expiration": exp, "spread_pct": round((ask - bid) / ((bid + ask) / 2) * 100, 1)}
    put = {"kind": "put", "ticker": "AMZN", "strike": 245.0, "contracts": 2, "credit": 5.00, "dte": 18}
    ctx = {"today": T, "fee": fee, "grade": "C"}
    cur_ask = 9.00
    rows = [q(245, 11.0, 11.4, 0.48), q(240, 8.6, 8.9, 0.42), q(235, 9.05, 9.3, 0.36), q(250, 14.0, 14.4, 0.55),
            q(230, 5.0, 5.3, 0.30), q(237.5, 9.6, 11.6, 0.39)]
    picks, why, _ = choose(rows, put, cur_ask, ctx)
    ok([c["strike"] for c in picks] == [235, 245],
       "put roll: lowest strike first that still credits after fees; 240 (debit), 250 (higher strike), "
       "230 (debit), 237.5 (spread) left off: %s" % [c["strike"] for c in picks])
    ok(roll_reason(q(235, 9.01, 9.3, 0.36), put, cur_ask, ctx) == "no net credit after fees",
       "a $2 credit (2 x 100 x $0.01) under $2.64 of fees fails")
    ok(roll_reason(q(235, 9.02, 9.3, 0.36), put, cur_ask, ctx) is None, "a $4 credit over $2.64 of fees passes")
    ok(roll_reason(q(250, 14.0, 14.4, 0.55), put, cur_ask, ctx) == "strike above the current 245", "a put rolls down, never up")
    ok(roll_reason(q(245, 11.0, 11.4, 0.51), put, cur_ask, ctx) == "delta above 0.50", "delta 0.51 fails")
    ok(roll_reason(q(245, 11.0, 11.4, 0.50), put, cur_ask, ctx) is None, "delta 0.50 passes")
    ok(roll_reason(q(245, 11.0, 11.4, 0.48, dte=29), put, cur_ask, ctx) == "outside 30-45 days", "29 days fails")
    # GOOGL 10-30: all intrinsic, no credit
    g = {"kind": "put", "ticker": "GOOGL", "strike": 335.0, "contracts": 1, "credit": 8.38, "dte": 21}
    _, why, _ = choose([q(335, 54.2, 55.6, 0.97), q(330, 49.0, 50.5, 0.96)], g, 54.60, ctx)
    ok(why.startswith("no roll passes: no roll earns a net credit after fees (the best is -$"), "GOOGL: says no roll earns a credit: %s" % why)
    rf = roll_flags(q(235, 9.05, 9.3, 0.36), put, dict(ctx, next_report=date(2026, 12, 1)))
    ok(rf == ["delta 0.36 -- outside the 0.25-0.30 band", "earnings 2026-12-01 inside the trade"],
       "a roll outside the put band and over earnings is flagged: %s" % rf)
    # covered call
    cc = {"kind": "cc", "ticker": "GOOGL", "strike": 330.0, "contracts": 1, "credit": 3.0, "dte": 20}
    cctx = dict(ctx, eff_basis=326.62, latest_expiry=date(2027, 1, 31))
    picks, _, _ = choose([q(330, 6.0, 6.2, 0.45), q(335, 4.4, 4.6, 0.38), q(340, 3.9, 4.0, 0.31), q(325, 8.0, 8.2, 0.5)],
                         cc, 4.0, cctx)
    ok([c["strike"] for c in picks] == [335, 330], "covered call: highest strike that credits first; 340 debit, 325 below current")
    ok(roll_reason(q(335, 4.4, 4.6, 0.38), cc, 4.0, dict(cctx, eff_basis=336)) == "strike below the effective basis",
       "a covered-call roll below the effective basis fails")
    ok(roll_reason(q(335, 4.4, 4.6, 0.38), cc, 4.0, dict(cctx, latest_expiry=date(2026, 12, 1))) == "earnings inside the trade",
       "a covered-call roll over earnings fails")
    # PMCC alternatives and width test
    from helm.fillopens import width_test, width_floor
    legs = [{"id": "L", "direction": "LONG", "status": "OPEN", "option_type": "CALL", "strike": 160.0, "contracts": 1,
             "multiplier": 100, "open_price": 62.90, "close_price": None, "expiration": "2027-12-17"},
            {"id": "S", "direction": "SHORT", "status": "OPEN", "option_type": "CALL", "strike": 225.0, "contracts": 1,
             "multiplier": 100, "open_price": 3.40, "close_price": None, "expiration": "2026-12-04"}]
    pm = {"kind": "pmcc", "ticker": "NVDA", "strike": 225.0, "contracts": 1, "credit": 3.40, "dte": 21}
    after = [dict(l, status="CLOSED", close_price=9.0) if l["id"] == "S" else l for l in legs]
    pctx = dict(ctx, long_expiry="2027-12-17", long_strike=160.0, long_price=62.90, closed_result=0.0, long_bid=80.0,
                width_ok=lambda k: bool(width_test(after, k, 1)[0]),
                floor_if_assigned=width_floor([dict(l, status="CLOSED", close_price=0.0) if l["id"] == "S" else l
                                               for l in legs])[0])
    ok(abs(width_floor(after)[0] - 68.50) < 1e-9, "a buy-back at $9.00 on a $3.40 short raises the floor to 62.90 + 5.60 = 68.50")
    ok(roll_reason(q(228.5, 9.8, 10.0, 0.45), pm, 9.0, pctx) == "fails the width test (counting the buy-back)",
       "PMCC roll to 228.50 fails the width test once the buy-back is counted")
    ok(roll_reason(q(228.5, 9.8, 10.0, 0.45), pm, 9.0, dict(pctx, width_ok=lambda k: bool(width_test(legs, k, 1)[0]))) is None,
       "...and would pass if the buy-back were not counted (the check can fail)")
    ok(roll_reason(q(230, 9.6, 9.8, 0.42), pm, 9.0, pctx) is None, "PMCC roll to 230 passes")
    ok(roll_reason(q(230, 9.6, 9.8, 0.42, exp="2027-12-17", dte=40), pm, 9.0, pctx) == "expires on or after the long",
       "a PMCC short can't outlive the long")
    alt = {a["key"]: a for a in alternatives(pm, {"bid": 8.8, "ask": 9.0}, pctx)}
    ok(alt["buy_back"]["result"] == round((3.40 - 9.0) * 100 - 0.66, 2), "buy back the short: (credit - ask) x 100 - fee")
    ok(alt["close_all"]["result"] == round((80.0 - 62.90) * 100 + (3.40 - 9.0) * 100 - 1.32, 2),
       "close the whole trade: long at its bid + short at its ask, both fees")
    ok(abs(alt["let_assigned"]["result"] - (225 - 160 - 59.50) * 100) < 1e-6,
       "let it be assigned: at least (225 - 160 - (62.90 - 3.40)) x 100 = $550")
    alt = {a["key"]: a for a in alternatives(put, {"bid": 8.8, "ask": 9.0}, ctx)}
    ok(alt["take_shares"]["lean"].startswith("HELM leans: take the shares") and "$240.00" in alt["take_shares"]["text"],
       "put: take the shares at effective basis 245 - 5 = $240.00, HELM's lean while graded C")
    alt = {a["key"]: a for a in alternatives(put, None, dict(ctx, grade="D"))}
    ok(alt["take_shares"]["lean"].startswith("No default") and alt["buy_back"]["result"] is None,
       "graded D: no default; no quote: no buy-back figure")
    alt = {a["key"]: a for a in alternatives(cc, {"bid": 3.9, "ask": 4.0}, cctx)}
    ok(abs(alt["let_go"]["result"] - ((330 - 326.62) * 100 + 300)) < 1e-6, "covered call: let go = (strike - basis) x 100 + premium")

    # end to end on a tiny database
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE positions (id TEXT PRIMARY KEY, account_id TEXT, strategy TEXT, ticker TEXT, status TEXT, book TEXT);
      CREATE TABLE legs (id TEXT PRIMARY KEY, position_id TEXT, option_type TEXT, direction TEXT, strike REAL,
        expiration TEXT, contracts INT, multiplier INT, open_price REAL, close_price REAL, status TEXT);
      CREATE TABLE stock_positions (id TEXT PRIMARY KEY, account_id TEXT, ticker TEXT, shares INT, cost_basis REAL,
        acquired_at TEXT, notes TEXT, updated_at TEXT);
      CREATE TABLE watchlist (ticker TEXT, next_earnings TEXT);
      CREATE TABLE earnings_history (ticker TEXT, earn_date TEXT);
      CREATE TABLE approved_funds (ticker TEXT, bucket TEXT);
      CREATE TABLE ownership_quality (ticker TEXT, date TEXT, grade TEXT, updated_at TEXT);
      INSERT INTO ownership_quality VALUES ('NVDA','2026-10-24','B','x');
      INSERT INTO watchlist VALUES ('NVDA','2026-11-17');
      INSERT INTO positions VALUES ('N','a','PMCC','NVDA','OPEN','REAL');
      INSERT INTO legs VALUES ('NL','N','CALL','LONG',160,'2027-12-17',1,100,62.90,NULL,'OPEN');
      INSERT INTO legs VALUES ('NS','N','CALL','SHORT',225,'2026-12-04',1,100,3.40,NULL,'OPEN');
    """)
    chain = [q(230, 9.6, 9.8, 0.42), q(228.5, 9.8, 10.0, 0.45), q(240, 6.0, 6.3, 0.30), q(235, 7.8, 8.0, 0.36)]

    def fk(t, ot, k, e, lp=None):
        return {"rows": chain, "cur": {"strike": 225.0, "bid": 8.8, "ask": 9.0, "expiration": "2026-12-04"},
                "long_q": {"bid": 80.0} if lp else None, "spot": 231.0, "exdiv": None, "err": None}
    run = build(conn, T, ["NVDA", "XYZ"], fetch=fk)
    it = run["items"][0]
    ok([r["strike"] for r in it["rolls"]] == [230] and run["missing"] == ["XYZ"],
       "NVDA tested: one roll (230); 235/240 are debits, 228.50 fails the width test; XYZ has nothing open: %s"
       % [r["strike"] for r in it["rolls"]])
    ok(it["rolls"][0]["net_credit"] == round(0.60 * 100 - 1.32, 2), "230: sell at 9.60, buy back at 9.00, $60 - $1.32 fees")
    ok("earnings 2026-11-17 inside the trade -- allowed for a PMCC" in it["rolls"][0]["flags"]
       and "outside the 0.20-0.30 band" in it["rolls"][0]["flags"], "flags: earnings inside (allowed) and delta 0.42 above the band")
    ok(it["rolls"][0]["extra"]["floor_strike_after"] == 228.5, "the floor after the roll: 160 + 68.50 = 228.50")
    ok({c["strike"]: c["reason"] for c in it["contracts_seen"]}[228.5] == "fails the width test (counting the buy-back)",
       "every contract recorded with its reason")
    record(conn, run)
    ok(conn.execute("SELECT COUNT(*) FROM roll_contracts").fetchone()[0] == 4
       and conn.execute("SELECT choice FROM roll_contracts WHERE strike=230").fetchone()[0] == 1,
       "a run records every contract, the chosen one numbered")
    print("\n%d failed" % len(fails) if fails else "\nall passed")
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
