"""s156 (Russ, 2026-11-08; board cleanup step 4): Today's tables, as data, built to the display rules.

Every rule result comes from the engine's own comparisons -- the advice's rule rows (helm.advice.advise),
the limits (helm.advice.limit_row on helm.sleeve.book / helm.bookview), the latest recorded `helm roll`,
`helm calls` and `helm puts` runs. The board renders; it computes nothing. READ-ONLY. advice.json is untouched.
Prices are the stored mid (the readings' current_price), as on the cards.
"""
import json
import re
from datetime import date

from helm import advice as A
from helm import sleeve as S

SHORT_LABEL = {"tested": "tested", "time_21": "21-day", "take_profit": "50%", "loss_limit": "loss limit",
               "itm_expiry": "in the money", "long_review": "long review", "short_open": "no short",
               "cc_earnings": "earnings inside", "grade": "grade below C"}
LIMIT_KEYS = ("name_limit", "sector_limit", "book_delta")
BINDING = {"sleeve_total": "sleeve full", "sleeve_name": "name over", "sleeve_sector": "sector over"}


# ---------------------------------------------------------------- pure helpers
def roll_short(why):
    """The roll tool's reason, as a table cell."""
    if not why:
        return "none"
    m = re.search(r"earnings (\d{4}-(\d\d-\d\d))", why)
    if m:
        return "none · earnings %s" % m.group(2)
    if "net credit" in why:
        return "none · no net credit"
    tail = why.split(":", 1)[-1].strip()
    return "none · " + tail.split(";")[0].split(" (")[0]


def put_limits(c):
    return ("fits ×%d" % c["size_n"]) if c.get("size_n") else BINDING.get(c.get("size_binding"), "no room")


def put_earnings(line):
    """MM-DD when the report falls inside the trade, else None."""
    m = re.match(r"earnings \d{4}-(\d\d-\d\d) .*inside the trade", line or "")
    return m.group(1) if m else None


def calls_status(rej, n_choices):
    if n_choices:
        return "%d choice%s" % (n_choices, "" if n_choices == 1 else "s")
    if rej and rej.startswith("no call passes"):
        return "blocked"
    return "none" if rej else "not priced"


def _md(iso):
    return str(iso)[5:10]


def _label(conn, p):
    """Position label: 'CSCO 105 P 11-20 ×3', 'CVX PMCC 230 C 12-18', 'NVDA PMCC 160 C 12-17-27'."""
    legs = [dict(l) for l in conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN' AND "
                                          "option_type IN ('CALL','PUT') ORDER BY id", (p["id"],))]
    sh = [l for l in legs if l["direction"] == "SHORT"]
    lg = [l for l in legs if l["direction"] == "LONG"]
    if p["strategy"] in ("CSP", "COVERED_CALL") and sh:
        l = sh[0]
        return "%s %g %s %s ×%d" % (p["ticker"], l["strike"], l["option_type"][0], _md(l["expiration"]), l["contracts"] or 0), l
    if sh:
        l = sh[0]
        return "%s %s %g %s %s" % (p["ticker"], p["strategy"], l["strike"], l["option_type"][0], _md(l["expiration"])), l
    if lg:
        l = lg[0]
        e = str(l["expiration"])[:10]
        return "%s %s %g %s %s-%s" % (p["ticker"], p["strategy"], l["strike"], l["option_type"][0], e[5:10], e[2:4]), None
    return "%s %s" % (p["ticker"], p["strategy"]), None


def _calls_for(conn, ticker, kind="shares"):
    """The newest real `helm calls` run that read this holding: (run_at, reject or None, [choices])."""
    from helm import calltool as C
    if not S._has_table(conn, "call_runs"):
        return None
    have = {r[1] for r in conn.execute("PRAGMA table_info(call_candidates)")}
    cols = [c for c in C.CAND_COLS if c in have]
    key = ticker if kind == "shares" else ticker + " (PMCC)"
    for r in conn.execute("SELECT id, run_at, rejects_json FROM call_runs WHERE hypothetical=0 "
                          "ORDER BY run_at DESC, rowid DESC"):
        rej = (json.loads(r["rejects_json"] or "{}")).get(key)
        ch = []
        for x in conn.execute("SELECT %s FROM call_candidates WHERE run_id=? AND ticker=? ORDER BY choice"
                              % ", ".join(cols), (r["id"], ticker)):
            d = {c: None for c in C.CAND_COLS}
            d.update(zip(cols, tuple(x)))
            if (d.get("kind") or "shares") == kind:
                ch.append(d)
        if rej is not None or ch:
            return {"run_at": r["run_at"][:16], "reject": rej, "choices": ch}
    return None


# ---------------------------------------------------------------- the view
def view(account_id=None, db=None, today=None):
    today = today or date.today()
    recs = A.advise(db=db, account_id=account_id)
    recs = A.in_order(recs, A.position_keys(db=db, account_id=account_id))
    st = A.status(db=db, account_id=account_id)
    from helm import bookview, rolltool
    from helm import decide as D
    bv = bookview.view(account_id, db)
    b = S.book(account_id=account_id, db=db)
    L = bv["limits"]
    mk = bv["market"]
    out = {"status": st, "market": mk}
    out["figures"] = {"sleeve": b["share"], "in_use": b["used"], "room": b["free"], "theta": L["theta"],
                      "day_down": mk.get("typical_down"), "day_up": mk.get("typical_up"),
                      "bw_delta": L["bw_delta"], "delta_cap": L["delta_cap"]}
    cov = st.get("coverage") or {}
    out["missed"] = ", ".join("%s %s" % (t, s) for t, s in cov.get("missed") or []) or None
    out["missed_day"] = cov.get("day")

    # ---- book rules: the limits as rows; the hot ones listed with their largest
    pos_rows = bv["positions"]

    def top(key, n=3):
        rs = sorted((r for r in pos_rows if r.get(key)), key=lambda r: -abs(r[key] or 0))[:n]
        return " · ".join("%s%s %s" % (r["ticker"], " sh" if r["strategy"] == "SHARES" else "", A._m(abs(r[key])).replace("-", ""))
                          for r in rs)
    book = [dict(A.limit_row("sleeve", "Sleeve", b["used"], b["share"]), largest=None)]
    for nm in sorted(b["by_name"]):
        book.append(dict(A.limit_row("name_limit", "%s name limit (20%%)" % nm, b["by_name"][nm], b["name_cap"]),
                         largest=None))
    for sec in sorted(b["by_sector"]):
        names = sorted({it["ticker"] for it in b["items"] if it["sector"] == sec})
        book.append(dict(A.limit_row("sector_limit", "%s sector limit (30%%)" % sec, b["by_sector"][sec], b["sector_cap"]),
                         largest=" · ".join(names)))
    th_state = L["theta_state"]
    book.append({"key": "theta", "rule": "Theta range (0.05–0.12%)",
                 "state": "fired" if (th_state == "high" or (th_state == "low" and pos_rows)) else "pass",
                 "reading": "%s / %s–%s" % (A._m(L["theta"]), A._m(L["theta_lo"]), A._m(L["theta_hi"])),
                 "line": None, "result": {"high": "over", "low": "under"}.get(th_state),
                 "largest": top("theta") if th_state == "high" else None})
    book.append(dict(A.limit_row("book_delta", "Book delta limit (half the sleeve)", abs(L["bw_delta"]), L["delta_cap"]),
                     largest=top("bw_delta")))
    out["book_rules"] = [r for r in book if r["state"] in ("fired", "near")]
    out["book_pass"] = len([r for r in book if r["state"] == "pass"])

    # ---- positions: decisions (one of the position's own rules fired) and holding
    conn = S._conn(db)
    try:
        rolls = rolltool.latest_for(conn, [r["position_id"] for r in recs if r.get("position_id")])
        dec, hold = [], []
        for r in recs:
            pid = r.get("position_id")
            if not pid or r["verb"] == "OVER_RULE":
                continue
            p = conn.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
            label, short = _label(conn, p)
            nums = r.get("numbers") or {}
            rows = [x for x in r.get("rules") or [] if x["key"] not in LIMIT_KEYS]
            g = A._grade(conn, p["ticker"])
            if g not in A.PASSING:
                rows.append({"key": "grade", "rule": "Grade below C", "state": "fired",
                             "reading": "grade %s" % (g or "none"), "line": None})
            fired = [x for x in rows if x["state"] == "fired"]
            row = {"position_id": pid, "ticker": p["ticker"], "strategy": p["strategy"], "label": label,
                   "verb": r["verb"], "pnl": nums.get("pnl"), "stale": r["verb"] == "STALE",
                   "earnings": next((m.split()[1] for m in r.get("marks") or [] if m.startswith("earnings")), None)}
            if r["verb"] == "STALE":
                row["note"] = "not read yet" if "not read yet" in r["headline"] else "old reading"
                hold.append(row)
                continue
            pm = None
            if p["strategy"] == "PMCC":
                pm = D.pmcc_read(conn, pid, today)
                pm = None if pm.get("error") else pm
            # days: the short's (a put, a covered call, a PMCC's open short), else the long's
            if p["strategy"] in ("CSP", "COVERED_CALL"):
                row["days"] = nums.get("dte")
                row["close"] = None if nums.get("mark") is None else -nums["mark"] * 100 * (short["contracts"] or 0)
                kp = nums.get("kept_pct")
                row["kept"] = kp if (kp is not None and kp >= 0) else None
                k, sp = nums.get("strike"), nums.get("spot")
                row["cushion"] = (None if not sp else ((sp - k) / sp * 100.0 if p["strategy"] == "CSP"
                                                        else (k - sp) / sp * 100.0))
            elif pm:
                row["days"] = pm["Sh"]["dte"] if pm["Sh"] else pm["L"]["dte"]
                row["close"] = pm["num"]["close_mid"]
                if pm["Sh"] and pm["Sh"]["mid"] is not None and pm["Sh"]["open_price"]:
                    kp = (pm["Sh"]["open_price"] - pm["Sh"]["mid"]) / pm["Sh"]["open_price"] * 100.0
                    row["kept"] = kp if kp >= 0 else None
                    row["cushion"] = pm["num"]["to_short_pct"]
            nxt = next((x for x in rows if x["key"] == "time_21" and x["state"] == "next"), None)
            row["next"] = ("21 days: %s" % nxt["line"]) if nxt else None
            if not fired and not r["needs_you"]:
                hold.append(row)
                continue
            row["rule"] = " · ".join(SHORT_LABEL.get(x["key"], x["rule"].lower()) for x in fired) or r["label"].lower()
            first = fired[0] if fired else None
            if first and first["key"] == "short_open":
                w = next((x for x in rows if x["key"] == "width_floor"), None)
                row["reading"] = (w["reading"] if w else first["reading"])
            else:
                row["reading"] = first["reading"] if first else r["reason"]
            # the roll: only where the advice points to `helm roll` (the engine's own words decide when)
            row["roll"] = None
            if "helm roll" in (r.get("act") or ""):
                rl = rolls.get(pid)
                if rl is None:
                    row["roll"] = "not priced"
                    row["roll_cmd"] = True
                else:
                    f = rl["rolls"][0] if rl.get("rolls") else None
                    row["roll"] = ("%s $%g %s" % (_md(f["expiry"]), f["strike"], A._m(f.get("net_credit"), sign=True))
                                   if f else roll_short(rl.get("why_none")))
                    if rl["run_at"][:10] < today.isoformat():
                        row["roll"] += " (%s)" % _md(rl["run_at"])
                        row["roll_cmd"] = True
            row["assigned"] = (short["strike"] * 100 * (short["contracts"] or 0)
                               if (p["strategy"] == "CSP" and short is not None and
                                   any(x["key"] == "tested" for x in fired)) else None)
            ad = next((x for x in rows if x["key"] == "assign_default"), None)
            row["default"] = ("assign: %s · %s" % (ad["line"], ad["reading"].replace("grade ", ""))) if ad else None
            if first and first["key"] == "short_open":
                w = next((x for x in rows if x["key"] == "width_floor"), None)
                cv = D.pmcc_calls_view(_calls_latest(conn), p["ticker"],
                                       pm["closed"][-1]["close_date"][:10] if (pm and pm["closed"]) else None, pid)
                if cv.get("choices"):
                    row["default"] = "next short: %d choice%s" % (len(cv["choices"]), "" if len(cv["choices"]) == 1 else "s")
                elif cv.get("run_at") and not cv.get("stale"):
                    row["default"] = "next short: none passes"
                else:
                    row["default"] = "next short: not priced"
                    row["calls_cmd"] = True
            dec.append(row)

        # ---- HELM-held shares: one holding row each
        from helm import calltool as C
        shares, new_calls = [], []
        px = {r["ticker"]: r for r in pos_rows if r["strategy"] == "SHARES"}
        for h in (C.holdings(conn) if S._has_table(conn, "stock_positions") else []):
            t = h["ticker"]
            v = px.get(t) or {}
            eb = h.get("effective_basis")
            sp = v.get("spot")
            cl = _calls_for(conn, t)
            n_ch = len(cl["choices"]) if cl else 0
            row = {"ticker": t, "label": "%s shares ×%d" % (t, h["shares"]), "shares": h["shares"],
                   "pnl": (sp - eb) * h["shares"] if (sp is not None and eb is not None) else None,
                   "price_day": (str(v.get("as_of"))[:10] if v.get("as_of") else None), "basis": eb,
                   "covered": h["free_shares"] < h["shares"],
                   "calls": None if h["free_shares"] < 100 else calls_status(cl["reject"] if cl else None, n_ch),
                   "calls_run": cl["run_at"] if cl else None}
            shares.append(row)
            if n_ch:
                for c in cl["choices"]:
                    new_calls.append(dict(c, run_at=cl["run_at"]))
        out["shares"] = shares
        out["new_calls"] = new_calls
    finally:
        conn.close()
    out["decisions"], out["holding"] = dec, hold
    return out


def _calls_latest(conn):
    from helm import calltool as C
    return C.latest(conn, 0)


def held_on(conn, ticker, expiry=None, strike=None):
    """s157b (Russ, 2026-11-13): what HELM holds open now on this company (GOOG + GOOGL one name), for the put
    list's Held column -- "10 × same put" when it is the offered contract, else each put / call as "N × MM-DD S P",
    "PMCC", "100 sh". "" when nothing. Display only: nothing is offered, ranked or sized from it."""
    names = [t for t in {ticker} | {x for g in S.SAME_COMPANY if ticker in g for x in g}]
    q = ",".join("?" * len(names))
    out = []
    for r in conn.execute(
            "SELECT p.ticker, p.strategy, l.option_type, l.strike, l.expiration, l.contracts FROM positions p "
            "JOIN legs l ON l.position_id=p.id WHERE p.book='REAL' AND p.status='OPEN' AND l.status='OPEN' "
            "AND l.direction='SHORT' AND p.strategy IN ('CSP','COVERED_CALL') AND p.ticker IN (%s) "
            "ORDER BY p.ticker, l.expiration, l.strike" % q, names).fetchall():
        tk, strat, ot, k, exp, n = tuple(r)
        pre = "" if tk == ticker else tk + " "
        if (strat == "CSP" and tk == ticker and expiry and str(exp)[:10] == str(expiry)[:10]
                and strike is not None and abs(float(k) - float(strike)) < 1e-9):
            out.append("%s × same put" % n)
        else:
            out.append("%s%s × %s %g %s" % (pre, n, _md(exp), k, "P" if ot == "PUT" else "C"))
    for (tk,) in conn.execute("SELECT DISTINCT ticker FROM positions WHERE book='REAL' AND status='OPEN' "
                              "AND strategy IN ('PMCC','DIAGONAL') AND ticker IN (%s) ORDER BY ticker" % q, names):
        out.append(("" if tk == ticker else tk + " ") + "PMCC")
    has_sp = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_positions'").fetchone()
    if has_sp:
        for tk, sh in conn.execute("SELECT ticker, SUM(shares) FROM stock_positions WHERE ticker IN (%s) "
                                   "GROUP BY ticker HAVING SUM(shares) > 0 ORDER BY ticker" % q, names):
            out.append("%s%d sh" % ("" if tk == ticker else tk + " ", sh))
    return " · ".join(out)


def _held_conn():
    import sqlite3
    from helm.config import DB_PATH
    return sqlite3.connect("file:" + str(DB_PATH) + "?mode=ro", uri=True)


def puts_view(run, conn=None):
    """The put list's rows for Today, from puttool.latest's run (top 5, the best that fit).
    s157b: each row's Held -- what HELM holds open on the name now (held_on)."""
    from helm import puttool as P
    if not run or run.get("error"):
        return run
    own = conn is None
    try:
        conn = conn or _held_conn()
        held = {(c["ticker"], c["expiry"], c["strike"]): held_on(conn, c["ticker"], c["expiry"], c["strike"])
                for c in run["candidates"]}
    except Exception as e:        # display only -- never costs the list
        held, held_err = {}, "held not read (%s)" % str(e)[:60]
    else:
        held_err = None
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def row(c):
        return {"rank": c["rank"], "ticker": c["ticker"], "grade": c["grade"],
                "put": "%g P %s" % (c["strike"], _md(c["expiry"])), "dte": c["dte"], "delta": c["delta"],
                "mid": c["mid"], "rate": c["ann_return"], "collateral": c["collateral"], "rest": c["rest_price"],
                "earnings": put_earnings(c["earnings"]), "limits": put_limits(c), "fits": bool(c.get("size_n")),
                "held": held.get((c["ticker"], c["expiry"], c["strike"]), held_err or "")}
    cands = run["candidates"]
    return {"run_at": run["run_at"][:16], "market_open": run.get("market_open"),
            "n_universe": run["n_universe"], "n_checked": run["n_checked"], "n_found": run["n_found"],
            "n_declined": len([c for c in cands if not c.get("size_n")]),
            "top": [row(c) for c in cands[:P.TOP_N]], "fit": [row(c) for c in P.fitting(cands)]}


def _selftest():
    fails = []

    def ok(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)
    ok(roll_short("no put roll: earnings 2026-11-12, in 10 days (no entry within 10)") == "none · earnings 11-12", "roll: earnings")
    ok(roll_short("no roll passes: no roll earns a net credit after fees (the best is -$56); strike above") == "none · no net credit",
       "roll: no net credit")
    ok(roll_short(None) == "none", "roll: no reason")
    ok(put_limits({"size_n": 5}) == "fits ×5" and put_limits({"size_n": 0, "size_binding": "sleeve_sector"}) == "sector over"
       and put_limits({"size_n": 0, "size_binding": "sleeve_name"}) == "name over", "put limits")
    ok(put_earnings("earnings 2026-11-27 (21 days) -- inside the trade") == "11-27"
       and put_earnings("earnings 2026-12-23 (47 days)") is None and put_earnings("fund") is None, "put earnings inside only")
    ok(calls_status("no call passes: strike below the effective basis", 0) == "blocked" and calls_status(None, 2) == "2 choices"
       and calls_status(None, 0) == "not priced", "calls status")
    print("todayview selftest:", "PASS (6)" if not fails else "%d FAIL" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
