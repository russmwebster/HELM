"""helm/netpnl.py -- net of fees, the P&L of record from 2026-10-24 (s142).

Rulebook v0.2: net of fees is the P&L of record, gross beside it. Russ, 2026-10-31,
chose the small version: net from the sleeve's start (10/24) only; older trades stay
gross, labelled "fees not applied" (the year's fees are about $16 a trade -- they
change no study's answer).

Fees come from broker_fills: every Fidelity row the import booked, matched or used is
recorded there with its commission and fees. Two rules:

  * ONE ROW PER TRADE. Fidelity can revise a row's fee after the trade (XLE 10-30:
    $0.09 in one download, $0.11 in a later one), and a revised row has a new key, so
    both can be recorded. fee_rows() keeps one per (date, symbol, opening/closing,
    quantity, price) -- the copy from the NEWEST download (its "Date downloaded", stored
    per row), whatever order the downloads were imported in.
  * The SLEEVE counts fees as paid: every HELM row dated from the start, deduped. A
    fee paid before 10/24 (the opening fee of a put sold before then) was paid before
    the sleeve existed and is not in it -- the same cash basis as dividends.

A POSITION's net = its gross realized result - the fees of its own rows. A position
split by a partial close (tag split_from:<id>) shares its original's opening rows by
contracts. Labels: "net" (an opening row is recorded for every option leg), "net of
the fees HELM has" (one is missing), "gross -- fees not applied" (closed before 10/24).
"""
from __future__ import annotations

import sqlite3

START = "2026-10-24"


def _has(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _side(action):
    a = (action or "").upper()
    for w in ("OPENING", "CLOSING", "ASSIGNED", "EXERCISED", "EXPIRED", "DIVIDEND"):
        if w in a:
            return w
    return "OTHER"


def fee_rows(conn, where="1=1", args=()):
    """broker_fills rows, one per trade (the last imported of any revised copies)."""
    if not _has(conn, "broker_fills"):
        return []
    dl = "downloaded_at" in {c[1] for c in conn.execute("PRAGMA table_info(broker_fills)")}
    order = "COALESCE(downloaded_at, ''), imported_at, fill_key" if dl else "imported_at, fill_key"
    rows = conn.execute("SELECT * FROM broker_fills WHERE %s ORDER BY %s" % (where, order), args).fetchall()
    keep = {}
    for r in rows:
        k = (r["as_of"], (r["symbol"] or "").strip(), _side(r["action"]), r["qty"], r["price"])
        keep[k] = r          # the newest download's copy wins (a row with no download time counts as oldest)
    return list(keep.values())


def _fee(r):
    return float(r["commission"] or 0) + float(r["fees"] or 0)


def fees_since(conn, start=START):
    """Every fee HELM paid from `start` (sleeve, cash basis)."""
    return round(sum(_fee(r) for r in fee_rows(conn, "as_of >= ?", (start,))), 2)


def _family(conn, pid):
    """[position rows] of the trade a split belongs to: the original and its pieces."""
    p = conn.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
    root = pid
    for t in (p["tags"] or "").split(","):
        if t.startswith("split_from:"):
            root = t[len("split_from:"):]
    fam = [conn.execute("SELECT * FROM positions WHERE id=?", (root,)).fetchone()]
    fam += conn.execute("SELECT * FROM positions WHERE tags LIKE ?", ("%split_from:" + root + "%",)).fetchall()
    return root, [x for x in fam if x is not None]


def _contracts(conn, pid):
    r = conn.execute("SELECT COALESCE(SUM(contracts),0) FROM legs WHERE position_id=? AND option_type IN ('PUT','CALL')",
                     (pid,)).fetchone()
    return int(r[0] or 0)


def position(conn, pid):
    """{gross, fees, net, label, opening_rows_missing} for one real position."""
    p = conn.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
    gross = p["realized_pnl"]
    if p["status"] != "OPEN" and (p["closed_at"] or "")[:10] < START:
        return {"gross": gross, "fees": None, "net": gross, "label": "gross -- fees not applied (closed before %s)" % START,
                "opening_rows_missing": None}
    root, fam = _family(conn, pid)
    ids = [x["id"] for x in fam]
    rows = fee_rows(conn, "position_id IN (%s)" % ",".join("?" * len(ids)), tuple(ids))
    own = [r for r in rows if r["position_id"] == pid and _side(r["action"]) != "OPENING"]
    opening = [r for r in rows if _side(r["action"]) == "OPENING"]
    if len(fam) > 1:
        total = sum(_contracts(conn, x) for x in ids) or 1
        share = _contracts(conn, pid) / total
    else:
        share = 1.0
    fees = round(sum(_fee(r) for r in own) + share * sum(_fee(r) for r in opening), 2)
    # every option leg of the root opened with a recorded opening row?
    legs = conn.execute("SELECT * FROM legs WHERE position_id=? AND option_type IN ('PUT','CALL')", (root,)).fetchall()
    have = {(r["as_of"], (r["symbol"] or "").strip()) for r in opening}
    missing = 0
    for l in legs:
        sym = _symbol(conn, root, l)
        if ((l["open_date"] or "")[:10], sym) not in have:
            missing += 1
    # a leg bought or sold back has a closing row with a fee (an expiry or assignment has none)
    closes = {(r["as_of"], (r["symbol"] or "").strip()) for r in rows if _side(r["action"]) == "CLOSING"}
    miss_close = 0
    for l in conn.execute("SELECT * FROM legs WHERE position_id=? AND option_type IN ('PUT','CALL') AND status='CLOSED' "
                          "AND close_price > 0", (pid,)).fetchall():
        if ((l["close_date"] or "")[:10], _symbol(conn, pid, l)) not in closes:
            miss_close += 1
    net = None if gross is None else round(gross - fees, 2)
    gaps = []
    if missing:
        gaps.append("%d opening row(s)" % missing)
    if miss_close:
        gaps.append("%d closing row(s)" % miss_close)
    label = "net" if not gaps else "net of the fees HELM has -- %s not recorded" % " and ".join(gaps)
    return {"gross": gross, "fees": fees, "net": net, "label": label, "opening_rows_missing": missing,
            "closing_rows_missing": miss_close}


def _symbol(conn, pid, l):
    """Fidelity's option symbol for a leg: -TICKERYYMMDD[C|P]STRIKE."""
    tk = conn.execute("SELECT ticker FROM positions WHERE id=?", (pid,)).fetchone()[0]
    e = (l["expiration"] or "")[:10]
    k = float(l["strike"])
    ks = ("%d" % k) if k == int(k) else ("%g" % k)
    return "-%s%s%s%s%s%s" % (tk, e[2:4], e[5:7], e[8:10], "P" if (l["option_type"] or "") == "PUT" else "C", ks)


def _selftest():
    import tempfile, os
    fails = []

    def ok(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)

    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE positions (id TEXT PRIMARY KEY, ticker TEXT, status TEXT, closed_at TEXT, realized_pnl REAL, tags TEXT);
      CREATE TABLE legs (id TEXT, position_id TEXT, option_type TEXT, strike REAL, expiration TEXT, contracts INT,
                         open_date TEXT, status TEXT, close_price REAL, close_date TEXT);
      CREATE TABLE broker_fills (fill_key TEXT PRIMARY KEY, as_of TEXT, action TEXT, symbol TEXT, qty INT, price REAL,
                                 commission REAL, fees REAL, position_id TEXT, imported_at TEXT);
      INSERT INTO positions VALUES ('X','XLE','OPEN',NULL,NULL,NULL),
                                   ('XP','XLE','CLOSED','2026-11-02T16:00:00',305,'split_from:X'),
                                   ('W','WMT','CLOSED','2026-10-30T16:00:00',192,NULL),
                                   ('O','OLD','CLOSED','2026-09-01T16:00:00',100,NULL);
      INSERT INTO legs VALUES ('XL','X','PUT',67,'2026-12-04',3,'2026-10-30T16:00:00','OPEN',NULL,NULL),
                              ('XPL','XP','PUT',67,'2026-12-04',5,'2026-10-30T16:00:00','CLOSED',0.61,'2026-11-02T16:00:00'),
                              ('WL','W','PUT',113,'2026-11-20',2,'2026-10-19T16:00:00','CLOSED',0.92,'2026-10-30T16:00:00');
      INSERT INTO broker_fills VALUES
        ('a1','2026-10-30','YOU SOLD OPENING TRANSACTION PUT (XLE)',' -XLE261204P67',-8,1.22,5.20,0.09,'X','2026-10-31T10:00'),
        ('a2','2026-10-30','YOU SOLD OPENING TRANSACTION PUT (XLE)',' -XLE261204P67',-8,1.22,5.20,0.11,'X','2026-10-31T19:00'),
        ('a3','2026-11-02','YOU BOUGHT CLOSING TRANSACTION PUT (XLE)',' -XLE261204P67',5,0.61,3.25,0.05,'XP','2026-11-02T20:00'),
        ('w1','2026-10-30','YOU BOUGHT CLOSING TRANSACTION PUT (WMT)',' -WMT261120P113',2,0.92,1.30,0.02,'W','2026-10-31T10:00'),
        ('w0','2026-10-19','YOU SOLD OPENING TRANSACTION PUT (WMT)',' -WMT261120P113',-2,1.88,1.30,0.03,'W','2026-10-31T10:00');
    """)
    ok(len(fee_rows(c)) == 4, "a revised copy of a row counts once (XLE: $0.09 then $0.11 -> one row)")
    ok(abs(fees_since(c) - (5.31 + 3.30 + 1.32)) < 1e-9, "the sleeve's fees: rows dated from 10/24, deduped: %s" % fees_since(c))
    w = position(c, "W")
    ok(w["fees"] == 2.65 and w["net"] == 189.35 and w["label"] == "net",
       "WMT: open and close fees, net $189.35, labelled net: %s" % w)
    x, xp = position(c, "X"), position(c, "XP")
    ok(abs(xp["fees"] - (3.30 + 5.31 * 5 / 8)) < 0.006 and abs(x["fees"] - 5.31 * 3 / 8) < 0.006,
       "a split shares its opening fee by contracts (5/8 and 3/8): %s / %s" % (xp["fees"], x["fees"]))
    ok(abs(x["fees"] + xp["fees"] - (5.31 + 3.30)) < 0.011, "...and the parts add up to the trade's fees")
    o = position(c, "O")
    ok(o["fees"] is None and o["net"] == 100 and o["label"].startswith("gross -- fees not applied"),
       "a trade closed before 10/24 stays gross, labelled")
    c.execute("DELETE FROM broker_fills WHERE fill_key='w0'")
    w = position(c, "W")
    ok(w["opening_rows_missing"] == 1 and w["label"].startswith("net of the fees HELM has"),
       "a missing opening row is labelled: %s" % w["label"])
    c.execute("DELETE FROM broker_fills WHERE fill_key='w1'")
    w = position(c, "W")
    ok(w["closing_rows_missing"] == 1 and "1 closing row(s)" in w["label"], "a missing closing row is labelled too: %s" % w["label"])
    # the newest DOWNLOAD's copy wins, whatever the import order
    c.execute("ALTER TABLE broker_fills ADD COLUMN downloaded_at TEXT")
    c.execute("UPDATE broker_fills SET downloaded_at='2026-10-31T16:50' WHERE fill_key='a1'")   # $0.09, newer download
    c.execute("UPDATE broker_fills SET downloaded_at='2026-10-30T12:32' WHERE fill_key='a2'")   # $0.11, imported later
    r = [x for x in fee_rows(c) if x["symbol"].strip() == "-XLE261204P67" and x["qty"] == -8]
    ok(len(r) == 1 and r[0]["fill_key"] == "a1", "the newest download's copy wins over the one imported last")
    print("\n%d failed" % len(fails) if fails else "\nall passed")
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
