"""s133 (2026-10-22) -- undo a re-sell booked in error on the REAL JPM diagonal.

On 10-22 12:32 HELM recorded a re-sold SHORT CALL 2026-11-20 $335 x2 @ 8.81
against JPM-DIAGONAL-20261016-FE17CF. Fidelity's actual fill that day was
2 JPM 2026-11-20 $335 PUTS sold @ 7.81 -- a different trade, kept by Russ as
its own CSP and booked separately with `helm open`. The diagonal is long the
12/18 $325C x2 only (its 11/13 $345C was bought back 10-21).

Removes, in one transaction, only the rows the error created:
  * the leg JPM-DIAGONAL-20261016-FE17CF-SH-CA3F and its 2 leg_checks rows
  * its LEG_ADDED lifecycle event EVT-80BE97BA
  * the two 10-22 readings that priced the phantom short as the primary leg
    (CHK-18ED372A 12:34, CHK-6B3EEA02 15:19) and their leg_checks rows
  * resets the 'bare' flag (id 115) that the phantom re-sell had marked RESOLD
And adds: an ADJUSTED lifecycle event "CORRECTION | ..." and a note on the position.
Refuses unless every row is exactly as found on 10-22 18:10. Backs up first.

    python3 _s133/fix_jpm_resell_error.py            # dry run
    python3 _s133/fix_jpm_resell_error.py --apply
    HELM_DB=/path/copy.db python3 _s133/fix_jpm_resell_error.py --apply
"""
import os, sys, sqlite3, uuid, datetime as dt
PID = "JPM-DIAGONAL-20261016-FE17CF"
LID = "JPM-DIAGONAL-20261016-FE17CF-SH-CA3F"
EVT = "EVT-80BE97BA"
CHKS = ("CHK-18ED372A", "CHK-6B3EEA02")
FLAG = 115
NOTE = (" | CORRECTION (s133, 2026-10-22): a re-sold short call 11/20 $335 x2 @ 8.81 booked 10-22 12:32 "
        "was an error -- the Fidelity fill was 2 x 11/20 $335 PUTS @ 7.81, kept as a separate CSP. "
        "Leg, its lifecycle event and the two 10-22 readings that priced it (12:34, 15:19) removed; "
        "'bare' flag reopened.")

here = os.path.dirname(os.path.abspath(__file__))
db = os.path.abspath(os.environ.get("HELM_DB") or os.path.join(here, "..", "data", "helm.db"))
apply = "--apply" in sys.argv
print("database:", db, "| mode:", "APPLY" if apply else "dry run")
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
c.execute("PRAGMA foreign_keys=ON")

def q(sql, *a): return c.execute(sql, a).fetchall()
leg = q("select * from legs where id=?", LID)
p = q("select * from positions where id=?", PID)
checks = q("select id from checks where id in (?,?) and position_id=?", *CHKS, PID)
lc_leg = q("select id from leg_checks where leg_id=?", LID)
lc_chk = q("select id from leg_checks where check_id in (?,?)", *CHKS)
evt = q("select * from lifecycle_events where id=?", EVT)
flag = q("select * from exit_flags where id=?", FLAG)
other_open = q("select id from legs where position_id=? and status='OPEN' and id<>?", PID, LID)
problems = []
if not (len(p) == 1 and p[0]["status"] == "OPEN" and p[0]["book"] == "REAL"): problems.append("position not REAL/OPEN")
if p and "CORRECTION (s133" in (p[0]["notes"] or ""): problems.append("already corrected")
if not (len(leg) == 1 and leg[0]["position_id"] == PID and leg[0]["option_type"] == "CALL" and leg[0]["direction"] == "SHORT"
        and leg[0]["strike"] == 335.0 and leg[0]["expiration"] == "2026-11-20" and leg[0]["contracts"] == 2
        and abs(leg[0]["open_price"] - 8.81) < 1e-9 and leg[0]["status"] == "OPEN"): problems.append("leg not as found")
if len(checks) != 2: problems.append(f"{len(checks)} of the 2 readings found")
if len(lc_leg) != 2: problems.append(f"{len(lc_leg)} leg_checks rows for the leg (expected 2)")
if len(lc_chk) != 4: problems.append(f"{len(lc_chk)} leg_checks rows on the 2 readings (expected 4)")
if not (len(evt) == 1 and evt[0]["leg_id"] == LID and (evt[0]["narrative"] or "").startswith("LEG_ADDED")): problems.append("lifecycle event not as found")
if not (len(flag) == 1 and flag[0]["position_id"] == PID and flag[0]["kind"] == "bare"
        and flag[0]["disposition"] == "ACTED" and flag[0]["reason"] == "RESOLD"): problems.append("bare flag not as found")
if [r["id"] for r in other_open] != ["JPM-DIAGONAL-20261016-FE17CF-LO-E7CC"]: problems.append("other open legs not exactly the 12/18 $325C")
if q("select count(*) n from checks where position_id=? and checked_at>?", PID, "2026-10-22T15:20")[0]["n"]:
    problems.append("a reading newer than 10-22 15:19 exists -- it also priced the phantom short; re-measure first")
if problems:
    sys.exit("REFUSED: " + "; ".join(problems) + ". Nothing written.")

print(f"  remove leg {LID} (SHORT CALL 2026-11-20 $335 x2 @ 8.81)")
print(f"  remove {len(lc_leg)} + {len(lc_chk)} leg_checks rows (overlapping: {len({r['id'] for r in lc_leg} | {r['id'] for r in lc_chk})} distinct)")
print(f"  remove readings {CHKS}; remove lifecycle {EVT}; reopen exit flag {FLAG} (bare)")
print(f"  add ADJUSTED lifecycle 'CORRECTION | ...'; notes += {NOTE!r}")
if not apply:
    sys.exit(0)
bak = db + ".bak-s133-jpm-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
c.execute("VACUUM INTO ?", (bak,)); print("backup:", bak)
now = dt.datetime.now().isoformat()
with c:
    n = {}
    n["lc"] = c.execute("delete from leg_checks where leg_id=? or check_id in (?,?)", (LID, *CHKS)).rowcount
    n["chk"] = c.execute("delete from checks where id in (?,?) and position_id=?", (*CHKS, PID)).rowcount
    n["evt"] = c.execute("delete from lifecycle_events where id=? and leg_id=?", (EVT, LID)).rowcount
    n["leg"] = c.execute("delete from legs where id=? and status='OPEN'", (LID,)).rowcount
    n["flag"] = c.execute("update exit_flags set disposition=NULL, decided_at=NULL, decided_date=NULL, decided_by=NULL, reason=NULL "
                          "where id=? and reason='RESOLD'", (FLAG,)).rowcount
    n["new_evt"] = c.execute("insert into lifecycle_events (id, position_id, leg_id, event_type, occurred_at, narrative, created_at) "
                             "values (?,?,NULL,'ADJUSTED',?,?,?)",
                             ("EVT-" + uuid.uuid4().hex[:8].upper(), PID, now,
                              "CORRECTION | removed SHORT_CALL $335 2026-11-20 x2 @ 8.81 booked 10-22 12:32 in error "
                              "(Fidelity fill was 2 x $335 PUTS @ 7.81, kept as a separate CSP) | s133", now)).rowcount
    n["note"] = c.execute("update positions set notes=coalesce(notes,'')||?, updated_at=? where id=?", (NOTE, now, PID)).rowcount
    want = {"lc": 4, "chk": 2, "evt": 1, "leg": 1, "flag": 1, "new_evt": 1, "note": 1}
    if n != want:
        raise SystemExit(f"REFUSED mid-way: {n} != {want}; rolled back.")
print("OK:", n)
