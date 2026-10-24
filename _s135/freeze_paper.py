#!/usr/bin/env python3
"""s135 (Russ, 2026-10-24): freeze the retired paper book.

Closes every OPEN PAPER position at its last stored mark, with exit_reason
RUN_OFF, so nothing paper is left to manage or settle. The paper exit agent
is retired in the same change (paper_exit_agent.PAPER_EXITS_RETIRED).

  python3 _s135/freeze_paper.py                 # dry run: prints every close, writes nothing
  python3 _s135/freeze_paper.py --apply         # backs up helm.db, then writes

Each open leg closes at the current_price of its latest leg_checks row (the
snapshot's mid). A leg that has never been marked closes at its own open
price (P&L 0) and is named. Legs already closed (settled, bought back) keep
their booked close -- _finalize_close, the same function every close uses,
handles that and computes the P&L.

Refuses, writing nothing, when:
  - any open PAPER leg is past its expiry -- run `helm settle --date=...` first;
  - the number of open PAPER positions is not --expect (default 77);
  - a mark is older than --oldest-mark (default 2026-10-23).
A second run finds no open paper positions and does nothing.
REAL positions are never read for closing; the script checks the REAL book is
byte-for-byte unchanged afterwards.
"""
import os, sys, sqlite3, argparse
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("HELM_ROOT", str(ROOT))
sys.path.insert(0, str(ROOT))

from helm.db import get_conn
from helm.cli.close_cmd import _finalize_close
from helm.models.position import Position
from helm.models.leg import Leg

REASON = "RUN_OFF"
TAG = "paper_run_off_20261024"


def real_fingerprint(c):
    p = c.execute("select id,status,realized_pnl,closed_at,exit_reason,notes,tags from positions "
                  "where book='REAL' order by id").fetchall()
    l = c.execute("select l.id,l.status,l.close_price,l.close_date from legs l join positions p "
                  "on p.id=l.position_id where p.book='REAL' order by l.id").fetchall()
    return [tuple(r) for r in p], [tuple(r) for r in l]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--expect", type=int, default=77)
    ap.add_argument("--oldest-mark", default="2026-10-23")
    a = ap.parse_args()
    today = date.today().isoformat()

    c = get_conn()
    print("database:", c.execute("pragma database_list").fetchone()[2])
    pos = [dict(r) for r in c.execute(
        "select * from positions where book='PAPER' and status='OPEN' order by ticker, id")]
    if not pos:
        print("no open PAPER positions -- nothing to do (already frozen)")
        return 0
    if len(pos) != a.expect:
        print("REFUSED: %d open PAPER positions, expected %d" % (len(pos), a.expect))
        return 2

    plan, problems, unmarked = [], [], []
    for p in pos:
        legs = [dict(r) for r in c.execute("select * from legs where position_id=?", (p["id"],))]
        prices, marks = {}, []
        for l in legs:
            if str(l["status"]).upper() == "CLOSED" and l["close_price"] is not None:
                continue
            if (l["expiration"] or "")[:10] < today:
                problems.append("%s %s leg %s %s %s expired %s, not settled"
                                % (p["ticker"], p["strategy"], l["direction"], l["option_type"],
                                   l["strike"], l["expiration"][:10]))
                continue
            m = c.execute("select checked_at, current_price from leg_checks where leg_id=? "
                          "and current_price is not null order by checked_at desc limit 1",
                          (l["id"],)).fetchone()
            if m is None:
                prices[l["id"]] = l["open_price"]
                unmarked.append("%s %s %s %s %s" % (p["ticker"], p["strategy"], l["direction"],
                                                    l["option_type"], l["strike"]))
                marks.append("never marked")
            else:
                if m[0][:10] < a.oldest_mark:
                    problems.append("%s leg %s: last mark %s is older than %s"
                                    % (p["ticker"], l["strike"], m[0][:16], a.oldest_mark))
                prices[l["id"]] = float(m[1])
                marks.append(m[0][:16])
        plan.append((p, prices, marks))

    if problems:
        print("REFUSED -- nothing written:")
        for x in problems:
            print("  " + x)
        print("Settle expired paper legs first: helm settle --date=2026-10-16, then --date=2026-10-23")
        return 2

    # P&L preview, same arithmetic as _finalize_close
    tot, by = 0.0, {}
    for p, prices, marks in plan:
        pnl = 0.0
        for l in c.execute("select * from legs where position_id=?", (p["id"],)):
            l = dict(l)
            cp = l["close_price"] if l["id"] not in prices else prices[l["id"]]
            if cp is None:
                continue
            sgn = 1 if l["direction"] == "SHORT" else -1
            pnl += sgn * (l["open_price"] - cp) * l["contracts"] * l["multiplier"]
        tot += pnl
        s = by.setdefault(p["strategy"], [0, 0.0]); s[0] += 1; s[1] += pnl
        print("  %-6s %-12s opened %s  P&L %10.2f  marks %s"
              % (p["ticker"], p["strategy"], str(p["opened_at"])[:10], pnl, ", ".join(sorted(set(marks)))))
    print("by strategy:")
    for k, (n, v) in sorted(by.items()):
        print("  %-12s %3d positions  P&L %11.2f" % (k, n, v))
    print("total: %d positions  P&L %.2f" % (len(plan), tot))
    if unmarked:
        print("never marked, closed at open price (P&L 0):", "; ".join(unmarked))

    if not a.apply:
        print("DRY RUN -- nothing written. Re-run with --apply.")
        return 0

    dbfile = c.execute("pragma database_list").fetchone()[2]
    bak = "%s.bak-s135-freeze-%s" % (dbfile, datetime.now().strftime("%Y%m%d-%H%M%S"))
    c.execute("VACUUM INTO ?", (bak,))
    print("backup:", bak)
    before = real_fingerprint(c)
    c.close()

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    done = 0
    for p, prices, marks in plan:
        pobj = Position.get(p["id"])
        lobjs = Leg.for_position(p["id"])
        res = _finalize_close(pobj, lobjs, prices, REASON)
        if not res.get("ok", True):
            print("  close FAILED for", p["ticker"], p["id"], res)
            continue
        c = get_conn()
        note = ("RUN_OFF %s: paper book retired 2026-10-24; closed at last mark (%s), "
                "not a rule exit" % (stamp, ", ".join(sorted(set(marks)))))
        c.execute("update positions set notes = coalesce(notes,'') || ' | ' || ?, "
                  "tags = case when tags is null or tags='' then ? else tags || ',' || ? end "
                  "where id=?", (note, TAG, TAG, p["id"]))
        c.commit(); c.close()
        done += 1

    c = get_conn()
    left = c.execute("select count(*) from positions where book='PAPER' and status='OPEN'").fetchone()[0]
    after = real_fingerprint(c)
    print("closed %d; open PAPER positions left: %d" % (done, left))
    print("REAL book unchanged:", "YES" if before == after else "NO -- restore from " + bak)
    return 0 if (left == 0 and before == after) else 1


if __name__ == "__main__":
    sys.exit(main())
