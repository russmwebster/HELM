# s163: patch a SCRATCH copy of helm (never the live folder). Usage: python3 patch_engine.py <helm dir>
import sys, io, os
root = sys.argv[1]
assert "/tmp/" in os.path.abspath(root), "scratch copies only"
def patch(rel, pairs):
    p = os.path.join(root, rel); s = io.open(p, encoding="utf-8").read()
    for old, new in pairs:
        n = s.count(old)
        assert n == 1, "%s: anchor found %d times: %r" % (rel, n, old[:70])
        s = s.replace(old, new)
    io.open(p, "w", encoding="utf-8").write(s)
    print("patched", rel, len(pairs))

# ------------------------------------------------------------------ helm/puttool.py
patch("helm/puttool.py", [
# 1. the index table
('''    size_note     TEXT,
    PRIMARY KEY (run_id, ticker)
);
"""
''', '''    size_note     TEXT,
    PRIMARY KEY (run_id, ticker)
);
CREATE TABLE IF NOT EXISTS put_index (            -- s163: the broad index funds, read whatever their IV rank
    run_id        TEXT NOT NULL REFERENCES put_runs(id),
    ticker        TEXT NOT NULL,
    bar_met       INTEGER,                    -- 1 = IV rank at or above the bar; 0 = below; NULL = no reading
    note          TEXT,                       -- why no contract is stored, when none is
    rank          INTEGER,                    -- its rank on the list when it passed every rule, else NULL
    grade         TEXT,
    sector        TEXT,
    expiry        TEXT,
    dte           INTEGER,
    strike        REAL,
    spot          REAL,
    bid REAL, ask REAL, mid REAL,
    spread_pct    REAL,
    delta REAL, gamma REAL, theta REAL, vega REAL,
    iv            REAL,
    iv_rank       REAL,
    ann_return    REAL,
    collateral    REAL,
    earnings      TEXT,
    rest_price    REAL,
    size_n        INTEGER,
    size_binding  TEXT,
    size_note     TEXT,
    PRIMARY KEY (run_id, ticker)
);
"""
'''),
# 2. one universe for the screen and the count
('''    names = [r[0] for r in conn.execute(
        "SELECT ticker FROM watchlist WHERE COALESCE(active,0)=1 ORDER BY ticker")]
    funds = {r[0] for r in conn.execute("SELECT ticker FROM approved_funds")}
    names = sorted(set(names) | funds)
    if tickers:
''', '''    names, funds = _names(conn)
    if tickers:
'''),
('''def screen(conn, today, tickers=None):
''', '''def _names(conn):
    """(every name the list looks at, the approved funds): the active watchlist plus the funds."""
    names = [r[0] for r in conn.execute(
        "SELECT ticker FROM watchlist WHERE COALESCE(active,0)=1 ORDER BY ticker")]
    funds = {r[0] for r in conn.execute("SELECT ticker FROM approved_funds")}
    return sorted(set(names) | funds), funds


def premium_count(conn):
    """s163 (Russ 2026-11-20, for the Book's daily market read): of the names the desk may sell --
    graded A-C or an approved fund, the list's own universe -- how many have an IV rank at or above
    the bar on the latest morning reading, split into stocks and funds. Read-only, no network."""
    from helm import advice as A
    ivr_date = _latest_ivr_date(conn)
    names, _funds = _names(conn)
    out = {"as_of": ivr_date, "bar": IVR_MIN, "checked": 0, "rich": 0, "no_reading": 0,
           "stocks": {"checked": 0, "rich": 0}, "funds": {"checked": 0, "rich": 0}}
    for t in names:
        g = A._grade(conn, t)
        if g not in PASSING:
            continue
        r = conn.execute("SELECT iv_rank FROM iv_history WHERE ticker=? AND date=?",
                         (t, ivr_date)).fetchone() if ivr_date else None
        ivr = r[0] if r else None
        if ivr is None:
            out["no_reading"] += 1
            continue
        for d in (out, out["funds" if g == "FUND" else "stocks"]):
            d["checked"] += 1
            d["rich"] += int(ivr >= IVR_MIN)
    return out


def screen(conn, today, tickers=None):
'''),
# 3. the row built in one place (the list and the index line share it)
('''        sec, _ = S.sector_of(conn, t)
        n, binding, note = S.size("CSP", t, best["strike"] * 100.0, db=db, b=b)
        earn = ch["earn_line"]
        if earnings_in_trade(ch["upcoming"], best["expiration"]):
            earn += " -- inside the trade"
        bests.append({
            "ticker": t, "grade": ch["grade"], "sector": sec,
            "expiry": best["expiration"], "dte": best["dte"], "strike": best["strike"],
            "spot": spot, "bid": best["bid"], "ask": best["ask"], "mid": best["mid"],
            "spread_pct": best["spread_pct"], "delta": best.get("delta"),
            "gamma": best.get("gamma"), "theta": best.get("theta"), "vega": best.get("vega"),
            "iv": best.get("iv"), "iv_rank": ch["iv_rank"], "ann_return": ann_return(best),
            "collateral": best["strike"] * 100.0, "earnings": earn,
            "rest_price": _half_up(best["mid"] * (1 - PROFIT_TAKE)),
            "size_n": n, "size_binding": binding, "size_note": note,
        })
    ranked, firsts = [], {}
''', '''        bests.append(_row(conn, ch, best, spot, b, db))
    ranked, firsts = [], {}
'''),
('''    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return {"id": "puts_" + uuid.uuid4().hex[:12],
''', '''    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    try:                                        # s163: display only -- never costs the list
        index = index_rows(conn, today, rejects, bests, fetch, b, db, ivr_date, tickers, stopped)
        rk = {r["ticker"]: r["rank"] for r in ranked}
        for x in index:
            x["rank"] = rk.get(x["ticker"])
    except Exception as e:
        index = [{"ticker": "INDEX", "bar_met": None, "note": "index funds not read: %s" % str(e)[:80]}]
    return {"id": "puts_" + uuid.uuid4().hex[:12],
'''),
('''            "sleeve_free": b["share"] - b["used"], "rejects": rejects,
            "candidates": ranked, "stopped": stopped}
''', '''            "sleeve_free": b["share"] - b["used"], "rejects": rejects,
            "candidates": ranked, "stopped": stopped, "index": index}
'''),
('''def build(conn, today, tickers=None, fetch=fetch_contracts, book=None, db=None):
''', '''def _row(conn, ch, best, spot, b, db):
    """One name's line from its chosen contract: sector, sizing against the limits, earnings, the 50% price."""
    from helm import sleeve as S
    from helm.bookview import _half_up
    t = ch["ticker"]
    sec, _ = S.sector_of(conn, t)
    n, binding, note = S.size("CSP", t, best["strike"] * 100.0, db=db, b=b)
    earn = ch["earn_line"]
    if earnings_in_trade(ch["upcoming"], best["expiration"]):
        earn += " -- inside the trade"
    return {
        "ticker": t, "grade": ch["grade"], "sector": sec,
        "expiry": best["expiration"], "dte": best["dte"], "strike": best["strike"],
        "spot": spot, "bid": best["bid"], "ask": best["ask"], "mid": best["mid"],
        "spread_pct": best["spread_pct"], "delta": best.get("delta"),
        "gamma": best.get("gamma"), "theta": best.get("theta"), "vega": best.get("vega"),
        "iv": best.get("iv"), "iv_rank": ch["iv_rank"], "ann_return": ann_return(best),
        "collateral": best["strike"] * 100.0, "earnings": earn,
        "rest_price": _half_up(best["mid"] * (1 - PROFIT_TAKE)),
        "size_n": n, "size_binding": binding, "size_note": note,
    }


# ---------------------------------------------------------------- the index funds (s163)
# Russ, 2026-11-20: the broad index funds are read on every run WHATEVER their IV rank and the best
# is shown beside the list -- display only, to put a month of quoted index-put prices next to the
# stocks'. Nothing here changes the screen, the rejects, the ranking or the sizing of the list.
INDEX_BUCKET = "US equity index"


def index_names(conn):
    try:
        return [r[0] for r in conn.execute(
            "SELECT ticker FROM approved_funds WHERE bucket=? ORDER BY ticker", (INDEX_BUCKET,))]
    except Exception:
        return []


def index_rows(conn, today, rejects, bests, fetch, b, db, ivr_date, tickers=None, stopped=None):
    """One entry per broad index fund. A fund already on the list is copied (never read twice); one the
    IV-rank bar left off is read now, under the same contract rules (delta, days, spread); any other
    reason it was left off is kept as its note. Reads `rejects` and `bests`, changes neither."""
    from helm import advice as A
    want = {t.upper() for t in tickers} if tickers else None
    on_list = {r["ticker"]: r for r in bests}
    out = []
    for t in index_names(conn):
        if want is not None and t not in want:
            continue
        if t in on_list:
            out.append(dict(on_list[t], bar_met=1, note=None))
            continue
        r = conn.execute("SELECT iv_rank FROM iv_history WHERE ticker=? AND date=?",
                         (t, ivr_date)).fetchone() if ivr_date else None
        ivr = r[0] if r else None
        base = {"ticker": t, "iv_rank": ivr, "bar_met": None if ivr is None else int(ivr >= IVR_MIN)}
        why = rejects.get(t) or "not on the run"
        if not (why.startswith("IV rank ") and ", below " in why):
            out.append(dict(base, note=why))        # stale or missing IV rank, grade, spread, chain not read ...
            continue
        if stopped:
            out.append(dict(base, note="not read: run stopped -- IB Gateway unreachable"))
            continue
        rows, spot, err = fetch(t)
        if err:
            out.append(dict(base, note=err if err.startswith("no expiry") else "chain not read: %s" % err))
            continue
        best, w = best_contract(rows)
        if best is None:
            out.append(dict(base, note=w))
            continue
        _ok, line, upcoming = earnings_check(True, None, None, today)
        ch = {"ticker": t, "grade": A._grade(conn, t), "iv_rank": ivr, "earn_line": line, "upcoming": upcoming}
        out.append(dict(_row(conn, ch, best, spot, b, db), bar_met=0, note=None))
    return out


def index_best(rows):
    """The index fund shown: the one with a contract and the highest yearly return, or None."""
    have = [x for x in (rows or []) if x.get("strike") is not None and x.get("ann_return") is not None]
    return max(have, key=lambda x: x["ann_return"]) if have else None


def build(conn, today, tickers=None, fetch=fetch_contracts, book=None, db=None):
'''),
('''    checked, rejects, ivr_date, n_universe = screen(conn, today, tickers)
    b = book if book is not None else S.book()
''', '''    checked, rejects, ivr_date, n_universe = screen(conn, today, tickers)
    b = book if book is not None else S.book()
'''),
# 4. record and read back
('''             "collateral", "earnings", "rest_price", "size_n", "size_binding", "size_note")
''', '''             "collateral", "earnings", "rest_price", "size_n", "size_binding", "size_note")
INDEX_COLS = ("ticker", "bar_met", "note") + CAND_COLS[1:]      # s163: put_index's columns
'''),
('''            (run["id"],) + tuple(c.get(k) for k in CAND_COLS))
    conn.commit()
''', '''            (run["id"],) + tuple(c.get(k) for k in CAND_COLS))
    for x in run.get("index") or []:             # s163
        conn.execute("INSERT INTO put_index (run_id, %s) VALUES (?, %s)" % (
            ", ".join(INDEX_COLS), ", ".join("?" * len(INDEX_COLS))),
            (run["id"],) + tuple(x.get(k) for k in INDEX_COLS))
    conn.commit()
'''),
('''        "SELECT %s FROM put_candidates WHERE run_id=? ORDER BY rank" % ", ".join(CAND_COLS), (run["id"],))]
    return run
''', '''        "SELECT %s FROM put_candidates WHERE run_id=? ORDER BY rank" % ", ".join(CAND_COLS), (run["id"],))]
    run["index"] = []                            # s163: runs before the index line have none
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='put_index'").fetchone():
        run["index"] = [dict(zip(INDEX_COLS, tuple(x))) for x in conn.execute(
            "SELECT %s FROM put_index WHERE run_id=? ORDER BY ticker" % ", ".join(INDEX_COLS), (run["id"],))]
    return run
'''),
# 5. self-test
('''    ok(_ivr_text(49.5) == "49.5" and _ivr_text(49.4) == "49" and _ivr_text(38.0) == "38",
''', '''    # s163: the index funds and the premium count
    ok(run["index"] == [] and back["index"] == [], "no broad index fund approved -> no index rows, recorded and read back")
    pc = premium_count(conn)
    ok(pc == {"as_of": "2026-10-28", "bar": 50.0, "checked": 7, "rich": 6, "no_reading": 0,
              "stocks": {"checked": 6, "rich": 5}, "funds": {"checked": 1, "rich": 1}},
       "premium count: 7 sellable names read, 6 at or above the bar (grade D left out): %s" % pc)
    for t, ivr in (("SPY", 35), ("QQQ", 61), ("DIA", 50), ("IWM", None)):
        conn.execute("INSERT INTO approved_funds VALUES (?, 'US equity index')", (t,))
        if ivr is not None:
            conn.execute("INSERT INTO iv_history VALUES (?,'2026-10-28',?)", (t, ivr))
    pc = premium_count(conn)
    ok((pc["checked"], pc["rich"], pc["no_reading"], pc["funds"]) == (10, 8, 1, {"checked": 4, "rich": 3}),
       "premium count: 50 counts as rich, 35 does not, a fund with no reading is counted apart: %s" % pc)
    chains2 = dict(chains, SPY=[{**base, "mid": 0.9, "strike": 650.0}], QQQ=[{**base, "mid": 9.0, "strike": 600.0}],
                   DIA=[{**base, "spread_pct": 12.0}])
    calls = []
    def _f(t):
        calls.append(t)
        return (chains2.get(t, []), 100.0, None)
    S.sector_of = lambda c, t: ("Tech", "test")
    try:
        run2 = build(conn, T, fetch=_f, book=dict(fake_book, used=0.0), db=tmp)
        run_old = build(conn, T, fetch=lambda t: (chains2.get(t, []), 100.0, None), book=dict(fake_book, used=0.0), db=tmp)
    finally:
        S.sector_of = _so
    ix = {x["ticker"]: x for x in run2["index"]}
    ok(sorted(ix) == ["DIA", "IWM", "QQQ", "SPY"], "one index row per broad index fund: %s" % sorted(ix))
    ok(ix["SPY"]["bar_met"] == 0 and ix["SPY"]["strike"] == 650.0 and ix["SPY"]["rank"] is None
       and run2["rejects"]["SPY"] == "IV rank 35, below 50" and "SPY" not in [c["ticker"] for c in run2["candidates"]],
       "a fund under the bar is read and shown, and stays off the list with its reason")
    ok(ix["QQQ"]["bar_met"] == 1 and ix["QQQ"]["rank"] == [c["rank"] for c in run2["candidates"] if c["ticker"] == "QQQ"][0],
       "a fund over the bar is copied from the list with its rank")
    ok(calls.count("QQQ") == 1 and calls.count("SPY") == 1, "no chain is read twice: %s" % calls)
    ok(ix["DIA"].get("strike") is None and "spread" in ix["DIA"]["note"] and ix["DIA"]["bar_met"] == 1,
       "a fund the list left off for its spread keeps that reason, no contract")
    ok(ix["IWM"].get("strike") is None and ix["IWM"]["bar_met"] is None and "no IV rank" in ix["IWM"]["note"],
       "a fund with no IV rank is not read: %s" % ix["IWM"]["note"])
    ok(index_best(run2["index"])["ticker"] == "QQQ" and index_best([ix["DIA"], ix["IWM"]]) is None,
       "the fund shown is the one with the highest yearly return; none when no contract")
    ok([(c["ticker"], c["rank"], c["size_n"]) for c in run2["candidates"]] ==
       [(c["ticker"], c["rank"], c["size_n"]) for c in run_old["candidates"]] and run2["rejects"] == run_old["rejects"]
       and (run2["n_universe"], run2["n_checked"], run2["n_found"]) == (run_old["n_universe"], run_old["n_checked"], run_old["n_found"]),
       "the list, its ranks, sizes, rejects and counts are the same on a second run")
    record(conn, run2)
    back2 = latest(conn)
    ok(back2["index"] == sorted(({k: x.get(k) for k in INDEX_COLS} for x in run2["index"]), key=lambda x: x["ticker"]),
       "index rows recorded and read back unchanged")
    S.sector_of = lambda c, t: ("Tech", "test")
    try:
        run3 = build(conn, T, tickers=["AAA"], fetch=_f, book=dict(fake_book, used=0.0), db=tmp)
        def _boom(t):
            if t == "SPY":
                raise RuntimeError("boom")
            return (chains2.get(t, []), 100.0, None)
        run4 = build(conn, T, fetch=_boom, book=dict(fake_book, used=0.0), db=tmp)
    finally:
        S.sector_of = _so
    ok(run3["index"] == [], "a partial run that names no index fund has no index rows")
    ok([c["ticker"] for c in run4["candidates"]] == [c["ticker"] for c in run2["candidates"]]
       and len(run4["index"]) == 1 and "not read" in run4["index"][0]["note"],
       "a failure reading an index fund never costs the list: %s" % run4["index"])
    ok(_ivr_text(49.5) == "49.5" and _ivr_text(49.4) == "49" and _ivr_text(38.0) == "38",
'''),
])

# ------------------------------------------------------------------ helm/advice.py
patch("helm/advice.py", [
('''    return {"schema_version": SCHEMA_VERSION, "generated_at": _now_iso(), "source": source,
            "slot": slot_of(slot_at or (snapshot or {}).get("started")) if snapshot else None,
''', '''    # s163 (Russ 2026-11-20, for the Book's daily market read): how many of the names the desk may
    # sell have rich premium by the put list's own measure. Additive; a failure never costs the file.
    try:
        from helm import puttool as P
        pconn = S._conn(db)
        try:
            premium = P.premium_count(pconn)
        finally:
            pconn.close()
        premium["meaning"] = ("of the names the desk may sell (graded A-C or an approved fund) with a reading on "
                              "as_of's morning, how many have implied volatility in the upper half of its own "
                              "past-year range (IV rank at or above bar)")
    except Exception as e:
        premium = {"as_of": None, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    return {"schema_version": SCHEMA_VERSION, "generated_at": _now_iso(), "source": source,
            "slot": slot_of(slot_at or (snapshot or {}).get("started")) if snapshot else None,
'''),
('''            "book_flags": book_flags, "limits": limits,
            "note": "Advice only, from stored readings''', '''            "book_flags": book_flags, "limits": limits, "premium_count": premium,
            "note": "Advice only, from stored readings'''),
])

# ------------------------------------------------------------------ helm/cli/puts_cmd.py
patch("helm/cli/puts_cmd.py", [
('''            t.add_row(str(c["rank"]), c["ticker"], held.get(''', '''            t.add_row(str(c["rank"]) if c.get("rank") is not None else "—", c["ticker"], held.get('''),
('''    rej = run.get("rejects") or {}
    if rej:
        def bucket(r):
''', '''    idx = run.get("index") or []                 # s163 (Russ 2026-11-20): display only
    if idx:
        from helm.puttool import index_best, IVR_MIN
        have = sorted((x for x in idx if x.get("strike") is not None), key=lambda x: -x["ann_return"])
        shown = have if show_all else have[:1]
        console.print()
        console.print("[bold]Index fund[/bold]  [dim](read whatever its IV rank -- the bar is %.0f; not part of the "
                      "ranking, # only when it also passed every rule%s)[/dim]"
                      % (IVR_MIN, "" if show_all or len(have) < 2 else "; --all for all %d" % len(have)))
        if shown:
            _table(shown)
        else:
            console.print("  no index fund had a contract inside the delta, days and spread rules")
        if show_all or why or not shown:
            for x in idx:
                if x.get("strike") is None:
                    console.print("  %-6s %s" % (x["ticker"], x.get("note") or ""))
    rej = run.get("rejects") or {}
    if rej:
        def bucket(r):
'''),
])

# ------------------------------------------------------------------ helm/todayview.py
patch("helm/todayview.py", [
('''    if not run or run.get("error"):
        return run
    own = conn is None
    try:
        conn = conn or _held_conn()
        held = {(c["ticker"], c["expiry"], c["strike"]): held_on(conn, c["ticker"], c["expiry"], c["strike"])
                for c in run["candidates"]}
''', '''    if not run or run.get("error"):
        return run
    ibest = P.index_best(run.get("index"))       # s163: the index fund shown beside the list (display only)
    shown = list(run["candidates"]) + ([ibest] if ibest else [])
    own = conn is None
    try:
        conn = conn or _held_conn()
        held = {(c["ticker"], c["expiry"], c["strike"]): held_on(conn, c["ticker"], c["expiry"], c["strike"])
                for c in shown}
'''),
('''            mconn = conn if not own else _held_conn()
            for c in run["candidates"]:
''', '''            mconn = conn if not own else _held_conn()
            for c in shown:
'''),
('''        return {"rank": c["rank"], "ticker": c["ticker"], "grade": c["grade"],
                "put": "%g P %s" % (c["strike"], _md(c["expiry"])), "dte": c["dte"], "delta": c["delta"],
''', '''        return {"rank": c["rank"] if c.get("rank") is not None else "—", "ticker": c["ticker"], "grade": c["grade"],
                "put": "%g P %s" % (c["strike"], _md(c["expiry"])), "dte": c["dte"], "delta": c["delta"],
'''),
('''            "top": [row(c) for c in cands[:P.TOP_N]], "fit": [row(c) for c in P.fitting(cands)]}
''', '''            "top": [row(c) for c in cands[:P.TOP_N]], "fit": [row(c) for c in P.fitting(cands)],
            "index": (dict(row(ibest), iv_rank=ibest.get("iv_rank"), bar=P.IVR_MIN) if ibest else None)}
'''),
])
print("done")
