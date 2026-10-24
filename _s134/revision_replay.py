"""
s134 -- replay of the HELM revision rules (decisions of 2026-10-24) on stored history.

READ-ONLY. Opens the database with ?mode=ro. Run it on a VACUUM INTO copy:
    python3 _s134/revision_replay.py PATH_TO_DB_COPY
    python3 _s134/revision_replay.py --selftest

Parts:
  A  CSP entry rules, applied one at a time (cumulative), on every closed CSP (real + paper)
  B  the $300K sleeve / 20% name / 30% sector limits walked through the REAL book in time order
  C  the 50% profit-take as a resting order, read from the stored marks (checks trimmed to close)
  D  daily candidate count under the universe rule (scan signals routed CSP)
  E  covered calls on record
Old = opened before 2026-08-11 (last regime break); new = from 2026-08-11.
P&L is gross (HELM has never stored fees).
"""
import sqlite3, sys, statistics as st, datetime as dt
from collections import defaultdict

BREAK = dt.date(2026, 8, 11)
SLEEVE, NAME_CAP, SECTOR_CAP = 300_000, 60_000, 90_000
GOOD_GRADES = ("A", "B", "C")


def d(s):
    return dt.date.fromisoformat(str(s)[:10])


# ---------------------------------------------------------------- pure rules
def in_band(x):
    dte = x["dte"]
    return x["ad"] is not None and 0.25 <= x["ad"] <= 0.30 and dte is not None and 30 <= dte <= 45


def spread_ok(x):
    return x["spr"] is None or x["spr"] < 10.0


def ivr_ok(x):
    return x["ivr"] is None or x["ivr"] >= 50


def earn_ok(x):
    return x["d2e"] is None or x["d2e"] < 0 or x["d2e"] >= 10


def walk_limits(trades, sleeve=SLEEVE, name_cap=NAME_CAP, sector_cap=SECTOR_CAP):
    """trades: dicts with open, close (date), name, sector, coll, kind ('new' or 'held').
    'held' items always stand (already open, never forced closed); a 'new' item is declined
    if it would put the sleeve, its name or its sector over the cap. Returns (taken, declined, peak)."""
    events = sorted(trades, key=lambda t: (t["open"], t.get("seq", 0)))
    book, taken, declined, peak = [], [], [], 0.0
    for t in events:
        book = [b for b in book if b["close"] > t["open"]]
        tot = sum(b["coll"] for b in book)
        nm = sum(b["coll"] for b in book if b["name"] == t["name"])
        sc = sum(b["coll"] for b in book if b["sector"] == t["sector"])
        why = []
        if tot + t["coll"] > sleeve: why.append("sleeve")
        if nm + t["coll"] > name_cap: why.append("name")
        if sc + t["coll"] > sector_cap: why.append("sector")
        if why and t.get("kind") != "held":
            declined.append(dict(t, why=why)); continue
        book.append(t); taken.append(t)
        peak = max(peak, tot + t["coll"])
    return taken, declined, peak


def resting_fill(credit, marks):
    """First mark at or under half the credit -> the resting order fills there."""
    for when, m in marks:
        if m is not None and m <= 0.5 * credit:
            return when
    return None


def selftest():
    f = 0
    def ok(c, m):
        nonlocal f
        if not c: f += 1; print("FAIL", m)
    a = dt.date(2026, 9, 1)
    T = lambda i, o, c, n, s, coll, k="new": dict(seq=i, open=a + dt.timedelta(o), close=a + dt.timedelta(c), name=n, sector=s, coll=coll, kind=k)
    tk, dc, pk = walk_limits([T(1, 0, 30, "X", "S1", 50_000), T(2, 1, 30, "X", "S1", 20_000)])
    ok(len(dc) == 1 and dc[0]["why"] == ["name"], "name cap declines the second X")
    tk, dc, pk = walk_limits([T(1, 0, 30, "X", "S1", 50_000), T(2, 1, 30, "Y", "S1", 45_000)])
    ok(len(dc) == 1 and dc[0]["why"] == ["sector"], "sector cap at 95k")
    tk, dc, pk = walk_limits([T(1, 0, 30, "X", "S1", 50_000), T(2, 31, 60, "X", "S1", 50_000)])
    ok(not dc, "a closed position frees its room")
    tk, dc, pk = walk_limits([T(i, 0, 30, "N%d" % i, "S%d" % i, 60_000) for i in range(5)] + [T(9, 1, 30, "Z", "SZ", 1)])
    ok(len(dc) == 1 and dc[0]["name"] == "Z" and "sleeve" in dc[0]["why"], "sleeve full at 300k declines $1 more")
    tk, dc, pk = walk_limits([T(1, 0, 30, "X", "S1", 70_000, "held")])
    ok(not dc and pk == 70_000, "held positions are never declined")
    ok(resting_fill(2.0, [(1, 1.5), (2, 1.0)]) == 2, "fills at exactly half")
    ok(resting_fill(2.0, [(1, 1.5), (2, None)]) is None, "no fill without a mark at half")
    x = dict(ad=0.30, dte=45, spr=10.0, ivr=50, d2e=9)
    ok(in_band(x) and not spread_ok(x) and ivr_ok(x) and not earn_ok(x), "edges: 0.30/45 in, 10.0% out, IVR 50 in, 9 days out")
    print("selftest:", "PASS" if not f else "%d FAIL" % f)
    return f


# ---------------------------------------------------------------- data
def load(c):
    c.row_factory = sqlite3.Row
    grades = defaultdict(list)
    for r in c.execute("select ticker,date,grade from ownership_quality order by date"):
        grades[r["ticker"]].append((d(r["date"]), r["grade"]))
    sector = {r[0]: r[1] for r in c.execute("select ticker,sector from watchlist")}
    rows = c.execute("""select p.id,p.ticker,p.book,p.opened_at,p.closed_at,p.realized_pnl pnl,p.total_contracts n,
        p.entry_dte, e.delta,e.dte edte,e.iv_rank ivr,e.days_to_earnings d2e,e.bid_ask_spread_pct spr,
        (select strike from legs l where l.position_id=p.id and l.leg_role='SHORT_PUT' order by l.created_at limit 1) k,
        (select open_price from legs l where l.position_id=p.id and l.leg_role='SHORT_PUT' order by l.created_at limit 1) credit,
        (select contracts from legs l where l.position_id=p.id and l.leg_role='SHORT_PUT' order by l.created_at limit 1) lc
        from positions p left join entry_snapshots e on e.position_id=p.id
        where p.strategy='CSP' and p.status='CLOSED' and coalesce(p.exit_reason,'')<>'VOIDED'""").fetchall()
    D = []
    for r in rows:
        x = dict(r)
        x["open"], x["close"] = d(x["opened_at"]), d(x["closed_at"])
        x["days"] = max((x["close"] - x["open"]).days, 1)
        x["old"] = x["open"] < BREAK
        n = x["lc"] or x["n"] or 1
        x["coll"] = (x["k"] or 0) * 100 * n
        x["ad"] = abs(x["delta"]) if x["delta"] is not None else None
        x["dte"] = x["edte"] if x["edte"] is not None else x["entry_dte"]
        g = grades.get(x["ticker"], [])
        asof = [gr for (gd, gr) in g if gd <= x["open"]]
        x["grade_asof"] = asof[-1] if asof else None
        x["grade_first"] = g[0][1] if g else None
        x["grade_any"] = x["grade_asof"] or x["grade_first"]
        x["sector"] = sector.get(x["ticker"]) or ("no sector: " + x["ticker"])
        D.append(x)
    return D, grades, sector


def line(lbl, sub):
    sub = [x for x in sub if x["coll"]]
    if not sub:
        print("%-44s n=  0" % lbl); return
    pnl = sum(x["pnl"] for x in sub)
    cy = sum(x["coll"] * x["days"] / 365 for x in sub)
    srt = sorted(sub, key=lambda x: x["pnl"])
    print("%-44s n=%3d win=%3.0f%% pnl=$%8.0f med=$%6.0f ann=%5.1f%% ann-excl-worst5=%5.1f%%" % (
        lbl, len(sub), 100 * sum(x["pnl"] > 0 for x in sub) / len(sub), pnl, st.median([x["pnl"] for x in sub]),
        100 * pnl / cy, 100 * sum(x["pnl"] for x in srt[5:]) / cy if len(sub) > 5 else float("nan")))


def split(lbl, sub):
    line(lbl, sub)
    line("    old", [x for x in sub if x["old"]])
    line("    new", [x for x in sub if not x["old"]])


def main(path):
    c = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    D, grades, sector = load(c)
    print("loaded", len(D), "closed CSPs (VOIDED excluded); grades dated", min(g[0][0] for g in grades.values()), "->",
          max(g[-1][0] for g in grades.values()))
    print("\n== A. CSP entry rules, cumulative (real + paper)")
    stages = [("all", lambda x: True), ("+ delta .25-.30, DTE 30-45", in_band), ("+ spread < 10%", spread_ok),
              ("+ IV rank >= 50", ivr_ok), ("+ earnings veto (>=10d)", earn_ok)]
    cur = D
    for lbl, f in stages:
        cur = [x for x in cur if f(x)]; split(lbl, cur)
    base = cur
    split("+ grade A-C as of entry (strict)", [x for x in base if x["grade_asof"] in GOOD_GRADES])
    split("+ grade A-C, earliest grade if none yet", [x for x in base if x["grade_any"] in GOOD_GRADES])
    split("  (excluded by that: D/F)", [x for x in base if x["grade_any"] in ("D", "F")])
    split("  (excluded by that: never graded)", [x for x in base if x["grade_any"] is None])
    print("  strict universe, from 7/13 only (grades exist):")
    split("  band..veto, opened >= 7/13", [x for x in base if x["open"] >= dt.date(2026, 7, 13)])
    split("  ... and grade A-C as of entry", [x for x in base if x["open"] >= dt.date(2026, 7, 13) and x["grade_asof"] in GOOD_GRADES])
    for b in ("REAL", "PAPER"):
        line("final rule set, %s" % b, [x for x in base if x["grade_any"] in GOOD_GRADES and x["book"] == b])
    print("  worst 5 closed CSPs and the first rule that would have stopped each:")
    for x in sorted(D, key=lambda x: x["pnl"])[:5]:
        why = next((l for l, f in stages[1:] if not f(x)), None) or ("grade %s" % x["grade_any"] if x["grade_any"] not in GOOD_GRADES else "none")
        print("   %-5s %s %s $%.0f delta=%s dte=%s grade=%s -> %s" % (x["ticker"], x["book"], x["open"], x["pnl"], x["ad"], x["dte"], x["grade_any"], why))

    print("\n== B. Sleeve $300K / name $60K / sector $90K, REAL book in time order")
    pos = c.execute("""select p.id,p.ticker,p.strategy,p.opened_at,p.closed_at,p.realized_pnl from positions p
        where p.book='REAL' and p.strategy in ('CSP','DIAGONAL','PMCC') and p.status in ('OPEN','CLOSED')""").fetchall()
    T = []
    for i, p in enumerate(pos):
        if p["strategy"] == "CSP":
            coll = c.execute("select coalesce(sum(strike*100*contracts),0) from legs where position_id=? and leg_role='SHORT_PUT'", (p["id"],)).fetchone()[0]
        else:
            coll = c.execute("select coalesce(sum(open_price*100*contracts),0) from legs where position_id=? and direction='LONG'", (p["id"],)).fetchone()[0]
        T.append(dict(seq=i, id=p["id"], open=d(p["opened_at"]), close=d(p["closed_at"]) if p["closed_at"] else dt.date(2099, 1, 1),
                      name=p["ticker"], sector=sector.get(p["ticker"]) or ("no sector: " + p["ticker"]), coll=coll, pnl=p["realized_pnl"],
                      strategy=p["strategy"], kind="new"))
    tk, dc, pk = walk_limits(T)
    print("  real CSP + diagonal positions: %d; declined: %d; peak sleeve used $%.0f" % (len(T), len(dc), pk))
    for why in ("sleeve", "name", "sector"):
        sub = [t for t in dc if why in t["why"]]
        print("   declined for %-6s %3d  (closed P&L of those: $%.0f)" % (why, len(sub), sum(t["pnl"] or 0 for t in sub)))
    for half, f in (("old", lambda t: t["open"] < BREAK), ("new", lambda t: t["open"] >= BREAK)):
        print("   %s: positions %d, declined %d, declined closed P&L $%.0f" % (half, sum(f(t) for t in T), sum(f(t) for t in dc), sum(t["pnl"] or 0 for t in dc if f(t))))
    print("   names with no sector on the watchlist (each counted as its own sector):", sorted({t["name"] for t in T if t["sector"].startswith("no sector")}))
    print("   declined, closed: n=%d P&L $%.0f; still open: %d" % (sum(t["pnl"] is not None for t in dc), sum(t["pnl"] or 0 for t in dc), sum(t["pnl"] is None for t in dc)))
    for t in sorted(dc, key=lambda t: t["pnl"] or 0)[:6]:
        print("    %s %s %s $%.0f coll $%.0f %s" % (t["open"], t["name"], t["strategy"], t["pnl"] or 0, t["coll"], "+".join(t["why"])))
    # what the actual book looked like: peak exposures
    days = sorted({t["open"] for t in T})
    pk_tot = max((sum(t["coll"] for t in T if t["open"] <= dd < t["close"]), dd) for dd in days)
    pk_nm = max((sum(t["coll"] for t in T if t["open"] <= dd < t["close"] and t["name"] == n), dd, n) for dd in days for n in {t["name"] for t in T})
    pk_sc = max((sum(t["coll"] for t in T if t["open"] <= dd < t["close"] and t["sector"] == s), dd, s) for dd in days for s in {t["sector"] for t in T})
    print("  actual peaks: total $%.0f (%s); name $%.0f (%s %s); sector $%.0f (%s %s)" % (pk_tot[0], pk_tot[1], pk_nm[0], pk_nm[2], pk_nm[1], pk_sc[0], pk_sc[2], pk_sc[1]))

    print("\n== C. 50% profit-take as a resting order (mid marks at the stored checks, trimmed to close)")
    better = worse = same = nofill = 0; gain = []
    for x in D:
        if not x["credit"]:
            continue
        marks = [(r[0], r[1]) for r in c.execute(
            "select checked_at,current_price from checks where position_id=? and checked_at<=? and data_quality='GOOD' order by checked_at",
            (x["id"], x["closed_at"]))]
        when = resting_fill(x["credit"], marks)
        if when is None:
            nofill += 1; continue
        n = x["lc"] or x["n"] or 1
        alt = 0.5 * x["credit"] * 100 * n
        diff = alt - x["pnl"]
        if abs(diff) < 1: same += 1
        elif diff > 0: better += 1; gain.append(diff)
        else: worse += 1; gain.append(diff)
        x["rest_alt"] = alt
        x["rest_same_check"] = (str(when)[:10] == str(x["closed_at"])[:10])
        x["rest_diff"] = diff
    print("  CSPs whose mark reached half the credit before close: %d; resting order better %d, worse %d, same %d; never reached: %d" % (better + worse + same, better, worse, same, nofill))
    print("  net change $%.0f; sum of gains $%.0f; sum of give-ups $%.0f" % (sum(gain), sum(g for g in gain if g > 0), sum(g for g in gain if g < 0)))
    gap = [x for x in D if "rest_alt" in x and x["rest_same_check"]]
    held = [x for x in D if "rest_alt" in x and not x["rest_same_check"]]
    print("   closed the same day the mark first showed half (difference is only how far under half that mark was): n=%d change $%.0f" % (len(gap), sum(x["rest_diff"] for x in gap)))
    print("   held past the day it first showed half: n=%d change $%.0f (resting better %d $%.0f, worse %d $%.0f)" % (
        len(held), sum(x["rest_diff"] for x in held), sum(x["rest_diff"] > 0 for x in held), sum(x["rest_diff"] for x in held if x["rest_diff"] > 0),
        sum(x["rest_diff"] < 0 for x in held), sum(x["rest_diff"] for x in held if x["rest_diff"] < 0)))
    for x in sorted(held, key=lambda x: -x["rest_diff"])[:5]:
        print("    %s %s %s actual $%.0f, resting $%.0f" % (x["ticker"], x["book"], x["open"], x["pnl"], x["rest_alt"]))
    for b in ("REAL", "PAPER"):
        sub = [x for x in D if x["book"] == b and "rest_alt" in x]
        print("   %s: n=%d change $%.0f" % (b, len(sub), sum(x["rest_alt"] - x["pnl"] for x in sub)))

    print("\n== D. Daily CSP candidates under the universe rule (scan signals routed CSP, one per name per day)")
    sig = c.execute("""select date(generated_at) dd,ticker,days_to_earnings from signals where top_strategy='CSP'
        group by date(generated_at),ticker order by dd""").fetchall()
    per = defaultdict(lambda: [0, 0, 0])
    for s in sig:
        dd = d(s["dd"]); g = grades.get(s["ticker"], [])
        asof = [gr for (gd, gr) in g if gd <= dd]
        latest = g[-1][1] if g else None
        e_ok = s["days_to_earnings"] is None or s["days_to_earnings"] < 0 or s["days_to_earnings"] >= 10
        per[dd][0] += 1
        per[dd][1] += bool(e_ok and asof and asof[-1] in GOOD_GRADES)
        per[dd][2] += bool(e_ok and latest in GOOD_GRADES)
    last = sorted(per)[-15:]
    print("  date        routed  A-C as of day  A-C on latest grade")
    for dd in last:
        print("  %s  %5d  %12d  %18d" % (dd, *per[dd]))
    print("  last 15 scan days, median: routed %.0f, A-C as of day %.0f, A-C latest %.0f" % tuple(st.median([per[dd][i] for dd in last]) for i in range(3)))

    print("\n== E. Covered calls on record")
    for r in c.execute("select ticker,book,opened_at,closed_at,realized_pnl,exit_reason from positions where strategy='COVERED_CALL'"):
        print("  ", tuple(r))
    print("  stock_positions rows:", c.execute("select count(*) from stock_positions").fetchone()[0])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    if len(sys.argv) != 2:
        print(__doc__); sys.exit(2)
    if selftest():
        sys.exit(1)
    main(sys.argv[1])
