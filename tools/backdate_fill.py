"""Move a late-booked REAL single-leg position to its real fill time (s132, 2026-10-20).

Reusable successor to _s130/backdate_csco.py. `helm open` stamps the booking
time; when a trade is booked after it filled, this moves the position's
opened_at, its leg's open_date/created_at and positions.entry_dte to the fill,
and appends a BACKDATED note. Everything else (price, contracts, the entry
snapshot, checks) is left as booked: the entry snapshot stays a record of the
BOOKING and the note says so.

It refuses unless the rows are exactly as expected: REAL, OPEN, one option leg,
open price equal to --price, not already BACKDATED, fill strictly earlier than
the booking. Backs up the database first (VACUUM INTO). Dry run by default.

    python3 tools/backdate_fill.py --position ID --filled-at 2026-10-12T15:00 --price 8.37 [--approx] [--apply]
    python3 tools/backdate_fill.py --position ID --dte-only [--apply]
        (an already-BACKDATED position whose entry_dte still counts from the booking day)
    HELM_DB=/path/copy.db python3 tools/backdate_fill.py ...      # on a copy

Russ runs --apply on the Mac. The device VM never writes the live database.
"""
import argparse, os, sqlite3, sys, datetime as dt

ap = argparse.ArgumentParser()
ap.add_argument("--position", required=True)
ap.add_argument("--filled-at")
ap.add_argument("--price", type=float)
ap.add_argument("--approx", action="store_true", help="the broker shows no exact minute")
ap.add_argument("--dte-only", action="store_true")
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

here = os.path.dirname(os.path.abspath(__file__))
db = os.path.abspath(os.environ.get("HELM_DB") or os.path.join(here, "..", "data", "helm.db"))
print("database:", db, "| mode:", "APPLY" if a.apply else "dry run")
c = sqlite3.connect(db); c.row_factory = sqlite3.Row

def refuse(why):
    sys.exit("REFUSED: " + why + " Nothing written.")

ps = c.execute("select * from positions where id=?", (a.position,)).fetchall()
if len(ps) != 1: refuse("no such position (or not unique).")
p = ps[0]
legs = c.execute("select * from legs where position_id=?", (a.position,)).fetchall()
if p["book"] != "REAL" or p["status"] != "OPEN": refuse(f"position is {p['book']}/{p['status']}, not REAL/OPEN.")
if len(legs) != 1 or legs[0]["option_type"] in (None, "STOCK"): refuse(f"{len(legs)} legs; this script handles one option leg only.")
l = legs[0]
exp = dt.date.fromisoformat(l["expiration"][:10])
notes = p["notes"] or ""

def show(tag):
    q = c.execute("select opened_at, entry_dte, notes from positions where id=?", (a.position,)).fetchone()
    r = c.execute("select open_date, created_at from legs where id=?", (l["id"],)).fetchone()
    print(f"  {tag}: opened_at={q['opened_at']} entry_dte={q['entry_dte']} leg open_date={r['open_date']} created_at={r['created_at']}")
    print(f"  {tag}: notes={q['notes']!r}")

if a.dte_only:
    if "BACKDATED" not in notes: refuse("--dte-only is for a position already BACKDATED.")
    new_dte = (exp - dt.date.fromisoformat(p["opened_at"][:10])).days
    if p["entry_dte"] == new_dte: refuse(f"entry_dte is already {new_dte}.")
    note = f" | entry_dte {p['entry_dte']} -> {new_dte} (counted from the fill day, s132)."
    show("before")
    if not a.apply:
        print(f"  after : entry_dte={new_dte}; notes += {note!r}"); sys.exit(0)
    bak = db + ".bak-dte-" + p["ticker"].lower() + "-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    c.execute("VACUUM INTO ?", (bak,)); print("backup:", bak)
    with c:
        n = c.execute("update positions set entry_dte=?, notes=coalesce(notes,'')||? where id=? and entry_dte=?",
                      (new_dte, note, a.position, p["entry_dte"])).rowcount
        if n != 1: raise SystemExit(f"REFUSED mid-way: {n} rows; rolled back.")
    show("after "); print("OK: entry_dte corrected."); sys.exit(0)

if not a.filled_at or a.price is None: refuse("--filled-at and --price are required.")
fill = dt.datetime.fromisoformat(a.filled_at)
booked = dt.datetime.fromisoformat(p["opened_at"])
if "BACKDATED" in notes: refuse("already BACKDATED.")
if abs((l["open_price"] or -1) - a.price) > 1e-9: refuse(f"leg open price is {l['open_price']}, not {a.price}.")
if not fill < booked: refuse(f"fill {fill} is not before the booking {booked}.")
if l["open_date"] != p["opened_at"][:10]: refuse(f"leg open_date {l['open_date']} does not match the booking day.")
if fill.date() > exp: refuse("fill is after expiry.")
n_pre = c.execute("select count(*) from checks where position_id=?", (a.position,)).fetchone()[0]
new_open = fill.isoformat(timespec="seconds")
new_dte = (exp - fill.date()).days
when = f"{fill:%Y-%m-%d} {'~' if a.approx else ''}{fill:%H:%M}" + (" (broker shows no minute)" if a.approx else "")
note = (f" | BACKDATED (s132): filled {when} at {a.price:g}; booked {booked:%Y-%m-%d %H:%M}. "
        f"Readings before the booking were never taken. Entry snapshot is from the booking, not the fill. "
        f"entry_dte {p['entry_dte']} -> {new_dte}.")
show("before")
if not a.apply:
    print(f"  after : opened_at={new_open} entry_dte={new_dte} leg open_date={fill.date()} created_at={new_open}")
    print(f"  after : notes += {note!r}"); sys.exit(0)
bak = db + ".bak-backdate-" + p["ticker"].lower() + "-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
c.execute("VACUUM INTO ?", (bak,)); print("backup:", bak)
with c:
    n1 = c.execute("update positions set opened_at=?, entry_dte=?, notes=coalesce(notes,'')||? where id=? and opened_at=?",
                   (new_open, new_dte, note, a.position, p["opened_at"])).rowcount
    n2 = c.execute("update legs set open_date=?, created_at=? where id=? and created_at=?",
                   (fill.date().isoformat(), new_open, l["id"], l["created_at"])).rowcount
    if (n1, n2) != (1, 1): raise SystemExit(f"REFUSED mid-way: rows changed {n1},{n2}; rolled back.")
show("after "); print(f"OK: moved to {new_open}.")
