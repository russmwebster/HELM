#!/usr/bin/env python3
"""s135 (Russ, 2026-10-24): close the BSX stock leg the 5/29 close left open.

BSX-COVERED_CALL-20260525-6BC6EC closed 2026-05-29 (TARGET, +$393.20) with its
short call closed but its LONG_STOCK leg (400 sh @ 68.94) left OPEN. Fidelity
holds no BSX. HELM never tracked the shares' disposal, so the leg is closed AT
COST on the position's close date: P&L 0, the position's realized_pnl is not
touched. It is the only open leg on a closed position in the database.

  python3 _s135/fix_bsx_stock_leg.py            # dry run
  python3 _s135/fix_bsx_stock_leg.py --apply    # backs up helm.db, then writes

Refuses unless the leg and its position are exactly as described; a second
run finds the leg closed and does nothing. Checks that nothing else changed.
"""
import os, sys, sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = os.environ.get("HELM_DB") or str(ROOT / "data" / "helm.db")
LEG = "BSX-COVERED_CALL-20260525-6BC6EC-LO-5C4A"
POS = "BSX-COVERED_CALL-20260525-6BC6EC"
CLOSE_DATE = "2026-05-29T00:00:00"
NOTE = (" | s135 record repair 2026-10-24: leg left OPEN by the 2026-05-29 close; "
        "closed at cost (P&L 0) on the position's close date -- HELM did not track "
        "the shares' disposal; Fidelity held no BSX at 2026-10-24")


def snapshot(c):
    return (c.execute("select * from positions order by id").fetchall(),
            c.execute("select * from legs where id != ? order by id", (LEG,)).fetchall())


def main():
    apply = "--apply" in sys.argv
    c = sqlite3.connect(DB)
    print("database:", DB)
    leg = c.execute("select status, direction, option_type, contracts, open_price, close_price, "
                    "position_id from legs where id=?", (LEG,)).fetchone()
    pos = c.execute("select status, book, closed_at, realized_pnl from positions where id=?",
                    (POS,)).fetchone()
    if leg is None or pos is None:
        print("REFUSED: leg or position not found"); return 2
    if leg[0] == "CLOSED":
        print("leg already CLOSED -- nothing to do"); return 0
    want_leg = ("OPEN", "LONG", "STOCK", 400, 68.94, None, POS)
    want_pos = ("CLOSED", "REAL", CLOSE_DATE, 393.2)
    if tuple(leg) != want_leg or (pos[0], pos[1], pos[2], round(pos[3], 2)) != want_pos:
        print("REFUSED: rows not as expected\n  leg %r\n  pos %r" % (leg, pos)); return 2
    others = c.execute("select count(*) from legs l join positions p on p.id=l.position_id "
                       "where l.status='OPEN' and p.status!='OPEN' and l.id!=?", (LEG,)).fetchone()[0]
    print("will close leg %s: 400 BSX @ 68.94, close 68.94 on %s (P&L 0); position P&L stays 393.20"
          % (LEG, CLOSE_DATE[:10]))
    print("other open legs on closed positions: %d" % others)
    if not apply:
        print("DRY RUN -- nothing written. Re-run with --apply."); return 0

    bak = "%s.bak-s135-bsx-%s" % (DB, datetime.now().strftime("%Y%m%d-%H%M%S"))
    c.execute("VACUUM INTO ?", (bak,))
    print("backup:", bak)
    before = snapshot(c)
    cur = c.execute("update legs set status='CLOSED', close_price=open_price, close_date=?, "
                    "notes=coalesce(notes,'') || ? where id=? and status='OPEN'",
                    (CLOSE_DATE, NOTE, LEG))
    if cur.rowcount != 1:
        c.rollback(); print("REFUSED: update touched %d rows; rolled back" % cur.rowcount); return 2
    after = snapshot(c)
    if before != after:
        c.rollback(); print("REFUSED: something else changed; rolled back"); return 2
    c.commit()
    print("closed. Positions and every other leg unchanged: YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
