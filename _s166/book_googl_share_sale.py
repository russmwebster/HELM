#!/usr/bin/env python3
"""s166 (2026-11-27) -- book the sale of HELM's 100 GOOGL shares: sold 2026-11-25 at $265.25 (Russ).

Why a one-time script: `helm fills` books shares leaving HELM only when a covered call is assigned
(called away). A plain sale of HELM-held shares is read and left alone, so the 11-25 import booked
the call's buy-back and left the shares in HELM's records.

What it writes -- the mirror of a call-away (helm/assignment.py, s142), in one transaction:
  1. a CLOSED stock leg on the covered-call position GOOGL-COVERED_CALL-20261123-CDB860:
     100 shares, in at the cost basis HELM paid (335.00, the put strike, on the assignment day),
     out at Fidelity's fill price on 2026-11-25. The sleeve's realized total counts it like any closed leg.
  2. that position's realized result = the call's result + the shares' result; a note with the parts.
     Its exit reason is left as it is (the call's own close); the share sale is recorded as Russ's choice.
  3. the shares leave stock_positions (HELM holds none after).
  4. the Fidelity row goes into the fill log (broker_fills, note SHARE_SALE), so its fee counts.
The price, date, quantity and fee come from Fidelity's activity download, never from this file.

    python3 _s166/book_googl_share_sale.py                 dry run (reads only)
    python3 _s166/book_googl_share_sale.py --apply         backup first, then write
    options: --db PATH   --file PATH (an Accounts_History download holding the 11-25 sale)

It refuses unless every row is exactly as expected; a second --apply refuses.
"""
import glob, os, sqlite3, sys, uuid
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HELM_ROOT") or os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from helm import brokerfills as B          # noqa: E402
from helm import sleeve as S               # noqa: E402

TICKER, SHARES, SALE_DATE, EXPECT_PRICE = "GOOGL", 100, "2026-11-25", 265.25
CC_ID = "GOOGL-COVERED_CALL-20261123-CDB860"
SP_ID = "SP-GOOGL"


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def find_fill(path):
    """(fill, key, export) for the one 11-25 sale of 100 GOOGL shares in an Accounts_History download."""
    cands = [path] if path else sorted(
        glob.glob(os.path.join(os.path.expanduser(os.environ.get("HELM_DOWNLOADS", "~/Downloads")), "Accounts_History*.csv")),
        key=os.path.getmtime, reverse=True)
    for c in cands:
        try:
            e = B.read_export(c)
        except Exception:                      # noqa: BLE001 -- an unreadable file is simply not used
            continue
        hits = [(f, k) for f, k in zip(e.fills, e.keys)
                if not f.is_option and f.symbol.strip() == TICKER and f.as_of == SALE_DATE and f.qty == -SHARES]
        if len(hits) == 1:
            return hits[0][0], hits[0][1], e
        if len(hits) > 1:
            sys.exit("REFUSED: %d matching sale rows in %s -- expected one" % (len(hits), B.mask_name(os.path.basename(c))))
    sys.exit("REFUSED: no Accounts_History download in Downloads holds the %s sale of %d %s shares" % (SALE_DATE, SHARES, TICKER))


def check(conn, f, key):
    """Every precondition; returns the rows. Exits with the reason if anything differs."""
    def no(msg):
        sys.exit("REFUSED (nothing written): " + msg)
    sp = conn.execute("SELECT * FROM stock_positions WHERE id=?", (SP_ID,)).fetchone()
    if sp is None:
        no("HELM holds no %s shares (already booked?)" % TICKER)
    if sp["ticker"] != TICKER or int(sp["shares"]) != SHARES or sp["cost_basis"] is None or abs(sp["cost_basis"] - 335.0) > 1e-9:
        no("stock_positions %s is not 100 %s at 335.00" % (SP_ID, TICKER))
    if conn.execute("SELECT count(*) FROM stock_positions WHERE ticker=?", (TICKER,)).fetchone()[0] != 1:
        no("more than one %s share row" % TICKER)
    p = conn.execute("SELECT * FROM positions WHERE id=?", (CC_ID,)).fetchone()
    if p is None or p["status"] != "CLOSED" or p["book"] != "REAL" or p["strategy"] != "COVERED_CALL" or p["ticker"] != TICKER:
        no("%s is not a closed real GOOGL covered call" % CC_ID)
    legs = conn.execute("SELECT * FROM legs WHERE position_id=?", (CC_ID,)).fetchall()
    if len(legs) != 1 or legs[0]["status"] != "CLOSED" or legs[0]["leg_role"] != "SHORT_CALL":
        no("%s: expected its one closed short call and nothing else, found %d legs" % (CC_ID, len(legs)))
    call_result = round(S.leg_realized(legs[0]), 2)
    if abs((p["realized_pnl"] or 0) - call_result) > 0.005:
        no("%s: realized %.2f is not its call's %.2f" % (CC_ID, p["realized_pnl"] or 0, call_result))
    if conn.execute("SELECT count(*) FROM legs l JOIN positions q ON q.id=l.position_id WHERE q.ticker=? AND q.book='REAL' "
                    "AND l.option_type='STOCK'", (TICKER,)).fetchone()[0]:
        no("a %s stock leg already exists" % TICKER)
    if conn.execute("SELECT count(*) FROM broker_fills WHERE fill_key=? OR (symbol=? AND as_of=?)", (key, TICKER, SALE_DATE)).fetchone()[0]:
        no("the sale is already in the fill log")
    if conn.execute("SELECT count(*) FROM positions WHERE ticker=? AND book='REAL' AND status='OPEN'", (TICKER,)).fetchone()[0]:
        no("an open real %s position exists -- its shares may be spoken for" % TICKER)
    if f.price is None or abs(f.price - EXPECT_PRICE) > 1e-9:
        no("Fidelity's fill price %s is not the %.2f Russ gave" % (f.price, EXPECT_PRICE))
    return sp, p, call_result


def main():
    db = os.path.expanduser(arg("--db") or os.environ.get("HELM_DB") or os.path.join(ROOT, "data", "helm.db"))
    apply_ = "--apply" in sys.argv
    f, key, e = find_fill(arg("--file"))
    conn = sqlite3.connect(db if apply_ else "file:%s?mode=ro" % db, uri=not apply_)
    conn.row_factory = sqlite3.Row
    sp, p, call_result = check(conn, f, key)
    cb = float(sp["cost_basis"])
    share_result = round((f.price - cb) * SHARES, 2)
    realized = round(call_result + share_result, 2)
    fee = round((f.commission or 0) + (f.fees or 0), 2)
    when = SALE_DATE + "T16:00:00"
    before = S.realized_since(conn)[0]
    print("loaded: %s" % os.path.abspath(__file__))
    print("database: %s%s" % (db, "" if apply_ else "   (dry run -- reads only)"))
    print("Fidelity: %s  sold %d %s at %.2f, fee $%.2f, cash in $%.2f   (from %s)"
          % (f.as_of, -f.qty, TICKER, f.price, fee, f.amount, B.mask_name(os.path.basename(e.path))))
    print("shares: in at %.2f on %s, out at %.2f on %s -> $%+.2f (before the $%.2f fee)"
          % (cb, (sp["acquired_at"] or "")[:10], f.price, SALE_DATE, share_result, fee))
    print("position %s: realized %.2f -> %.2f  (call $%+.2f, shares $%+.2f)" % (CC_ID, p["realized_pnl"], realized, call_result, share_result))
    print("HELM's %s shares: %d -> 0;   in use falls by $%.2f" % (TICKER, SHARES, cb * SHARES))
    print("sleeve realized (gross, since 10-24): %.2f -> %.2f" % (before, before + share_result))
    if not apply_:
        print("\nDry run. Nothing written. Add --apply to book it.")
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = "%s.bak-s166-googl-%s" % (db, stamp)
    conn.execute("VACUUM INTO ?", (bak,))
    print("backup: %s" % bak)
    now = datetime.now().isoformat(timespec="seconds")
    note = "Russ sold the shares by hand (not called away); booked s166 from Fidelity's fill"
    try:
        conn.execute("BEGIN IMMEDIATE")
        check(conn, f, key)
        sid = "%s-LO-%s" % (CC_ID, uuid.uuid4().hex[:4].upper())
        conn.execute(
            "INSERT INTO legs (id, position_id, leg_role, option_type, direction, strike, expiration, contracts, "
            "multiplier, open_price, close_price, open_date, close_date, status, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, CC_ID, "LONG_STOCK", "STOCK", "LONG", None, None, SHARES, 1, cb, f.price, sp["acquired_at"] or when, when, "CLOSED",
             "%d %s shares sold %s at %g; cost basis %.4f (what HELM paid); result $%.2f. %s"
             % (SHARES, TICKER, SALE_DATE, f.price, cb, share_result, note)))
        cur = conn.execute(
            "UPDATE positions SET realized_pnl=?, notes=COALESCE(notes || ' | ', '') || ?, updated_at=? WHERE id=? AND status='CLOSED'",
            (realized, "shares sold %s: %d at %g; call $%.2f + shares $%.2f = $%.2f; %s"
             % (SALE_DATE, SHARES, f.price, call_result, share_result, realized, note), now, CC_ID))
        if cur.rowcount != 1:
            raise RuntimeError("the position changed under the booking")
        if conn.execute("DELETE FROM stock_positions WHERE id=? AND shares=?", (SP_ID, SHARES)).rowcount != 1:
            raise RuntimeError("the share row changed under the booking")
        conn.execute(
            "INSERT INTO broker_fills (fill_key, source, as_of, run_date, action, symbol, qty, price, commission, fees, amount, "
            "outcome, position_id, leg_id, imported_at, note, downloaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, e.fmt, f.as_of, f.run_date, f.raw_action, f.symbol.strip(), f.qty, f.price, f.commission, f.fees, f.amount,
             "BOOKED", CC_ID, sid, now, "SHARE_SALE", e.downloaded))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    after = S.realized_since(conn)[0]
    print("WRITTEN. stock leg %s; sleeve realized (gross) now %.2f; HELM holds %d %s shares."
          % (sid, after, conn.execute("SELECT COALESCE(SUM(shares),0) FROM stock_positions WHERE ticker=?", (TICKER,)).fetchone()[0], TICKER))


if __name__ == "__main__":
    main()
