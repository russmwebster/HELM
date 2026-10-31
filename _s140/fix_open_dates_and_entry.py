"""s140 (2026-10-31): put the NVDA PMCC and the XLE put on their real trade date,
and correct two entry-snapshot readings taken from Saturday's stale quotes.

Both were traded Fri 2026-10-30 (Fidelity activity) and booked with `helm open` on
Sat 10-31, which stamps the booking day. The import's convention for a fill date:
16:00 on the trade date, labelled "date only" (the export has no time).

  both       positions.opened_at -> 2026-10-30T16:00:00, entry_dte 34 -> 35,
             legs.open_date -> 2026-10-30, the OPENED event's occurred_at likewise,
             entry snapshot dte 34 -> 35, a note saying what changed and why
  XLE        the "RULE OVERRIDE (W189) ... 22.0% of mid" note -> restated: the 22.0%
             was Saturday's stale quote; the 10-30 12:13 put list (IBKR) read the
             12/04 67 put at 1.18 x 1.21, 2.5% -- inside the rule. The snapshot's
             bid / ask / spread are set to that reading.
  NVDA       entry snapshot (short 225 call) gamma 0.0120, vega 0.1986 -- IBKR
             2026-10-30 13:47 ET, read in session s140 (Yahoo gave none at booking)

created_at stays the booking time. Refuses unless every row is exactly as booked.
    python3 _s140/fix_open_dates_and_entry.py            dry run
    python3 _s140/fix_open_dates_and_entry.py --apply    backup, then y/N
"""
import json, os, sqlite3, sys
from datetime import datetime

NV, XL = "NVDA-PMCC-20261031-CC173B", "XLE-CSP-20261031-5354C5"
NV_LEGS = (NV + "-SH-0501", NV + "-LO-D2BF")
XL_LEGS = (XL + "-SH-B74A",)
NV_SNAP, XL_SNAP = "snap-67EDA387", "snap-ABB1A94A"
NV_EV, XL_EV = "EVT-2094E9A7", "EVT-E23E93A9"
WHEN, DAY = "2026-10-30T16:00:00", "2026-10-30"
DATE_NOTE = (" | s140 2026-10-31: opened on the fill date %s (Fidelity activity; the export has no time "
             "-- 16:00 used, date only); booked with helm open on 2026-10-31" % DAY)
XL_OLD = ("RULE OVERRIDE (W189): the bid-ask spread is 22.0% of mid, at or over 10% -- not suggested "
          "as a CSP (W189); booked 8 contract(s) as typed")
XL_NEW = ("spread 2.5% of mid at entry (10-30 12:13 put list, IBKR: 1.18 x 1.21) -- inside the rule; "
          "booking on Sat 10-31 read 22.0% from stale quotes and recorded a RULE OVERRIDE (W189) "
          "that did not apply (corrected s140)")
NV_GREEKS = "gamma/vega from IBKR 2026-10-30 13:47 ET (s140); other fields read at booking 2026-10-31"
XL_SPREAD = "bid/ask/spread from the 10-30 12:13 put list (IBKR); other fields read at booking 2026-10-31"


def db_path():
    if os.environ.get("HELM_DB"):
        return os.environ["HELM_DB"]
    root = os.environ.get("HELM_ROOT") or os.path.expanduser("~/Projects/helm")
    return os.path.join(root, "data", "helm.db")


def check(c):
    bad = []
    for pid, legs in ((NV, NV_LEGS), (XL, XL_LEGS)):
        p = c.execute("SELECT status, book, substr(opened_at,1,10), entry_dte FROM positions WHERE id=?", (pid,)).fetchone()
        if p != ("OPEN", "REAL", "2026-10-31", 34):
            bad.append("%s position: %r" % (pid, p))
        got = c.execute("SELECT id, open_date, status FROM legs WHERE position_id=? ORDER BY id", (pid,)).fetchall()
        if got != sorted((l, "2026-10-31", "OPEN") for l in legs):
            bad.append("%s legs: %r" % (pid, got))
    for ev in (NV_EV, XL_EV):
        e = c.execute("SELECT event_type, substr(occurred_at,1,10) FROM lifecycle_events WHERE id=?", (ev,)).fetchone()
        if e != ("OPENED", "2026-10-31"):
            bad.append("event %s: %r" % (ev, e))
    s = c.execute("SELECT position_id, dte, gamma, vega FROM entry_snapshots WHERE id=?", (NV_SNAP,)).fetchone()
    if s != (NV, 34, None, None):
        bad.append("NVDA snapshot: %r" % (s,))
    s = c.execute("SELECT position_id, dte, bid_ask_spread_pct FROM entry_snapshots WHERE id=?", (XL_SNAP,)).fetchone()
    if s != (XL, 34, 22.0):
        bad.append("XLE snapshot: %r" % (s,))
    n = c.execute("SELECT notes FROM positions WHERE id=?", (XL,)).fetchone()
    if not n or (n[0] or "").count(XL_OLD) != 1:
        bad.append("XLE notes do not hold the override text once")
    for pid in (NV, XL):
        k = c.execute("SELECT COUNT(*) FROM checks WHERE position_id=?", (pid,)).fetchone()[0]
        if k:
            bad.append("%s already has %d snapshot reading(s); stop and look" % (pid, k))
    return bad


def _settings(c, snap, key, text):
    raw = c.execute("SELECT settings_snapshot FROM entry_snapshots WHERE id=?", (snap,)).fetchone()[0]
    d = json.loads(raw) if raw else {}
    d[key] = text
    return json.dumps(d)


def main():
    path = db_path()
    c = sqlite3.connect(path)
    bad = check(c)
    if bad:
        print("REFUSED -- the rows are not as booked, nothing changed:")
        for b in bad:
            print("  " + b)
        return 1
    print("Will change:")
    print("  NVDA PMCC and XLE put: opened %s (was 2026-10-31), entry DTE 35, legs' open date %s," % (WHEN, DAY))
    print("    the OPENED event's time, the entry snapshot's DTE 35, a note on each position")
    print("  XLE note: '%s'" % XL_NEW)
    print("  XLE entry snapshot: bid 1.18, ask 1.21, spread 0.03 = 2.5% (was 22.0%)")
    print("  NVDA entry snapshot: gamma 0.0120, vega 0.1986 (were empty)")
    if "--apply" not in sys.argv:
        print("\nDry run -- nothing written. Add --apply to write.")
        return 0
    bak = path + ".bak-s140-dates-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = sqlite3.connect(bak); c.backup(dst); dst.close()
    print("\nBackup: %s" % bak)
    if "--yes" not in sys.argv and input("Write these changes? [y/N] ").strip().lower() != "y":
        print("Nothing written.")
        return 0
    now = datetime.now().isoformat()
    nv_set = _settings(c, NV_SNAP, "s140_greeks", NV_GREEKS)
    xl_set = _settings(c, XL_SNAP, "s140_spread", XL_SPREAD)
    with c:
        for pid in (NV, XL):
            c.execute("UPDATE positions SET opened_at=?, entry_dte=35, notes=COALESCE(notes,'') || ?, updated_at=? "
                      "WHERE id=?", (WHEN, DATE_NOTE, now, pid))
            c.execute("UPDATE legs SET open_date=? WHERE position_id=?", (DAY, pid))
        c.execute("UPDATE positions SET notes=replace(notes, ?, ?) WHERE id=?", (XL_OLD, XL_NEW, XL))
        for ev in (NV_EV, XL_EV):
            c.execute("UPDATE lifecycle_events SET occurred_at=? WHERE id=?", (WHEN, ev))
        c.execute("UPDATE entry_snapshots SET dte=35, gamma=0.0120, vega=0.1986, settings_snapshot=? WHERE id=?",
                  (nv_set, NV_SNAP))
        c.execute("UPDATE entry_snapshots SET dte=35, bid=1.18, ask=1.21, bid_ask_spread=0.03, "
                  "bid_ask_spread_pct=2.5, settings_snapshot=? WHERE id=?", (xl_set, XL_SNAP))
    for pid in (NV, XL):
        r = c.execute("SELECT opened_at, entry_dte FROM positions WHERE id=?", (pid,)).fetchone()
        print("Done: %s opened %s, entry DTE %s" % (pid, r[0], r[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
