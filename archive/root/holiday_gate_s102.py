#!/usr/bin/env python3
"""
holiday_gate_s102.py -- make HELM's scheduled agents trading-calendar aware.

W110 / s102 (2026-08-09), decided by Russ.

THE DEFECT
----------
launchd understands weekdays. It knows nothing about market holidays, so every
scheduled agent fires on any Monday-Friday regardless of whether the exchange
is open. Measured on the live book before this shipped:

  * 2026-06-19 (Juneteenth) -- the snapshot journalled 84 position marks.
  * 2026-07-03 (Independence Day observed) -- the paper exit agent CLOSED SEVEN
    POSITIONS (BX, CVX, FCX, ISRG, SLB, UNH CSPs and a BAC iron condor), all
    exit_reason DTE_MANAGE, at 10:01-10:06 and 15:46, against a shut exchange.
    Those seven sit inside the 102-close DTE_MANAGE sample that W16's whole
    deadline argument rests on.

Half-days are the same defect, harder to see: on a 13:00 close the 15:15
snapshot and the 15:35 exit run both fire hours after the bell.

WHAT THIS SHIPS
---------------
1. NEW  helm/market_calendar.py   -- the gate, backed by exchange_calendars
                                     XNYS (already installed in the helm env).
2. EDIT paper_exit_agent.py       -- replaces a weekday-only guard.
3. EDIT helm/cli/check_cmd.py     -- gates cmd_snapshot.
4. EDIT helm/cli/ivr_cmd.py       -- gates cmd_refresh.

Each gated agent, when it stands down, writes an `agent_runs` row with status
SKIPPED_CLOSED naming the reason. It does NOT just fall silent -- HELM-154's
whole point is that a deliberate absence and a failure must not look alike, and
W96's end-of-day audit asserts every expected slot fired.

DELIBERATELY NOT DONE
---------------------
* `com.helm.mktsampler` is left alone -- it writes a CSV, not the book.
* `com.helm.pg` / `com.helm.server` are always-on, no schedule to gate.
* No existing data is touched. The 84 Juneteenth marks and the 7 July-3 closes
  stay exactly as they are; --census reports them so a future study can exclude
  them deliberately (Russ's call, s102).
* The IV refresh's WEEKEND behaviour is decision 1, a plist change, not this.
  This patch stops it on holidays; weekends are the next job.

USAGE
-----
    python3 holiday_gate_s102.py --test      # calendar + gate tests, no writes
    python3 holiday_gate_s102.py --census    # report non-trading-day rows
    python3 holiday_gate_s102.py             # dry run: show the edits
    python3 holiday_gate_s102.py --apply     # back up, patch, verify

Run with the market shut. Nothing here touches the database.
"""

import argparse
import datetime as _dt
import os
import shutil
import sys

ROOT = os.path.expanduser("~/Projects/helm")
STAMP = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")

MODULE_PATH = os.path.join(ROOT, "helm", "market_calendar.py")

MODULE_SRC = '''"""helm/market_calendar.py -- trading-calendar gate for the scheduled agents.

W110 / s102 (2026-08-09).

WHY THIS EXISTS
---------------
launchd understands weekdays; it knows nothing about market holidays, so every
scheduled agent fired on any Monday-Friday whether or not the exchange was
open. Measured before this shipped:

  * 2026-06-19 (Juneteenth)  -- the snapshot journalled 84 position marks.
  * 2026-07-03 (Jul 4 observed) -- the paper exit agent CLOSED SEVEN POSITIONS,
    all DTE_MANAGE, at 10:01-10:06 and 15:46, against a shut exchange.

Half-days matter for the same reason: on a 13:00 close the 15:15 snapshot and
the 15:35 exit run both fire hours after the bell.

SOURCE OF TRUTH
---------------
exchange_calendars, calendar XNYS. Verified 2026-08-09 against Juneteenth,
3 July, Labor Day, Thanksgiving, Christmas and both 1pm half-days (27 Nov and
24 Dec 2026).

FAIL-OPEN, DELIBERATELY
-----------------------
If the calendar cannot answer, the agents RUN. A whole trading day missing from
the corpus costs more than one spurious holiday row -- 2026-08-06 proved that,
when a gateway outage cost 227 readings. A fail-closed bug here would stop the
entire system while every status read green, which is the exact shape HELM-154
exists to prevent.

Every fallback is printed and carried into the run note. None is silent: a
silent except inside a guard against silent failure is the joke writing itself
(s100).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

STATUS_SKIPPED = "SKIPPED_CLOSED"

AGENT_SNAPSHOT = "com.helm.snapshot.daily"
AGENT_EXITS = "com.helm.paper.exits"
AGENT_IVR = "com.helm.ivr.refresh"

_CAL = None
_CAL_TRIED = False


def _calendar():
    """The XNYS calendar, built once per process. None if unavailable."""
    global _CAL, _CAL_TRIED
    if _CAL_TRIED:
        return _CAL
    _CAL_TRIED = True
    try:
        import exchange_calendars as xc
        _CAL = xc.get_calendar("XNYS")
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        print("[market_calendar] WARNING: calendar unavailable (%r); "
              "failing OPEN -- agents will run." % (e,))
        _CAL = None
    return _CAL


def _as_et(now=None):
    if now is None:
        return datetime.now(ET)
    if now.tzinfo is None:
        return now.replace(tzinfo=ET)
    return now.astimezone(ET)


def session_state(now=None) -> dict:
    """Describe the session for `now` (naive datetimes are read as ET).

    Returns {run, reason, degraded, close}. `degraded` marks a fail-open."""
    now = _as_et(now)
    cal = _calendar()
    if cal is None:
        return {"run": True, "degraded": True, "close": None,
                "reason": "trading calendar unavailable - failing open"}
    try:
        import pandas as pd
        ts = pd.Timestamp(now.date())
        if not cal.is_session(ts):
            return {"run": False, "degraded": False, "close": None,
                    "reason": "%s (%s) is not a trading session"
                              % (now.date().isoformat(), now.strftime("%a"))}
        close = cal.session_close(ts).tz_convert(ET)
        if now > close:
            return {"run": False, "degraded": False, "close": close,
                    "reason": "after the %s close on %s"
                              % (close.strftime("%H:%M"), now.date().isoformat())}
        return {"run": True, "degraded": False, "close": close,
                "reason": "session open until %s" % close.strftime("%H:%M")}
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        print("[market_calendar] WARNING: calendar lookup failed (%r); "
              "failing OPEN." % (e,))
        return {"run": True, "degraded": True, "close": None,
                "reason": "calendar lookup failed (%r) - failing open" % (e,)}


def agent_should_run(now=None):
    """(bool, reason). False means the exchange is shut -- stand down."""
    st = session_state(now)
    return st["run"], st["reason"]


def stand_down(agent, started_at, reason, slot=None):
    """Record a deliberate stand-down in the run ledger.

    An agent that simply returns leaves a hole indistinguishable from a crash.
    This writes SKIPPED_CLOSED with the reason named, so W96's end-of-day audit
    can tell 'correctly did nothing' from 'failed to run'."""
    finished = datetime.now().isoformat()
    try:
        from helm.db import get_conn
        conn = get_conn()
        conn.execute(
            "INSERT INTO agent_runs (agent, started_at, finished_at, slot, "
            "attempted, journaled, failed, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (agent, started_at, finished, slot, 0, 0, 0, STATUS_SKIPPED, reason))
        conn.commit()
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        print("[market_calendar] WARNING: could not record stand-down: %r" % (e,))
'''

# ---------------------------------------------------------------- edits ------

EDITS = [
    dict(
        rel="paper_exit_agent.py",
        why="replace the weekday-only guard with a trading-calendar guard",
        old='''    if date.today().weekday() >= 5:
        print("weekend — nothing to do")
        return
''',
        new='''    # W110 (s102): weekday != trading day. This guard used to test only the
    # weekday, so it ran on 2026-07-03 and closed seven paper positions against
    # a shut exchange. Holidays and post-early-close afternoons now stand down,
    # and the stand-down is RECORDED -- a silent return is indistinguishable
    # from a crash (HELM-154).
    from helm.market_calendar import agent_should_run, stand_down, AGENT_EXITS
    _run_ok, _why = agent_should_run()
    if not _run_ok:
        print("market closed (%s) — nothing to do" % _why)
        stand_down(AGENT_EXITS, _started, _why)
        return
''',
    ),
    dict(
        rel="helm/cli/check_cmd.py",
        why="gate cmd_snapshot on the trading calendar",
        old='''    writer, not a view.
    """
    conn = get_conn()
''',
        new='''    writer, not a view.

    W110 (s102): stands down when the exchange is shut. Before this, the three
    slots fired on any weekday -- Juneteenth 2026 journalled 84 marks against a
    closed market -- and on a 13:00 half-day the 15:15 slot fired hours late.
    """
    from helm.market_calendar import agent_should_run, stand_down, AGENT_SNAPSHOT
    _snap_started = _dt_now_iso()
    _run_ok, _why = agent_should_run()
    if not _run_ok:
        print("snapshot: market closed (%s) -- standing down" % _why)
        stand_down(AGENT_SNAPSHOT, _snap_started, _why)
        return
    conn = get_conn()
''',
    ),
    dict(
        rel="helm/cli/ivr_cmd.py",
        why="gate cmd_refresh on the trading calendar",
        old='''    _ivr_started = datetime.now().isoformat()
    from helm.ibkr import get_ib
''',
        new='''    _ivr_started = datetime.now().isoformat()
    # W110 (s102): the IV refresh has no Weekday key at all, so it fires every
    # calendar day. Holidays are handled here; weekends are decision 1, a plist
    # change. A weekend/holiday reading skews low and overwrites the stored row
    # that `helm scan` reads.
    from helm.market_calendar import agent_should_run, stand_down, AGENT_IVR
    _run_ok, _why = agent_should_run()
    if not _run_ok:
        print("ivr refresh: market closed (%s) -- standing down" % _why)
        stand_down(AGENT_IVR, _ivr_started, _why)
        return
    from helm.ibkr import get_ib
''',
    ),
]

# check_cmd needs a tiny helper for the timestamp; add it beside the import.
HELPER_EDIT = dict(
    rel="helm/cli/check_cmd.py",
    why="timestamp helper used by the snapshot stand-down",
    old='''def cmd_snapshot(args):
''',
    new='''def _dt_now_iso():
    """W110: local ISO timestamp for the snapshot stand-down record."""
    from datetime import datetime as _d
    return _d.now().isoformat()


def cmd_snapshot(args):
''',
)


def die(msg):
    print("\\nREFUSING: " + msg)
    print("Nothing was written.")
    sys.exit(2)


# ----------------------------------------------------------------- tests -----

def run_tests():
    """Exercise the gate on dates whose answers are independently known."""
    sys.path.insert(0, ROOT)
    ns = {}
    exec(compile(MODULE_SRC, "market_calendar.py", "exec"), ns)
    agent_should_run = ns["agent_should_run"]
    session_state = ns["session_state"]
    from datetime import datetime

    cases = [
        # (datetime,                       expected_run, label)
        (datetime(2026, 6, 19, 10, 0),  False, "Juneteenth 10:00 - the 84-mark day"),
        (datetime(2026, 7, 3, 10, 1),   False, "3 Jul 10:01 - the 7-close day"),
        (datetime(2026, 7, 3, 15, 46),  False, "3 Jul 15:46 - the BAC condor close"),
        (datetime(2026, 8, 7, 10, 0),   True,  "ordinary Friday 10:00"),
        (datetime(2026, 8, 7, 15, 15),  True,  "ordinary Friday 15:15"),
        (datetime(2026, 8, 8, 10, 0),   False, "Saturday"),
        (datetime(2026, 8, 9, 9, 35),   False, "Sunday 09:35 - today"),
        (datetime(2026, 9, 7, 12, 30),  False, "Labor Day"),
        (datetime(2026, 11, 26, 10, 0), False, "Thanksgiving"),
        (datetime(2026, 11, 27, 12, 0), True,  "half-day 12:00 - before 13:00 close"),
        (datetime(2026, 11, 27, 15, 15), False, "half-day 15:15 - AFTER the close"),
        (datetime(2026, 11, 27, 15, 35), False, "half-day 15:35 - exit agent"),
        (datetime(2026, 12, 24, 12, 30), True,  "Christmas Eve 12:30 - open"),
        (datetime(2026, 12, 24, 15, 15), False, "Christmas Eve 15:15 - after close"),
        (datetime(2026, 12, 25, 10, 0), False, "Christmas Day"),
        (datetime(2026, 1, 19, 10, 0),  False, "MLK Day"),
    ]

    print("=" * 72)
    print("GATE TESTS")
    print("=" * 72)
    bad = 0
    for when, expect, label in cases:
        got, why = agent_should_run(when)
        ok = (got == expect)
        if not ok:
            bad += 1
        print("  %-4s %-40s -> %-5s  %s"
              % ("ok" if ok else "FAIL", label, got, why[:34]))
    print("")

    # fail-open: a broken calendar must let the agents run, loudly
    ns["_CAL"] = None
    ns["_CAL_TRIED"] = True
    st = session_state(datetime(2026, 6, 19, 10, 0))
    fo_ok = st["run"] is True and st["degraded"] is True
    print("  %-4s fail-open with no calendar -> run=%s degraded=%s"
          % ("ok" if fo_ok else "FAIL", st["run"], st["degraded"]))
    if not fo_ok:
        bad += 1

    print("")
    print("%d test(s) failed." % bad if bad else "All %d checks passed."
          % (len(cases) + 1))
    return bad == 0


def census():
    """Report rows already recorded on non-trading days. Read-only."""
    import sqlite3
    sys.path.insert(0, ROOT)
    import exchange_calendars as xc
    import pandas as pd
    cal = xc.get_calendar("XNYS")
    db = os.path.join(ROOT, "data", "helm.db")
    c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)

    print("=" * 72)
    print("NON-TRADING-DAY CENSUS (read-only)")
    print("=" * 72)

    def closed(d):
        try:
            return not cal.is_session(pd.Timestamp(d))
        except Exception:
            return False

    rows = c.execute("SELECT date(checked_at) d, COUNT(*) FROM checks "
                     "GROUP BY 1 ORDER BY 1").fetchall()
    bad = [(d, n) for d, n in rows if d and closed(d)]
    print("checks on non-trading days: %d row(s) across %d date(s)"
          % (sum(n for _, n in bad), len(bad)))
    for d, n in bad:
        print("    %s  %s  %d marks" % (d, _dow(d), n))

    rows = c.execute("SELECT date(closed_at) d, ticker, strategy, book, "
                     "exit_reason, ROUND(COALESCE(realized_pnl,0)) "
                     "FROM positions WHERE closed_at IS NOT NULL "
                     "ORDER BY closed_at").fetchall()
    bad = [r for r in rows if r[0] and closed(r[0])]
    print("")
    print("positions CLOSED on non-trading days: %d" % len(bad))
    for d, tk, st, bk, why, pnl in bad:
        print("    %s  %s  %-14s %-6s %-12s %+d"
              % (d, _dow(d), tk + " " + st, bk, why, pnl))

    rows = c.execute("SELECT date, COUNT(*) FROM iv_history "
                     "GROUP BY 1 ORDER BY 1").fetchall()
    bad = [(d, n) for d, n in rows if closed(d)]
    print("")
    print("iv_history rows on non-trading days: %d row(s) across %d date(s)"
          % (sum(n for _, n in bad), len(bad)))
    print("    (each is superseded by the next refresh -- inert history)")
    c.close()


def _dow(d):
    try:
        return _dt.date.fromisoformat(d[:10]).strftime("%a")
    except Exception:
        return "?"


# ----------------------------------------------------------------- patch -----

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--census", action="store_true")
    args = ap.parse_args()

    if args.test:
        sys.exit(0 if run_tests() else 1)
    if args.census:
        census()
        return

    edits = EDITS + [HELPER_EDIT]

    # ---- verify every anchor before touching anything ----------------------
    print("=" * 72)
    print("HOLIDAY GATE (W110)" +
          ("   [APPLY]" if args.apply else "   [DRY RUN -- nothing written]"))
    print("=" * 72)

    if os.path.exists(MODULE_PATH) and not args.apply:
        print("note: %s already exists and would be OVERWRITTEN" % MODULE_PATH)
    print("NEW   helm/market_calendar.py   (%d lines)"
          % len(MODULE_SRC.splitlines()))

    srcs = {}
    for e in edits:
        p = os.path.join(ROOT, e["rel"])
        if not os.path.exists(p):
            die("missing file: " + p)
        if p not in srcs:
            srcs[p] = open(p, encoding="utf-8").read()
        n = srcs[p].count(e["old"])
        if n != 1:
            die("anchor in %s matched %d times, expected exactly 1.\\n"
                "  anchor: %r" % (e["rel"], n, e["old"][:70]))
        print("EDIT  %-26s anchor unique  -- %s" % (e["rel"], e["why"]))

    # apply in memory, compile, and only then write
    for e in edits:
        p = os.path.join(ROOT, e["rel"])
        srcs[p] = srcs[p].replace(e["old"], e["new"], 1)

    for p, s in srcs.items():
        try:
            compile(s, p, "exec")
        except SyntaxError as ex:
            die("patched %s does not compile: %s" % (p, ex))
    try:
        compile(MODULE_SRC, MODULE_PATH, "exec")
    except SyntaxError as ex:
        die("the new module does not compile: %s" % ex)
    print("")
    print("all patched files compile in memory")

    if not args.apply:
        print("")
        print("Dry run. Re-run with --apply to write.")
        print("Run --test first if you have not: it checks the gate against")
        print("Juneteenth, 3 July, both 1pm half-days and an ordinary Friday.")
        return

    # ---- write -------------------------------------------------------------
    backups = []
    for p in srcs:
        b = p + ".bak-w110-" + STAMP
        shutil.copy2(p, b)
        backups.append(b)
        print("backup: " + os.path.basename(b))

    with open(MODULE_PATH, "w", encoding="utf-8") as fh:
        fh.write(MODULE_SRC)
    for p, s in srcs.items():
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(s)

    # ---- read back and assert the SHAPE ------------------------------------
    problems = []
    if not os.path.exists(MODULE_PATH):
        problems.append("module was not written")
    else:
        m = open(MODULE_PATH, encoding="utf-8").read()
        for token in ("def agent_should_run", "def stand_down",
                      "SKIPPED_CLOSED", "failing OPEN"):
            if token not in m:
                problems.append("module missing %r" % token)

    for e in edits:
        p = os.path.join(ROOT, e["rel"])
        s = open(p, encoding="utf-8").read()
        if e["old"] in s:
            problems.append("%s still contains the OLD text" % e["rel"])
        if e["new"] not in s:
            problems.append("%s does not contain the NEW text" % e["rel"])

    # the specific regression this patch must not cause
    pe = open(os.path.join(ROOT, "paper_exit_agent.py"), encoding="utf-8").read()
    if "weekday() >= 5" in pe:
        problems.append("paper_exit_agent still has the weekday-only guard")

    print("")
    if problems:
        print("READBACK FAILED:")
        for x in problems:
            print("  - " + x)
        print("\\nRestore with:")
        for b in backups:
            print("  cp %s %s" % (b, b.split(".bak-w110-")[0]))
        sys.exit(4)

    print("READBACK OK")
    print("  new module : helm/market_calendar.py")
    print("  gated      : snapshot (3 slots), paper exits, ivr refresh")
    print("  stand-down : recorded as SKIPPED_CLOSED with the reason named")
    print("")
    print("NEXT, in order:")
    print("  1. python3 holiday_gate_s102.py --test     (re-run against the")
    print("     installed module, not the embedded copy)")
    print("  2. helm restart  AND  helm restart pg      (PG imports the engine)")
    print("  3. Monday 09:35 and 10:00 should behave EXACTLY as before --")
    print("     Monday is a trading day. A clean Monday confirms nothing about")
    print("     the holiday path; the first real proof is Labor Day, 7 Sept.")


if __name__ == "__main__":
    main()
