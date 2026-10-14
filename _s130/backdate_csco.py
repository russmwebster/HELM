"""s130 (2026-10-14) -- move the CSCO CSP to its real fill time.

Russ sold 3 CSCO 2026-11-20 $105 puts at 2.27 on 2026-10-13 ~15:00 and booked
them on 2026-10-14 12:41 (HELM can't record an earlier time). Approved by
Russ, 2026-10-14. One transaction, this position only. Refuses unless it finds
exactly the rows as booked. Backs up the database first (VACUUM INTO).

    python3 _s130/backdate_csco.py            # dry run: show before/after, write nothing
    python3 _s130/backdate_csco.py --apply    # back up, then write
    HELM_DB=/path/copy.db python3 _s130/backdate_csco.py --apply   # on a copy
"""
import os, sys, sqlite3, datetime as dt

PID = "CSCO-CSP-20261014-4E3977"
LID = "CSCO-CSP-20261014-4E3977-SH-9708"
OLD_OPEN, NEW_OPEN = "2026-10-14T12:41:41.806613", "2026-10-13T15:00:00"
OLD_LEG_CA = "2026-10-14T12:41:41.809005"
NOTE = (" | BACKDATED (s130): filled 2026-10-13 ~15:00 at 2.27; booked 2026-10-14 12:41. "
        "Entry snapshot is from the booking, not the fill.")

here = os.path.dirname(os.path.abspath(__file__))
db = os.environ.get("HELM_DB") or os.path.join(here, "..", "data", "helm.db")
db = os.path.abspath(db)
apply = "--apply" in sys.argv
print("database:", db, "| mode:", "APPLY" if apply else "dry run")

c = sqlite3.connect(db)
c.row_factory = sqlite3.Row

def show(tag):
    p = c.execute("select opened_at, created_at, notes from positions where id=?", (PID,)).fetchone()
    l = c.execute("select open_date, created_at from legs where id=?", (LID,)).fetchone()
    print(f"  {tag} position opened_at={p['opened_at']} created_at={p['created_at']}")
    print(f"  {tag} position notes={p['notes']!r}")
    print(f"  {tag} leg open_date={l['open_date']} created_at={l['created_at']}")

p = c.execute("select * from positions where id=?", (PID,)).fetchall()
l = c.execute("select * from legs where position_id=?", (PID,)).fetchall()
ok = (len(p) == 1 and len(l) == 1 and l[0]["id"] == LID
      and p[0]["opened_at"] == OLD_OPEN and p[0]["status"] == "OPEN" and p[0]["book"] == "REAL"
      and l[0]["open_date"] == "2026-10-14" and l[0]["created_at"] == OLD_LEG_CA
      and l[0]["strike"] == 105.0 and l[0]["contracts"] == 3 and abs(l[0]["open_price"] - 2.27) < 1e-9
      and "BACKDATED" not in (p[0]["notes"] or ""))
if not ok:
    sys.exit("REFUSED: the position is not exactly as booked (already changed, or different rows). Nothing written.")
show("before")
if not apply:
    print("  after  position opened_at=%s ; leg open_date=2026-10-13 created_at=%s ; notes += %r"
          % (NEW_OPEN, NEW_OPEN, NOTE))
    sys.exit(0)

bak = db + ".bak-s130-csco-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
c.execute("VACUUM INTO ?", (bak,))
print("backup:", bak)
with c:
    n1 = c.execute("update positions set opened_at=?, notes=coalesce(notes,'')||? where id=? and opened_at=?",
                   (NEW_OPEN, NOTE, PID, OLD_OPEN)).rowcount
    n2 = c.execute("update legs set open_date='2026-10-13', created_at=? where id=? and created_at=?",
                   (NEW_OPEN, LID, OLD_LEG_CA)).rowcount
    if (n1, n2) != (1, 1):
        raise SystemExit(f"REFUSED mid-way: rows changed {n1},{n2}; rolled back.")
show("after ")
print("OK: 1 position, 1 leg moved to 2026-10-13 15:00.")
