"""helm/stockreads.py -- the price of every stock HELM holds as shares (s158, Russ, 2026-11-14).

Why: the three daily readings price option positions only. Once a put is assigned and
HELM holds plain shares, nothing read their price -- GOOGL's 100 shares carried the
put's last reading (11-05, $284.49) for days, and the book's delta, the what-ifs and
the shares' P&L on Today used it.

Now each scheduled reading (helm snapshot) also reads one price for every ticker in
`stock_positions` and records it here. The snapshot stands down when the market is
shut, so every reading is taken in a session; `live` records whether IBKR called the
quote live (1) or frozen/delayed (0) -- the underlying price the option readings use
is not stored with that flag either, so both are kept. `helm.bookview.share_rows` takes the newer of this and any real
position's own reading on the name.

  stock_checks (ticker, checked_at local time, price, source, live)

A failed quote is printed in the snapshot's log and never costs the snapshot. These
reads are NOT counted in the snapshot's attempted / journaled figures (the Book of
Books' retry decision reads those).

    python3 -m helm.stockreads --selftest
"""
import sqlite3

DDL = ("CREATE TABLE IF NOT EXISTS stock_checks ("
       " ticker TEXT NOT NULL, checked_at TEXT NOT NULL, price REAL NOT NULL,"
       " source TEXT, live INTEGER NOT NULL DEFAULT 1,"
       " PRIMARY KEY (ticker, checked_at))")


def ensure_table(conn):
    conn.execute(DDL)


def held_tickers(conn):
    """Tickers HELM holds shares in (any account), A-Z."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_positions'").fetchone():
        return []
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM stock_positions WHERE shares > 0 ORDER BY ticker")]


def record(conn, ticker, price, checked_at, source="ibkr", live=True):
    """Store one reading. Returns True if stored. A missing or zero price is refused."""
    if price is None:
        return False
    try:
        price = float(price)
    except (TypeError, ValueError):
        return False
    if not price > 0:
        return False
    ensure_table(conn)
    conn.execute("INSERT OR IGNORE INTO stock_checks (ticker, checked_at, price, source, live) "
                 "VALUES (?, ?, ?, ?, ?)", (ticker, checked_at, round(price, 2), source, 1 if live else 0))
    conn.commit()
    return True


def latest(conn, ticker):
    """(price, checked_at) of the newest reading, or None. Safe on a database
    without the table (read-only connections included)."""
    try:
        r = conn.execute("SELECT price, checked_at FROM stock_checks WHERE ticker=? "
                         "ORDER BY checked_at DESC LIMIT 1", (ticker,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return (r[0], r[1]) if r else None


def read_all(fetch, conn, now_iso):
    """Read every held ticker with `fetch(ticker) -> {"price", "live", "error"}`
    (check_cmd.fetch_ibkr_underlying in the snapshot). Returns [(ticker, msg)] for
    the log; never raises for one ticker's failure."""
    out = []
    for t in held_tickers(conn):
        try:
            r = fetch(t) or {}
            if record(conn, t, r.get("price"), now_iso, source=r.get("source") or "ibkr",
                      live=bool(r.get("live"))):
                out.append((t, "%.2f%s" % (float(r["price"]), "" if r.get("live") else " (not live)")))
            else:
                out.append((t, "not stored: %s" % (r.get("error") or "no price")))
        except Exception as e:      # noqa: BLE001
            out.append((t, "FAILED: %s: %s" % (type(e).__name__, str(e)[:60])))
    return out


def _selftest():
    n = [0, 0]

    def ok(c, m):
        n[0 if c else 1] += 1
        print(("  ok   " if c else "  FAIL ") + m)

    c = sqlite3.connect(":memory:")
    ok(latest(c, "GOOGL") is None, "no table -> latest is None")
    ok(held_tickers(c) == [], "no stock_positions -> no tickers")
    c.execute("CREATE TABLE stock_positions (ticker TEXT, shares INTEGER)")
    c.executemany("INSERT INTO stock_positions VALUES (?,?)", [("GOOGL", 100), ("ZZZ", 0), ("AAA", 200)])
    ok(held_tickers(c) == ["AAA", "GOOGL"], "held tickers A-Z, zero-share rows skipped")
    ok(not record(c, "GOOGL", None, "2026-11-16T10:00:05"), "no price refused")
    ok(not record(c, "GOOGL", 0, "2026-11-16T10:00:05"), "zero refused")
    ok(latest(c, "GOOGL") is None, "nothing stored by the refusals")
    ok(record(c, "GOOGL", 294.604, "2026-11-16T10:00:05"), "live stored")
    ok(record(c, "GOOGL", 296.1, "2026-11-16T12:30:04"), "second stored")
    ok(latest(c, "GOOGL") == (296.1, "2026-11-16T12:30:04"), "latest is the newest")
    ok(record(c, "AAA", 50.0, "2026-11-16T10:00:06", live=False) and
       c.execute("SELECT live FROM stock_checks WHERE ticker='AAA'").fetchone()[0] == 0,
       "a frozen quote is stored, marked not live")
    ok(c.execute("SELECT price FROM stock_checks WHERE checked_at LIKE '%10:00%'").fetchone()[0] == 294.6,
       "price rounded to the cent")
    calls = []

    def fetch(t):
        calls.append(t)
        if t == "AAA":
            raise RuntimeError("boom")
        return {"price": 300.0, "live": True, "source": "ibkr"}
    res = dict(read_all(fetch, c, "2026-11-16T15:15:03"))
    ok(calls == ["AAA", "GOOGL"], "read_all reads every held ticker")
    ok(res["AAA"].startswith("FAILED") and res["GOOGL"] == "300.00", "one failure doesn't stop the rest")
    ok(latest(c, "GOOGL")[0] == 300.0, "read_all stored GOOGL")
    print("%d passed, %d failed" % tuple(n))
    return n[1] == 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print(__doc__)
