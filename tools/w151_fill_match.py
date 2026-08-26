#!/usr/bin/env python3
"""W151 step 2 - prove the widened match key against the live book.

READ-ONLY. Opens data/helm.db with mode=ro and asserts that a write fails,
so "read-only" is demonstrated rather than asserted in a docstring.

    python3 tools/w151_fill_match.py             # show how each fixture row resolves
    python3 tools/w151_fill_match.py --selftest  # assert it, both ways

What this has to get right, and what the old key got wrong:
  * the four condor legs resolve to the four RIGHT legs, by direction
  * the June CSP resolves to the CSP, never to the condor
  * a fill that agrees on contract but not direction is a NEAR_MISS,
    reported out loud - the MU case from W104 must stop being a MATCH
    without becoming a silence
  * the partial close resolves and says it is a partial, not a mismatch
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402

from helm.brokerfills import parse_activity_csv  # noqa: E402
from helm import fillmatch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "fidelity_activity_lrcx_s108.csv")
DB = os.environ.get("HELM_DB") or os.path.join(os.path.dirname(HERE), "data", "helm.db")

CONDOR = "LRCX-IRON_CONDOR-20260629-2A46F6"
CSP = "LRCX-CSP-2026-06-02-MANUAL"
ANY = ("OPEN", "CLOSED", "PENDING")

# The broker's fills against what HELM stored - the whole reason for W151.
BROKER = {
    "-LRCX260821P360": 27.90,
    "-LRCX260821P350": 24.22,
    "-LRCX260821C560": 14.90,
    "-LRCX260821C570": 13.80,
}
STORED = {
    "-LRCX260821P360": 29.47,
    "-LRCX260821P350": 25.20,
    "-LRCX260821C560": 14.81,
    "-LRCX260821C570": 14.30,
}

results = []


def check(ok, label, got=None, want=None):
    results.append((bool(ok), label, got, want))


def main():
    fills = parse_activity_csv(FIXTURE).fills
    conn = fillmatch.open_readonly(DB)
    selftest = "--selftest" in sys.argv

    opens = [f for f in fills if f.event == "OPEN" and f.expiration == "2026-08-21"]
    closes = [f for f in fills if f.event == "CLOSE" and f.expiration == "2026-08-21"]
    csp = [f for f in fills if f.expiration == "2026-07-17"]

    if not selftest:
        for match in fillmatch.resolve_all(conn, fills, statuses=ANY):
            print("  " + match.describe())
        return 0

    # 0 - read-only is a property to demonstrate, not to claim.
    try:
        conn.execute("CREATE TABLE w151_should_not_exist (x)")
        check(False, "database opened read-only", "write succeeded", "write refused")
    except sqlite3.OperationalError:
        check(True, "database opened read-only")

    # 1 - the four entry legs resolve to the four right legs.
    for match in fillmatch.resolve_all(conn, opens, statuses=ANY):
        sym = match.fill.symbol
        check(match.ok, "entry " + sym + " resolves", match.outcome, "RESOLVED")
        if not match.ok:
            continue
        check(match.leg["position_id"] == CONDOR, "entry " + sym + " lands on the condor",
              match.leg["position_id"], CONDOR)
        check(match.leg["direction"] == match.fill.leg_direction,
              "entry " + sym + " lands on the right leg",
              match.leg["direction"], match.fill.leg_direction)
        check(round(match.stored_price, 2) == STORED[sym],
              "entry " + sym + " stored price is the synthesised one",
              match.stored_price, STORED[sym])
        check(round(match.fill.price, 2) == BROKER[sym],
              "entry " + sym + " broker price would replace it",
              match.fill.price, BROKER[sym])
    landed = {m.leg["id"] for m in fillmatch.resolve_all(conn, opens, statuses=ANY) if m.ok}
    check(len(landed) == 4, "four entry fills land on four DISTINCT legs", len(landed), 4)

    # 2 - the partial close resolves, on the inverted direction, and says so.
    for match in fillmatch.resolve_all(conn, closes, statuses=ANY):
        sym = match.fill.symbol
        check(match.ok, "close " + sym + " resolves", match.outcome, "RESOLVED")
        if match.ok:
            check("partial: 7 of 20" in match.note, "close " + sym + " is named a partial",
                  match.note, "partial: 7 of 20")

    # 3 - THE TRAP. The CSP must land on the CSP and never on the condor.
    for match in fillmatch.resolve_all(conn, csp, statuses=ANY):
        check(match.ok, "csp " + match.fill.action + " resolves", match.outcome, "RESOLVED")
        if match.ok:
            check(match.leg["position_id"] == CSP, "csp lands on the CSP, not the condor",
                  match.leg["position_id"], CSP)
            check(match.leg["position_id"] != CONDOR, "csp is not the condor")

    # 4 - a near miss must be loud. This is W104's requirement: adding the
    #     field must stop the false MATCH without producing a silence.
    flipped = replace(opens[0], leg_direction=("LONG" if opens[0].leg_direction == "SHORT" else "SHORT"))
    miss = fillmatch.resolve(conn, flipped, statuses=ANY)
    check(miss.outcome == fillmatch.NEAR_MISS, "wrong direction is a NEAR_MISS",
          miss.outcome, fillmatch.NEAR_MISS)
    check(miss.outcome != fillmatch.RESOLVED, "wrong direction is never a match")
    check("DIRECTION" in miss.note, "the near miss names the field that differed", miss.note)
    check(len(miss.candidates) == 1, "the near miss shows what HELM holds",
          len(miss.candidates), 1)

    # 5 - the status scope actually scopes. The condor is CLOSED, so the live
    #     confirm path's default must not reach it.
    default_scope = fillmatch.resolve(conn, opens[0])
    check(default_scope.outcome == fillmatch.NO_MATCH,
          "default OPEN scope does not reach a CLOSED position",
          default_scope.outcome, fillmatch.NO_MATCH)

    # 6 - share rows and settlement rows carry no fill price and are skipped,
    #     never matched.
    shares = [f for f in fills if not f.is_option]
    check(len(shares) == 2, "two share rows in the fixture", len(shares), 2)
    for match in fillmatch.resolve_all(conn, shares, statuses=ANY):
        check(match.outcome == fillmatch.SKIPPED, "share row skipped", match.outcome, "SKIPPED")
    settle = [f for f in fills if f.is_option and f.leg_direction is None]
    check(len(settle) == 4, "four settlement rows", len(settle), 4)
    for match in fillmatch.resolve_all(conn, settle, statuses=ANY):
        check(match.outcome == fillmatch.SKIPPED, "settlement row skipped",
              match.outcome, "SKIPPED")

    failed = [r for r in results if not r[0]]
    for ok, label, got, want in results:
        if not ok:
            print("DRIFT  %-52s got %r want %r" % (label, got, want))
    print("%s  %d checks, %d drifted" % ("PASS" if not failed else "FAIL", len(results), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
