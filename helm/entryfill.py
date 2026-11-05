"""s151 (Russ, 2026-11-04 -- from the Book's plumbing request): entry readings that the
Fidelity import could not get (IB Gateway down or hung at import) are filled from the
position's FIRST snapshot reading after it was booked.

The import (helm/fillopens.py `book`) never stops for a missing reading any more: it books
the trade from Fidelity's fill, takes the stock price from the next source that has one
(labelled), and marks the position's notes "ENTRY READINGS PENDING (legs: ...)". After every
snapshot, `catch_up()` fills ONLY the gaps -- a column already written is never overwritten --
on those legs' entry_snapshots row(s) and on legs.entry_delta, from the first GOOD leg reading
at or after the leg was booked, and rewrites the marker as "entry readings filled from the first
snapshot <time>, N days after the fill: <fields>". A position whose legs have no reading yet
stays pending for the next snapshot. Each position is its own transaction.
Conventions kept: a put's delta is stored positive (HELM's convention; IBKR and leg_checks give
it negative); theta and IV as the leg reading has them (the import's own convention).
"""
from __future__ import annotations

import re
from datetime import date

MARK = "ENTRY READINGS PENDING"
_RX = re.compile(r"ENTRY READINGS PENDING \(legs: ([^)]*)\)[^|]*")
# entry_snapshots column <- leg_checks column
_SNAP = (("delta", "delta"), ("gamma", "gamma"), ("theta", "theta"), ("vega", "vega"),
         ("iv_current", "iv_current"), ("bid", "current_bid"), ("ask", "current_ask"))


def marker(leg_ids, fields):
    """The note the import writes when entry readings are missing."""
    return "%s (legs: %s): %s -- HELM fills them from the next snapshot" % (
        MARK, ", ".join(leg_ids), ", ".join(fields))


def pending(conn):
    """[(position_id, notes, [leg ids])] for open real positions still marked pending."""
    out = []
    for pid, notes in conn.execute("SELECT id, notes FROM positions WHERE book='REAL' AND status='OPEN' "
                                   "AND notes LIKE ? ORDER BY id", ("%" + MARK + " (legs:%",)):
        m = list(_RX.finditer(notes or ""))
        if m:
            out.append((pid, notes, [x.strip() for x in m[-1].group(1).split(",") if x.strip()]))
    return out


def _first_reading(conn, leg):
    """The leg's first GOOD reading at or after it was booked, with that check's stock price."""
    since = str(leg["created_at"] or leg["open_date"] or "")[:19]
    return conn.execute(
        "SELECT lc.*, c.spot_price AS spot, c.checked_at AS ck_at FROM leg_checks lc "
        "LEFT JOIN checks c ON c.id = lc.check_id WHERE lc.leg_id=? AND lc.checked_at >= ? "
        "AND COALESCE(lc.data_quality,'GOOD')='GOOD' AND lc.delta IS NOT NULL "
        "ORDER BY lc.checked_at LIMIT 1", (leg["id"], since)).fetchone()


def fill_one(conn, pid, notes, leg_ids, today=None):
    """Fill one position's gaps. Returns a message, or None while a leg has no reading yet.
    The caller commits."""
    legs, reads = [], {}
    for lid in leg_ids:
        lg = conn.execute("SELECT * FROM legs WHERE id=? AND position_id=?", (lid, pid)).fetchone()
        if lg is None:
            continue
        rd = _first_reading(conn, lg)
        if rd is None:
            return None
        legs.append(lg)
        reads[lid] = rd
    if not legs:
        return None
    filled, first_at = [], min(r["checked_at"] for r in reads.values())
    # no stock price anywhere at import -> no entry snapshot was written (spot_price is NOT NULL):
    # write it now from the first reading, anchored as the import anchors it (the short leg)
    if conn.execute("SELECT 1 FROM entry_snapshots WHERE position_id=?", (pid,)).fetchone() is None:
        anc = next((l for l in legs if (l["direction"] or "").upper() == "SHORT"), legs[0])
        rd = reads[anc["id"]]
        if rd["spot"] is None:
            return None
        from helm.cli.entry_snapshot import capture_entry_snapshot
        pos = conn.execute("SELECT ticker, net_premium FROM positions WHERE id=?", (pid,)).fetchone()
        try:
            dte = (date.fromisoformat(str(anc["expiration"])[:10]) - date.fromisoformat(str(anc["open_date"])[:10])).days
        except (TypeError, ValueError):
            dte = None
        dl = rd["delta"]
        dl = abs(dl) if (dl is not None and (anc["option_type"] or "").upper() == "PUT") else dl
        spr = (rd["current_ask"] - rd["current_bid"]) if (rd["current_ask"] is not None and rd["current_bid"] is not None) else None
        capture_entry_snapshot(position_id=pid, leg_id=anc["id"], ticker=pos["ticker"], spot_price=rd["spot"],
                               delta=dl, theta=rd["theta"], gamma=rd["gamma"], vega=rd["vega"],
                               iv_current=rd["iv_current"], dte=dte, premium_collected=abs(pos["net_premium"] or 0),
                               bid=rd["current_bid"], ask=rd["current_ask"],
                               bid_ask_spread=None if spr is None else round(spr, 2),
                               bid_ask_spread_pct=(round(spr / rd["current_price"] * 100, 2)
                                                   if (spr is not None and rd["current_price"]) else None),
                               conn=conn)
        filled.append("entry snapshot written (it had none: no stock price at import)")
    for lg in legs:
        rd = reads[lg["id"]]
        dl = rd["delta"]
        dl = abs(dl) if (dl is not None and (lg["option_type"] or "").upper() == "PUT") else dl
        if lg["entry_delta"] is None and dl is not None:
            conn.execute("UPDATE legs SET entry_delta=? WHERE id=? AND entry_delta IS NULL", (dl, lg["id"]))
            filled.append("%s delta" % _name(lg))
        snap = conn.execute("SELECT * FROM entry_snapshots WHERE position_id=? AND leg_id=?", (pid, lg["id"])).fetchone()
        if snap is None:
            continue
        sets, args, names = [], [], []
        if snap["spot_price"] is None and rd["spot"] is not None:
            sets.append("spot_price=?"); args.append(rd["spot"]); names.append("stock price")
        for col, src in _SNAP:
            v = dl if col == "delta" else rd[src]
            if snap[col] is None and v is not None:
                sets.append("%s=?" % col); args.append(v); names.append(col.replace("_current", ""))
        if snap["theta_per_day"] is None and rd["theta"] is not None:
            sets.append("theta_per_day=?"); args.append(rd["theta"])
        if sets:
            conn.execute("UPDATE entry_snapshots SET %s WHERE id=?" % ", ".join(sets), args + [snap["id"]])
            filled.append("entry snapshot " + ", ".join(names))
    try:
        od = date.fromisoformat(str(legs[0]["open_date"])[:10])
        days = (date.fromisoformat(first_at[:10]) - od).days
    except (TypeError, ValueError):
        days = None
    msg = "entry readings filled from the first snapshot %s%s: %s" % (
        first_at[:16].replace("T", " "), "" if days is None else ", %d day%s after the fill" % (days, "" if days == 1 else "s"),
        "; ".join(filled) if filled else "nothing was missing")
    m = list(_RX.finditer(notes or ""))[-1]
    new = notes[:m.start()] + msg + notes[m.end():]
    conn.execute("UPDATE positions SET notes=? WHERE id=?", (new, pid))
    return msg


def _name(lg):
    return "%s %g %s" % ("long" if (lg["direction"] or "").upper() == "LONG" else "short",
                         lg["strike"], (lg["option_type"] or "").lower())


def catch_up(conn=None):
    """Fill every pending position's gaps. Returns [(position_id, message)]; a position still
    waiting for its first reading is left out. Never raises for one position: its error is the message."""
    from helm.db import get_conn
    own = conn is None
    if own:
        conn = get_conn()
    out = []
    try:
        for pid, notes, lids in pending(conn):
            try:
                msg = fill_one(conn, pid, notes, lids)
                if msg is not None:
                    conn.commit()
                    out.append((pid, msg))
                else:
                    conn.rollback()
            except Exception as e:      # noqa: BLE001 -- one position never stops the rest
                conn.rollback()
                out.append((pid, "entry readings not filled: %s: %s" % (type(e).__name__, e)))
    finally:
        if own:
            conn.close()
    return out
