#!/usr/bin/env python3
"""W156 -- replay the gateway watchdog over days whose answers are known.

    python3 tools/w156_gwwatch_selftest.py            # show the replay
    python3 tools/w156_gwwatch_selftest.py --selftest  # assert it

READ-ONLY. It replays the real sampler log minute by minute against a
throwaway state file; it notifies nothing and writes nothing else.

The fixtures are real days, chosen because their answers are already known:
  2026-08-24  the outage    - MUST alert, early
  2026-08-25  still down    - MUST NOT alert again (one alert per outage)
  2026-08-26  clean day     - MUST recover once, then stay silent
  2026-08-21  clean day     - MUST stay silent throughout

A check that cannot fail on your worst known day is decoration, so this
also perturbs: with the consecutive-sample threshold raised beyond the
outage's length, the outage day must stop alerting.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helm import gateway_watch as G  # noqa: E402

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "logs", "mktdata_samples.csv")

results = []


def check(ok, label, got=None, want=None):
    results.append((bool(ok), label, got, want))


def replay(day, runs_all, consecutive=None, state=None, online=True):
    """Walk one trading day at 10-minute steps. Returns the actions fired."""
    old = G.CONSECUTIVE
    if consecutive:
        G.CONSECUTIVE = consecutive
    fired = []
    state = dict(state or {"state": G.OK})
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
        t = d.replace(hour=9, minute=30)
        end = d.replace(hour=16, minute=0)
        while t <= end:
            visible = [r for r in runs_all if r[0] <= t]
            v = G.decide(visible, t, state, online=online)
            state = {"state": v["state"]}
            if v["action"] != "NONE":
                fired.append((t.strftime("%H:%M"), v["action"], v["message"][:60]))
            t += timedelta(minutes=10)
    finally:
        G.CONSECUTIVE = old
    return fired, state


def main():
    selftest = "--selftest" in sys.argv
    runs_all = G.read_runs(CSV, tail_bytes=10_000_000)
    if not runs_all:
        print("no sampler data found at %s" % CSV)
        return 1

    days = ["2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26"]
    fired = {}
    carried = {"state": G.OK}
    for day in days:
        f, carried = replay(day, runs_all, state=carried)
        fired[day] = f

    if not selftest:
        for day in days:
            print("%s" % day)
            for t, action, msg in fired[day] or []:
                print("   %s  %-14s %s" % (t, action, msg))
            if not fired[day]:
                print("   (silent)")
        return 0

    # 1 - the outage day must alert, and early enough to matter.
    acts = [a for _t, a, _m in fired["2026-08-24"]]
    check("OUTAGE" in acts, "08-24 (the outage) alerts", acts, "OUTAGE")
    first = [t for t, a, _m in fired["2026-08-24"] if a == "OUTAGE"]
    check(bool(first) and first[0] <= "10:10",
          "08-24 alerts within 40 minutes of the open", first[:1], "<= 10:10")

    # 2 - it must not shout all day, nor start the next day fresh.
    check(len(fired["2026-08-24"]) <= 4, "08-24 stays quiet after the first alert",
          len(fired["2026-08-24"]), "<= 4")
    check(all(a != "OUTAGE" for _t, a, _m in fired["2026-08-25"]),
          "08-25 does not re-alert an outage already reported",
          [a for _t, a, _m in fired["2026-08-25"]], "no OUTAGE")

    # 3 - recovery is announced once, then silence.
    acts26 = [a for _t, a, _m in fired["2026-08-26"]]
    check(acts26.count("RECOVERED") == 1, "08-26 announces recovery exactly once",
          acts26.count("RECOVERED"), 1)
    check("OUTAGE" not in acts26, "08-26 (a clean day) reports no outage", acts26, "no OUTAGE")

    # 4 - a clean day in a clean week says nothing at all.
    f21, _ = replay("2026-08-21", runs_all, state={"state": G.OK})
    check(f21 == [], "08-21 (clean) is silent throughout", f21, [])

    # 5 - THE CONTROL. Raise the threshold past the outage's length and the
    #     alarm must fall silent - proving these days drive it, not luck.
    f_blunt, _ = replay("2026-08-24", runs_all, consecutive=999, state={"state": G.OK})
    check(all(a != "OUTAGE" for _t, a, _m in f_blunt),
          "with the threshold blunted, the outage day stops alerting",
          [a for _t, a, _m in f_blunt], "no OUTAGE")

    # 6 - the sampler-stale branch, which no real day here exercises.
    now = datetime(2026, 8, 26, 11, 0)
    stale = G.decide([(now - timedelta(minutes=90), True)], now, {"state": G.OK})
    check(stale["action"] == "SAMPLER_STALE", "a stale sampler is its own alert",
          stale["action"], "SAMPLER_STALE")
    fresh = G.decide([(now - timedelta(minutes=5), True)], now, {"state": G.OK})
    check(fresh["action"] == "NONE", "a fresh sampler is not", fresh["action"], "NONE")

    # 6b - THE TIMEZONE TRAP, caught by running this in a UTC shell against a
    #      sampler writing ET: a healthy sampler read four hours stale. The
    #      mtime path must not care what timezone the caller is in.
    now_utc = datetime(2026, 8, 26, 15, 0)          # a UTC clock
    et_rows = [(datetime(2026, 8, 26, 11, 0), True)]  # the sampler, in ET
    skewed = G.decide(et_rows, now_utc, {"state": G.OK})
    check(skewed["action"] == "SAMPLER_STALE",
          "without the mtime, a timezone skew reads as a stale sampler",
          skewed["action"], "SAMPLER_STALE")
    fixed = G.decide(et_rows, now_utc, {"state": G.OK}, sampler_age_min=4.0)
    check(fixed["action"] == "NONE",
          "with the mtime, the same skew is correctly ignored",
          fixed["action"], "NONE")

    # 6c - NO INTERNET MEANS SILENCE (Russ, 2026-08-28). On a road trip the
    #      gateway cannot hold an IBKR login, so it stops listening; that is
    #      correct behaviour, and "restart the gateway" is advice he cannot
    #      act on. Replay the same outage day with no connection.
    f_off, end_state = replay("2026-08-24", runs_all, state={"state": G.OK}, online=False)
    check(f_off == [], "offline: the outage day says nothing at all", f_off, [])
    check(end_state["state"] == G.OFFLINE, "offline: the state records why it was quiet",
          end_state["state"], G.OFFLINE)

    # and the reconnection must not be announced as a recovery from an outage
    # nobody was ever told about.
    f_back, _ = replay("2026-08-26", runs_all, state=end_state, online=True)
    check(all(a != "RECOVERED" for _t, a, _m in f_back),
          "coming back online announces no phantom recovery",
          [a for _t, a, _m in f_back], "no RECOVERED")

    # THE CONTROL: the same day, same data, WITH internet, must still alert -
    # proving the silence comes from the connection test and not from the day.
    f_on, _ = replay("2026-08-24", runs_all, state={"state": G.OK}, online=True)
    check(any(a == "OUTAGE" for _t, a, _m in f_on),
          "with internet, the same day still raises the alarm",
          [a for _t, a, _m in f_on][:1], "OUTAGE")

    # the probe itself must never raise, whatever it finds
    try:
        G.has_internet(hosts=[("127.0.0.1", 9)], timeout=0.05)
        ok_probe = True
    except Exception:
        ok_probe = False
    check(ok_probe, "the internet probe never raises")
    check(G.has_internet(hosts=[("127.0.0.1", 9)], timeout=0.05) is False,
          "an unreachable host reads as offline")

    # 7 - nothing fires outside a session.
    off = G.run(now=datetime(2026, 8, 23, 12, 0), csv_path=CSV,
                state_path=os.path.join(tempfile.mkdtemp(), "s.json"),
                do_notify=False, quiet=True)
    check(off["action"] == "NONE", "Sunday stands down", off["action"], "NONE")

    failed = [r for r in results if not r[0]]
    for ok, label, got, want in results:
        if not ok:
            print("DRIFT  %-52s got %r want %r" % (label, got, want))
    print("%s  %d checks, %d drifted" % ("PASS" if not failed else "FAIL",
                                         len(results), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
