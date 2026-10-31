"""s140 (2026-10-31): correct the NVDA PMCC booked at 2 contracts to 1.

Fidelity's 10/30 activity: BOUGHT 1 NVDA 12/17/27 160 call @ 62.90, SOLD 1 NVDA
12/04/26 225 call @ 3.40. `helm open` booked 2 (Russ: the prompt misbehaved).

Changes, one transaction, only these rows:
  positions        total_contracts 2 -> 1, net_premium -11900 -> -5950,
                   max_loss 11900 -> 5950, a correction note appended
  legs (both)      contracts 2 -> 1
  entry_snapshots  premium_collected 11900 -> 5950
  lifecycle_events the OPENED narrative restated at 1x, marked corrected

Refuses unless every row is exactly as booked. Backs up helm.db first.
    python3 _s140/fix_nvda_pmcc_contracts.py            dry run
    python3 _s140/fix_nvda_pmcc_contracts.py --apply    backup, then y/N
"""
import os, sqlite3, sys
from datetime import datetime

POS = "NVDA-PMCC-20261031-CC173B"
SH, LO = POS + "-SH-0501", POS + "-LO-D2BF"
SNAP = "snap-67EDA387"
NOTE = (" | s140 2026-10-31: corrected 2 -> 1 contract (the booking prompt took 2); "
        "Fidelity 10/30: bought 1 160C @ 62.90, sold 1 225C @ 3.40")


def db_path():
    if os.environ.get("HELM_DB"):
        return os.environ["HELM_DB"]
    root = os.environ.get("HELM_ROOT") or os.path.expanduser("~/Projects/helm")
    return os.path.join(root, "data", "helm.db")


def check(c):
    """Return a list of problems; empty means the rows are exactly as booked."""
    bad = []
    p = c.execute("SELECT ticker, strategy, status, book, total_contracts, net_premium, max_loss "
                  "FROM positions WHERE id=?", (POS,)).fetchone()
    if p != ("NVDA", "PMCC", "OPEN", "REAL", 2, -11900.0, 11900.0):
        bad.append("position: %r" % (p,))
    legs = c.execute("SELECT id, leg_role, strike, expiration, contracts, open_price, status "
                     "FROM legs WHERE position_id=? ORDER BY id", (POS,)).fetchall()
    want = [(LO, "LONG_CALL", 160.0, "2027-12-17", 2, 62.9, "OPEN"),
            (SH, "SHORT_CALL", 225.0, "2026-12-04", 2, 3.4, "OPEN")]
    if legs != want:
        bad.append("legs: %r" % (legs,))
    s = c.execute("SELECT position_id, premium_collected FROM entry_snapshots WHERE id=?", (SNAP,)).fetchone()
    if s != (POS, 11900.0):
        bad.append("entry snapshot: %r" % (s,))
    ev = c.execute("SELECT id, narrative FROM lifecycle_events WHERE position_id=? AND event_type='OPENED'",
                   (POS,)).fetchall()
    if len(ev) != 1 or not ev[0][1].startswith("Trade decision: 2x PMCC") or "$11900.00" not in ev[0][1]:
        bad.append("OPENED event: %r" % (ev,))
    for t, col in (("checks", "position_id"), ("leg_checks", "leg_id")):
        try:
            n = c.execute("SELECT COUNT(*) FROM %s WHERE %s LIKE ?" % (t, col), (POS + "%",)).fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
        if n:
            bad.append("%s already has %d reading(s) for it -- a snapshot ran; stop and look" % (t, n))
    return bad, (ev[0] if len(ev) == 1 else None)


def main():
    apply = "--apply" in sys.argv
    path = db_path()
    c = sqlite3.connect(path)
    bad, ev = check(c)
    if bad:
        print("REFUSED -- the rows are not as booked, nothing changed:")
        for b in bad:
            print("  " + b)
        return 1
    new_narr = (ev[1].replace("2x PMCC", "1x PMCC").replace("$11900.00", "$5950.00")
                + " [corrected s140 2026-10-31: booked as 2 by the prompt; Fidelity shows 1]")
    print("NVDA PMCC %s -- will change:" % POS)
    print("  positions        total_contracts 2 -> 1, net_premium -11900 -> -5950, max_loss 11900 -> 5950, note appended")
    print("  legs             160C 12/17/27 and 225C 12/04/26: contracts 2 -> 1")
    print("  entry_snapshots  premium_collected 11900 -> 5950")
    print("  lifecycle_events OPENED narrative -> %s" % new_narr)
    if not apply:
        print("\nDry run -- nothing written. Add --apply to write.")
        return 0
    bak = path + ".bak-s140-nvda-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = sqlite3.connect(bak)
    c.backup(dst)
    dst.close()
    print("\nBackup: %s" % bak)
    if "--yes" not in sys.argv and input("Write these changes? [y/N] ").strip().lower() != "y":
        print("Nothing written.")
        return 0
    now = datetime.now().isoformat()
    with c:
        c.execute("UPDATE positions SET total_contracts=1, net_premium=-5950.0, max_loss=5950.0, "
                  "notes=COALESCE(notes,'') || ?, updated_at=? WHERE id=?", (NOTE, now, POS))
        c.execute("UPDATE legs SET contracts=1 WHERE position_id=? AND id IN (?,?)", (POS, SH, LO))
        c.execute("UPDATE entry_snapshots SET premium_collected=5950.0 WHERE id=?", (SNAP,))
        c.execute("UPDATE lifecycle_events SET narrative=? WHERE id=?", (new_narr, ev[0]))
    p = c.execute("SELECT total_contracts, net_premium FROM positions WHERE id=?", (POS,)).fetchone()
    n = c.execute("SELECT GROUP_CONCAT(contracts) FROM legs WHERE position_id=?", (POS,)).fetchone()[0]
    print("Done: position %s contract(s), net %s; legs %s." % (p[0], p[1], n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
