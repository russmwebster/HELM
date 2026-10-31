"""_s123/w192_apply.py -- W192: bear call and bull put spreads are managed at
21 DTE, not 7 (Russ, 2026-09-26: the 7-day deadline's paper closes lost 10 of
10, median -363% of credit). Russ, 2026-09-27: change ONLY these two.

The deadline is strategy_settings.dte_exit_threshold, ONE ROW PER STRATEGY --
nothing is shared, so condors, CSPs and every other row are untouched (the
script proves it: every other row is byte-for-byte the same after). What
reads it: decision.evaluate (DTE_MANAGE -- the paper exit agent acts on it,
the real book's check and board show it as a flag), exit_considered,
rule_read, and open_cmd._entry_dte_floor (the entry-runway invariant; the
stored entry band is already 30-45 DTE, so the entry window does not move).

Before switching it on it lists every OPEN bear call / bull put spread, both
books, sitting between 7 and 21 DTE -- they would close (paper) or flag
(real) on the next pass -- and REFUSES to apply while any exist unless
--force is given.

    python3 _s123/w192_apply.py            # show, then apply
    python3 _s123/w192_apply.py --dry-run  # show only
    HELM_DB=/path/copy.db python3 ...      # a copy (the verify does this)

Idempotent: a row already at 21 is left alone and said so.
"""
import os, sqlite3, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("HELM_ROOT", ROOT)
from helm.config import DB_PATH                           # noqa: E402
from helm.dates import dte                                 # noqa: E402

STRATS = ("BEAR_CALL_SPREAD", "BULL_PUT_SPREAD")
OLD, NEW = 7, 21
NOTE = "W192 (Russ 2026-09-26/27): dte_exit_threshold 7 -> 21"


def in_window(conn):
    """OPEN bear call / bull put spreads, both books, with 7 < DTE <= 21 (the
    nearest open leg, the way decision.evaluate measures it)."""
    conn.row_factory = sqlite3.Row
    out = []
    for p in conn.execute(
            "SELECT id, ticker, strategy, book, opened_at FROM positions WHERE status='OPEN' "
            "AND strategy IN (?,?) ORDER BY book, ticker", STRATS):
        ds = [dte(r["expiration"]) for r in conn.execute(
            "SELECT expiration FROM legs WHERE position_id=? AND status='OPEN'", (p["id"],))
            if r["expiration"]]
        d = min([x for x in ds if x is not None], default=None)
        out.append(dict(p, dte=d, hit=(d is not None and OLD < d <= NEW)))
    return out


def rows(conn):
    conn.row_factory = sqlite3.Row
    return {(r["account_id"], r["strategy"]): dict(r)
            for r in conn.execute("SELECT * FROM strategy_settings")}


def main(argv):
    dry, force = "--dry-run" in argv, "--force" in argv
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    print("W192 on %s" % DB_PATH)
    open_ = in_window(conn)
    hits = [p for p in open_ if p["hit"]]
    print("open bear call / bull put spreads (both books): %d; between 7 and 21 DTE: %d"
          % (len(open_), len(hits)))
    for p in open_:
        print("  %s %-6s %-17s dte %s  opened %s%s" % (p["book"], p["ticker"], p["strategy"],
              p["dte"], str(p["opened_at"])[:10], "   <- would close (paper) / flag (real)"
              if p["hit"] else ""))
    before = rows(conn)
    for k in sorted(k for k in before if k[1] in STRATS):
        print("  now: %s %s dte_exit_threshold=%s" % (k[0], k[1], before[k]["dte_exit_threshold"]))
    if dry:
        print("dry run -- nothing changed")
        return 0
    if hits and not force:
        print("NOT APPLIED: %d open spread(s) inside the new window -- rerun with --force "
              "once decided" % len(hits))
        return 2
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        n = conn.execute(
            "UPDATE strategy_settings SET dte_exit_threshold=?, last_modified=?, "
            "notes=CASE WHEN notes IS NULL OR notes='' THEN ? ELSE notes || ' | ' || ? END "
            "WHERE strategy IN (?,?) AND dte_exit_threshold=?",
            (NEW, now, NOTE, NOTE) + STRATS + (OLD,)).rowcount
    after = rows(conn)
    conn.close()
    others_same = all(after.get(k) == v for k, v in before.items() if k[1] not in STRATS)
    targets = {k: after[k]["dte_exit_threshold"] for k in after if k[1] in STRATS}
    print("updated %d row(s)%s" % (n, "" if n else " -- already applied"))
    for k, v in sorted(targets.items()):
        print("  after: %s %s dte_exit_threshold=%s" % (k[0], k[1], v))
    print("every other strategy_settings row unchanged: %s" % others_same)
    ok = others_same and all(v == NEW for v in targets.values()) and len(after) == len(before)
    print("W192 %s" % ("APPLIED" if ok else "CHECK FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
