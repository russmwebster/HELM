#!/usr/bin/env python3
"""W165 - the Exit Discipline Scorecard (spec v0.2, 2026-09-06, s114).

Answers one question with numbers: ARE RUSS'S EXIT DECISIONS IMPROVING THE
REAL BOOK'S PERFORMANCE OVER TIME? Seven metrics by close-month cohort.

READ-ONLY. Opens the database mode=ro and writes nothing.

    python3 tools/exit_discipline.py                  # live table, REAL book
    python3 tools/exit_discipline.py --book PAPER
    python3 tools/exit_discipline.py --as-of DATE     # positions closed on/before DATE
    python3 tools/exit_discipline.py --json
    python3 tools/exit_discipline.py --selftest       # re-run the frozen universe, diff
    python3 tools/exit_discipline.py --freeze         # print a new FIXTURE (method change only)

THE METRICS (definitions frozen at v0.2; a change needs v0.3 and a logged amendment):

  M7 EXIT ALPHA - reported FIRST, because it is the only one that answers the
     question. For every closed position with a usable GOOD journal, replay the
     paper book's frozen exit doctrine over the SAME marks and take the mark at the
     first check where it would have fired. alpha = realized - that mark. Same
     position, same tape: the tape cancels, the decision remains. Reported four
     ways so no single row can manufacture it: sum, median, share of positions
     where the trader beat the rule, and the sum with the 5 largest |alpha| dropped.
     Also split by WHICH reference rule fired. A position the rule never fired on
     before the actual close is 'held' - alpha 0 by construction, and counted.
       credit (CSP, CC, spreads, condors): target 50% CSP/CC, 25% others; else 21 DTE.
       long (LONG_CALL, LONG_PUT, BEAR_PUT_SPREAD, DIAGONAL*, PMCC): v3 -
         stop <= -50%  ->  give-back (hwm - pct >= 20 pts, hwm > -50)  ->  7 DTE
         ->  21 DTE if not positive.  Precedence as long_exit.py.
  M1 EXIT LATENCY - trading days from the FIRST exit-relevant signal to the close,
     BY SIGNAL TYPE (target touch / thesis-break flag / 21-DTE). A single median
     lets fast profit-taking hide slow loss-cutting (s114: overall median 4d,
     thesis-break median 8-9d). thesis_broken exists only on LONG_CALL journals.
     Positions with no signal before the close are 'pure discretion' (counted, not timed).
  M2 HOLD ASYMMETRY - mean days held (losers) / mean days held (winners). 1.0 is
     symmetric; the rules-run paper book sits ~0.93.
  M3 LABELLED SHARE - closes carrying an exit_reason other than NULL or 'manual'
     ('manual' was the hardcoded default before s103, W105). HELM cannot tell a
     label written at close from one backfilled - exit_reason has no timestamp -
     so this is 'labelled at all', and says so.
  M4 DRIFT AFTER SIGNAL - realized - mark at first signal, summed. The dollar price
     of latency. Journal marks are sampled 3x/day: a LOWER BOUND, every run.
  M5 CAPTURE - sum(realized) / sum(best-ever mark) over ever-green positions.
     Outcome check, tape-dependent, reported last. >100% is possible and means the
     journal missed the true peak - the lower-bound caveat showing up as a number.
  M6 FLAG DISPOSITIONS - yes / no / silent per fired flag. NO DATA SOURCE until W158
     ships a disposition log; until then every flag reads 'silent' and the column
     is printed so the gap is visible rather than omitted.

COHORTS: close month. A cohort under 8 closes is merged into the previous one (or
the next, if it is the first) and marked '+'. A trailing-3-month row follows.

CALENDAR: latency uses exchange_calendars XNYS when importable, else weekdays, and
the output names which. --selftest and --freeze ALWAYS use weekdays so the fixture
is environment-independent (the VM that built this has no calendar package).

Verified both ways at ship (s114): PASS as frozen; the give-back band perturbed
20 -> 25 points drifted 11 keys; PASS on restore.
"""
import argparse, json, os, sqlite3, statistics, sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("HELM_DB") or (ROOT / "data" / "helm.db"))

LONG = {"LONG_CALL", "LONG_PUT", "BEAR_PUT_SPREAD", "DIAGONAL", "DIAGONAL_PUT", "PMCC"}
TARGET_PCT = {"CSP": 50.0, "COVERED_CALL": 50.0}
TARGET_DEFAULT = 25.0
DTE_MANAGE = 21
STOP_PCT = -50.0
GIVE_BACK_PTS = 20.0
HARD_CLOSE_DTE = 7
MIN_COHORT = 8
DROP_TOP = 5

FIXTURE_AS_OF = "2026-09-04"
FIXTURE_BOOK = "REAL"
# Frozen 2026-09-06 (s114) by --freeze over the REAL book closed on/before
# 2026-09-04, weekday calendar. Do not hand-edit. Regenerate with --freeze only
# if the METHOD deliberately changes (that is a v0.3), and say so in the commit.
# A --selftest failure means THIS CODE moved, not the book.
FIXTURE = {
 "2026-05+06.m1.discretion": 19,
 "2026-05+06.m1.dte.0": 2,
 "2026-05+06.m1.dte.1": 0.5,
 "2026-05+06.m1.dte.2": 1,
 "2026-05+06.m1.target.0": 10,
 "2026-05+06.m1.target.1": 0.5,
 "2026-05+06.m1.target.2": 14,
 "2026-05+06.m1.thesis.0": 0,
 "2026-05+06.m1.thesis.1": None,
 "2026-05+06.m1.thesis.2": None,
 "2026-05+06.m2": 1.27,
 "2026-05+06.m3.pct": 16.1,
 "2026-05+06.m4.sum": -10230,
 "2026-05+06.m5.pct": 25.2,
 "2026-05+06.m6.no": 0,
 "2026-05+06.m6.silent": 0,
 "2026-05+06.m6.yes": 0,
 "2026-05+06.m7.beat": 5,
 "2026-05+06.m7.by.21DTE.0": 2,
 "2026-05+06.m7.by.21DTE.1": -3050,
 "2026-05+06.m7.by.7DTE.0": 0,
 "2026-05+06.m7.by.7DTE.1": 0,
 "2026-05+06.m7.by.GIVE_BACK.0": 2,
 "2026-05+06.m7.by.GIVE_BACK.1": 761,
 "2026-05+06.m7.by.STOP.0": 0,
 "2026-05+06.m7.by.STOP.1": 0,
 "2026-05+06.m7.by.TARGET.0": 6,
 "2026-05+06.m7.by.TARGET.1": -2677,
 "2026-05+06.m7.held": 10,
 "2026-05+06.m7.median": 0,
 "2026-05+06.m7.n": 20,
 "2026-05+06.m7.sum": -4966,
 "2026-05+06.m7.sum_drop_top5": 146,
 "2026-05+06.n": 31,
 "2026-07.m1.discretion": 1,
 "2026-07.m1.dte.0": 12,
 "2026-07.m1.dte.1": 10.5,
 "2026-07.m1.dte.2": 11,
 "2026-07.m1.target.0": 14,
 "2026-07.m1.target.1": 0.0,
 "2026-07.m1.target.2": 31,
 "2026-07.m1.thesis.0": 0,
 "2026-07.m1.thesis.1": None,
 "2026-07.m1.thesis.2": None,
 "2026-07.m2": 2.61,
 "2026-07.m3.pct": 0.0,
 "2026-07.m4.sum": -13061,
 "2026-07.m5.pct": -94.2,
 "2026-07.m6.no": 0,
 "2026-07.m6.silent": 0,
 "2026-07.m6.yes": 0,
 "2026-07.m7.beat": 13,
 "2026-07.m7.by.21DTE.0": 12,
 "2026-07.m7.by.21DTE.1": -11352,
 "2026-07.m7.by.7DTE.0": 0,
 "2026-07.m7.by.7DTE.1": 0,
 "2026-07.m7.by.GIVE_BACK.0": 1,
 "2026-07.m7.by.GIVE_BACK.1": 1716,
 "2026-07.m7.by.STOP.0": 0,
 "2026-07.m7.by.STOP.1": 0,
 "2026-07.m7.by.TARGET.0": 11,
 "2026-07.m7.by.TARGET.1": -3357,
 "2026-07.m7.held": 2,
 "2026-07.m7.median": 6,
 "2026-07.m7.n": 26,
 "2026-07.m7.sum": -12993,
 "2026-07.m7.sum_drop_top5": 4747,
 "2026-07.n": 27,
 "2026-08.m1.discretion": 7,
 "2026-08.m1.dte.0": 10,
 "2026-08.m1.dte.1": 4.0,
 "2026-08.m1.dte.2": 15,
 "2026-08.m1.target.0": 15,
 "2026-08.m1.target.1": 1,
 "2026-08.m1.target.2": 23,
 "2026-08.m1.thesis.0": 6,
 "2026-08.m1.thesis.1": 7.5,
 "2026-08.m1.thesis.2": 16,
 "2026-08.m2": 2.29,
 "2026-08.m3.pct": 68.4,
 "2026-08.m4.sum": 2908,
 "2026-08.m5.pct": 20.8,
 "2026-08.m6.no": 0,
 "2026-08.m6.silent": 6,
 "2026-08.m6.yes": 0,
 "2026-08.m7.beat": 18,
 "2026-08.m7.by.21DTE.0": 9,
 "2026-08.m7.by.21DTE.1": 9716,
 "2026-08.m7.by.7DTE.0": 0,
 "2026-08.m7.by.7DTE.1": 0,
 "2026-08.m7.by.GIVE_BACK.0": 10,
 "2026-08.m7.by.GIVE_BACK.1": -17797,
 "2026-08.m7.by.STOP.0": 0,
 "2026-08.m7.by.STOP.1": 0,
 "2026-08.m7.by.TARGET.0": 10,
 "2026-08.m7.by.TARGET.1": 943,
 "2026-08.m7.held": 8,
 "2026-08.m7.median": 0,
 "2026-08.m7.n": 37,
 "2026-08.m7.sum": -7138,
 "2026-08.m7.sum_drop_top5": -4226,
 "2026-08.n": 38,
 "2026-09.m1.discretion": 0,
 "2026-09.m1.dte.0": 3,
 "2026-09.m1.dte.1": 2,
 "2026-09.m1.dte.2": 5,
 "2026-09.m1.target.0": 7,
 "2026-09.m1.target.1": 5,
 "2026-09.m1.target.2": 20,
 "2026-09.m1.thesis.0": 3,
 "2026-09.m1.thesis.1": 9,
 "2026-09.m1.thesis.2": 12,
 "2026-09.m2": 1.42,
 "2026-09.m3.pct": 100.0,
 "2026-09.m4.sum": -2904,
 "2026-09.m5.pct": -5.4,
 "2026-09.m6.no": 0,
 "2026-09.m6.silent": 3,
 "2026-09.m6.yes": 0,
 "2026-09.m7.beat": 5,
 "2026-09.m7.by.21DTE.0": 3,
 "2026-09.m7.by.21DTE.1": 521,
 "2026-09.m7.by.7DTE.0": 0,
 "2026-09.m7.by.7DTE.1": 0,
 "2026-09.m7.by.GIVE_BACK.0": 4,
 "2026-09.m7.by.GIVE_BACK.1": -3190,
 "2026-09.m7.by.STOP.0": 0,
 "2026-09.m7.by.STOP.1": 0,
 "2026-09.m7.by.TARGET.0": 6,
 "2026-09.m7.by.TARGET.1": 228,
 "2026-09.m7.held": 0,
 "2026-09.m7.median": -59,
 "2026-09.m7.n": 13,
 "2026-09.m7.sum": -2441,
 "2026-09.m7.sum_drop_top5": -67,
 "2026-09.n": 13,
 "as_of": "2026-09-04",
 "book": "REAL",
 "positions": 109,
 "trailing-3m.m1.discretion": 8,
 "trailing-3m.m1.dte.0": 25,
 "trailing-3m.m1.dte.1": 5,
 "trailing-3m.m1.dte.2": 15,
 "trailing-3m.m1.target.0": 36,
 "trailing-3m.m1.target.1": 1.0,
 "trailing-3m.m1.target.2": 31,
 "trailing-3m.m1.thesis.0": 9,
 "trailing-3m.m1.thesis.1": 8,
 "trailing-3m.m1.thesis.2": 16,
 "trailing-3m.m2": 2.19,
 "trailing-3m.m3.pct": 50.0,
 "trailing-3m.m4.sum": -13057,
 "trailing-3m.m5.pct": -20.1,
 "trailing-3m.m6.no": 0,
 "trailing-3m.m6.silent": 9,
 "trailing-3m.m6.yes": 0,
 "trailing-3m.m7.beat": 36,
 "trailing-3m.m7.by.21DTE.0": 24,
 "trailing-3m.m7.by.21DTE.1": -1115,
 "trailing-3m.m7.by.7DTE.0": 0,
 "trailing-3m.m7.by.7DTE.1": 0,
 "trailing-3m.m7.by.GIVE_BACK.0": 15,
 "trailing-3m.m7.by.GIVE_BACK.1": -19271,
 "trailing-3m.m7.by.STOP.0": 0,
 "trailing-3m.m7.by.STOP.1": 0,
 "trailing-3m.m7.by.TARGET.0": 27,
 "trailing-3m.m7.by.TARGET.1": -2186,
 "trailing-3m.m7.held": 10,
 "trailing-3m.m7.median": 0,
 "trailing-3m.m7.n": 76,
 "trailing-3m.m7.sum": -22572,
 "trailing-3m.m7.sum_drop_top5": -8900,
 "trailing-3m.n": 78
}


# ----------------------------------------------------------------------- calendar
def _tdays_weekday(a, b):
    n, x = 0, a
    while x < b:
        x += timedelta(1)
        if x.weekday() < 5:
            n += 1
    return n


def make_tdays(force_weekday=False):
    if not force_weekday:
        try:
            import exchange_calendars as xc
            import pandas as pd
            cal = xc.get_calendar("XNYS")
            def f(a, b):
                if b <= a:
                    return 0
                return len(cal.sessions_in_range(pd.Timestamp(a), pd.Timestamp(b))) - (1 if cal.is_session(pd.Timestamp(a)) else 0)
            return f, "XNYS"
        except Exception:
            pass
    return _tdays_weekday, "weekday-approx"


# ------------------------------------------------------------------------- data
def load(db, book, as_of):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    c.row_factory = sqlite3.Row
    where = "book=? AND status='CLOSED'"
    args = [book]
    if as_of:
        where += " AND substr(closed_at,1,10)<=?"
        args.append(as_of)
    pos = [dict(r) for r in c.execute(
        "SELECT id,strategy,exit_reason,realized_pnl,opened_at,closed_at FROM positions WHERE " + where, args)]
    disp = dispositions(c)
    out = []
    for p in pos:
        chk = [dict(r) for r in c.execute(
            "SELECT checked_at,pnl_unrealized u,pnl_pct pct,dte_now dte,thesis_broken tb FROM checks "
            "WHERE position_id=? AND data_quality='GOOD' AND pnl_unrealized IS NOT NULL "
            "ORDER BY checked_at", (p["id"],))]
        p["checks"] = chk
        p["disp"] = disp.get(p["id"], {})
        out.append(p)
    c.close()
    return out


def dispositions(c):
    """W158's exit_flags log, when it exists. {position_id: {kind: 'ACTED'|'KEEP'|None}}.

    Absent table -> {} -> every flag reads 'silent', which is what this tool
    printed before W158 shipped. A cohort that closed before the log existed
    stays silent for ever, correctly: nothing surfaced those flags at the time.
    """
    out = {}
    try:
        for pid, kind, d in c.execute(
                "SELECT position_id, kind, disposition FROM exit_flags"):
            out.setdefault(pid, {})[kind] = d
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------- per-position
def evaluate(p, tdays):
    s = p["strategy"]
    chk = p["checks"]
    closed = date.fromisoformat(p["closed_at"][:10])
    opened = date.fromisoformat(p["opened_at"][:10])
    pnl = p["realized_pnl"] or 0.0
    tgt = TARGET_PCT.get(s, TARGET_DEFAULT)
    r = {"id": p["id"], "strategy": s, "month": p["closed_at"][:7], "pnl": pnl,
         "held": (closed - opened).days,
         "labelled": bool(p["exit_reason"]) and p["exit_reason"] != "manual",
         "journal": len(chk) >= 2, "disp": p.get("disp") or {}}
    # peak (M5)
    peaks = [c["u"] for c in chk]
    r["peak"] = max(peaks) if peaks else None
    # first signal (M1, M4)
    sig = None
    for c in chk:
        kind = None
        if c["tb"] == 1:
            kind = "thesis"
        elif c["pct"] is not None and c["pct"] >= tgt:
            kind = "target"
        elif c["dte"] is not None and c["dte"] <= DTE_MANAGE:
            kind = "dte"
        if kind:
            sig = (date.fromisoformat(c["checked_at"][:10]), kind, c["u"])
            break
    if sig:
        r["sig_kind"] = sig[1]
        r["latency"] = tdays(sig[0], closed)
        r["drift"] = pnl - (sig[2] or 0.0)
    else:
        r["sig_kind"] = None
    # counterfactual (M7)
    r["ref"] = "held"
    r["alpha"] = 0.0
    if r["journal"]:
        hwm = -1e9
        for c in chk:
            pct, dte = c["pct"], (c["dte"] if c["dte"] is not None else 99)
            if pct is None:
                continue
            why = None
            if s in LONG:
                hwm = max(hwm, pct)
                if pct <= STOP_PCT:
                    why = "STOP"
                elif hwm - pct >= GIVE_BACK_PTS and hwm > STOP_PCT:
                    why = "GIVE_BACK"
                elif dte <= HARD_CLOSE_DTE:
                    why = "7DTE"
                elif dte <= DTE_MANAGE and pct <= 0:
                    why = "21DTE"
            else:
                if pct >= tgt:
                    why = "TARGET"
                elif dte <= DTE_MANAGE:
                    why = "21DTE"
            if why:
                r["ref"] = why
                r["alpha"] = pnl - (c["u"] or 0.0)
                break
    return r


# --------------------------------------------------------------------------- cohorts
def cohort(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["month"]].append(r)
    months = sorted(by)
    merged, labels = [], []
    for m in months:
        if merged and len(by[m]) < MIN_COHORT:
            merged[-1].extend(by[m]); labels[-1] += "+"
        elif merged and len(merged[-1]) < MIN_COHORT:
            merged[-1].extend(by[m]); labels[-1] = labels[-1] + "+" + m[5:]
        else:
            merged.append(list(by[m])); labels.append(m)
    out = [(l, g) for l, g in zip(labels, merged)]
    if len(months) >= 2:
        t3 = [r for m in months[-3:] for r in by[m]]
        out.append(("trailing-3m", t3))
    return out


def summarize(label, g):
    n = len(g)
    d = {"cohort": label, "n": n}
    # M7
    a = [r["alpha"] for r in g if r["journal"]]
    d["m7.n"] = len(a)
    d["m7.sum"] = round(sum(a))
    d["m7.median"] = round(statistics.median(a)) if a else None
    d["m7.beat"] = sum(1 for x in a if x > 0)
    d["m7.held"] = sum(1 for r in g if r["journal"] and r["ref"] == "held")
    drop = sorted(a, key=abs, reverse=True)[DROP_TOP:]
    d["m7.sum_drop_top%d" % DROP_TOP] = round(sum(drop))
    for k in ("TARGET", "21DTE", "STOP", "GIVE_BACK", "7DTE"):
        v = [r["alpha"] for r in g if r["ref"] == k]
        d["m7.by.%s" % k] = (len(v), round(sum(v)))
    # M1
    for k in ("target", "thesis", "dte"):
        v = [r["latency"] for r in g if r["sig_kind"] == k]
        d["m1.%s" % k] = (len(v), statistics.median(v), max(v)) if v else (0, None, None)
    d["m1.discretion"] = sum(1 for r in g if r["sig_kind"] is None)
    # M2
    w = [r["held"] for r in g if r["pnl"] > 0]
    l = [r["held"] for r in g if r["pnl"] <= 0]
    d["m2"] = round((sum(l) / len(l)) / (sum(w) / len(w)), 2) if w and l else None
    # M3
    d["m3.pct"] = round(100.0 * sum(1 for r in g if r["labelled"]) / n, 1)
    # M4
    d["m4.sum"] = round(sum(r["drift"] for r in g if r["sig_kind"]))
    # M5
    gg = [r for r in g if r["peak"] and r["peak"] > 0]
    d["m5.pct"] = round(100.0 * sum(r["pnl"] for r in gg) / sum(r["peak"] for r in gg), 1) if gg else None
    # M6 - W158's exit_flags log is the source. The DENOMINATOR is unchanged:
    # the thesis flags M1 already detects, so this column keeps meaning the
    # same thing it did before the log existed. (The log itself records all
    # three kinds; M6 stays thesis-only until the spec says otherwise.)
    m6 = {"yes": 0, "no": 0, "silent": 0}
    for r in g:
        if r["sig_kind"] != "thesis":
            continue
        dd = (r.get("disp") or {}).get("thesis")
        m6["yes" if dd == "ACTED" else "no" if dd == "KEEP" else "silent"] += 1
    d["m6"] = m6
    return d


def run(db, book, as_of, force_weekday=False):
    tdays, calname = make_tdays(force_weekday)
    rows = [evaluate(p, tdays) for p in load(db, book, as_of)]
    res = {"book": book, "as_of": as_of, "calendar": calname, "positions": len(rows),
           "cohorts": [summarize(l, g) for l, g in cohort(rows)]}
    return res


# --------------------------------------------------------------------------- output
def fmt_money(x):
    return "—" if x is None else format(x, "+,.0f")


def print_table(res):
    print("EXIT DISCIPLINE SCORECARD v0.2 — book %s — as of %s — calendar %s — %d closed positions"
          % (res["book"], res["as_of"] or "latest", res["calendar"], res["positions"]))
    if not any(d["m6"]["yes"] or d["m6"]["no"] for d in res["cohorts"]):
        print("M6 (flag dispositions): no flag on this book has been dispositioned yet — "
              "W158's log starts empty, so every flag still reads 'silent'.")
    print()
    hdr = "%-14s %4s | %10s %8s %6s %5s %10s | %-9s %-9s %-9s %4s | %5s | %5s | %9s | %7s | %s" % (
        "cohort", "n", "M7 alpha", "median", "beat", "held", "drop-top5",
        "M1 target", "M1 thesis", "M1 dte", "disc", "M2", "M3%", "M4 drift", "M5%", "M6 y/n/silent")
    print(hdr); print("-" * len(hdr))
    for d in res["cohorts"]:
        def m1(k):
            n, med, mx = d["m1.%s" % k]
            return "—" if not n else "%g/%g(%d)" % (med, mx, n)
        print("%-14s %4d | %10s %8s %3d/%-2d %5d %10s | %-9s %-9s %-9s %4d | %5s | %5.0f | %9s | %7s | %d/%d/%d" % (
            d["cohort"], d["n"], fmt_money(d["m7.sum"]), fmt_money(d["m7.median"]), d["m7.beat"], d["m7.n"],
            d["m7.held"], fmt_money(d["m7.sum_drop_top5"]), m1("target"), m1("thesis"), m1("dte"),
            d["m1.discretion"], "—" if d["m2"] is None else "%.2f" % d["m2"], d["m3.pct"],
            fmt_money(d["m4.sum"]), "—" if d["m5.pct"] is None else "%.1f" % d["m5.pct"],
            d["m6"]["yes"], d["m6"]["no"], d["m6"]["silent"]))
    print()
    print("M7 by reference rule that would have fired (n, alpha):")
    for d in res["cohorts"]:
        parts = ["%s %d/%s" % (k, *d["m7.by.%s" % k][:1], fmt_money(d["m7.by.%s" % k][1]))
                 for k in ("TARGET", "21DTE", "STOP", "GIVE_BACK", "7DTE") if d["m7.by.%s" % k][0]]
        print("  %-14s %s" % (d["cohort"], " · ".join(parts)))
    print()
    print("Read: M7 > 0 means the trader's exits beat the frozen rule on the same marks. M1 latency is "
          "median/max trading days (n). M4 and M5 are lower bounds — the journal samples 3x/day. "
          "'+' on a cohort marks a merge (< %d closes)." % MIN_COHORT)


def flatten(res):
    out = {"as_of": res["as_of"], "book": res["book"], "positions": res["positions"]}
    for d in res["cohorts"]:
        c = d["cohort"]
        for k, v in d.items():
            if k == "cohort":
                continue
            if isinstance(v, dict):
                for kk, vv in v.items():
                    out["%s.%s.%s" % (c, k, kk)] = vv
            elif isinstance(v, tuple):
                for i, vv in enumerate(v):
                    out["%s.%s.%d" % (c, k, i)] = vv
            else:
                out["%s.%s" % (c, k)] = v
    return out


def selftest(db):
    if not FIXTURE:
        print("NO FIXTURE FROZEN. Run --freeze and paste the result into FIXTURE."); return 2
    now = flatten(run(db, FIXTURE_BOOK, FIXTURE_AS_OF, force_weekday=True))
    diffs = [(k, FIXTURE.get(k), now.get(k)) for k in sorted(set(FIXTURE) | set(now))
             if FIXTURE.get(k) != now.get(k)]
    if not diffs:
        print("PASS — %d keys identical. Method unchanged; any difference in a live run is the BOOK, not the code." % len(now))
        return 0
    print("DRIFT — %d keys differ. THE CODE CHANGED; runs are not comparable." % len(diffs))
    for k, a, b in diffs[:40]:
        print("  %-40s frozen %r  now %r" % (k, a, b))
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--book", default="REAL")
    ap.add_argument("--as-of", dest="as_of", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--freeze", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest(a.db))
    if a.freeze:
        f = flatten(run(a.db, FIXTURE_BOOK, FIXTURE_AS_OF, force_weekday=True))
        print("FIXTURE = " + json.dumps(f, indent=1, sort_keys=True).replace("null", "None").replace("true", "True").replace("false", "False"))
        return
    res = run(a.db, a.book, a.as_of)
    if a.json:
        print(json.dumps(res, indent=1, default=str))
    else:
        print_table(res)


if __name__ == "__main__":
    main()
