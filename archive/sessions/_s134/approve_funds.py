"""
s134 -- create the approved_funds table and approve Russ's 24 funds (decision of 2026-10-24).

    python3 _s134/approve_funds.py            # dry run: shows what it would write
    python3 _s134/approve_funds.py --apply    # backs up helm.db, then writes

An approved fund is graded FUND by `helm quality` (helm/ownership.py) and offered like
an A-C name. `bucket` is what the sector limit counts it in: sector funds use the
watchlist's sector names, so they share a sector's $90,000 with its stocks.
Refuses if the table already holds any row (run once; change approvals by hand later).
Run on the Mac (the device shell never writes the live database). HELM_DB= points it at a copy.
"""
import os, sqlite3, sys, datetime

FUNDS = [
    # ticker, bucket, name
    ("SPY", "US equity index", "SPDR S&P 500 ETF"),
    ("QQQ", "US equity index", "Invesco QQQ (Nasdaq 100)"),
    ("IWM", "US equity index", "iShares Russell 2000"),
    ("DIA", "US equity index", "SPDR Dow Jones Industrial Average"),
    ("EFA", "International equity", "iShares MSCI EAFE"),
    ("EEM", "International equity", "iShares MSCI Emerging Markets"),
    ("GLD", "Metals", "SPDR Gold Shares"),
    ("SLV", "Metals", "iShares Silver Trust"),
    ("GDX", "Metals", "VanEck Gold Miners"),
    ("TLT", "Bonds", "iShares 20+ Year Treasury Bond"),
    ("HYG", "Bonds", "iShares iBoxx High Yield Corporate Bond"),
    ("IBIT", "Crypto", "iShares Bitcoin Trust"),
    ("SMH", "Technology", "VanEck Semiconductor"),
    ("XLK", "Technology", "Technology Select Sector SPDR"),
    ("XLF", "Financial Services", "Financial Select Sector SPDR"),
    ("XLE", "Energy", "Energy Select Sector SPDR"),
    ("XLB", "Basic Materials", "Materials Select Sector SPDR"),
    ("XLC", "Communication Services", "Communication Services Select Sector SPDR"),
    ("XLI", "Industrials", "Industrial Select Sector SPDR"),
    ("XLP", "Consumer Defensive", "Consumer Staples Select Sector SPDR"),
    ("XLRE", "Real Estate", "Real Estate Select Sector SPDR"),
    ("XLU", "Utilities", "Utilities Select Sector SPDR"),
    ("XLV", "Healthcare", "Health Care Select Sector SPDR"),
    ("XLY", "Consumer Cyclical", "Consumer Discretionary Select Sector SPDR"),
]

DDL = """CREATE TABLE IF NOT EXISTS approved_funds (
    ticker      TEXT PRIMARY KEY,
    bucket      TEXT NOT NULL,            -- what the sector limit counts it in
    approved_at TEXT NOT NULL,
    note        TEXT
)"""


def main():
    apply = "--apply" in sys.argv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db = os.environ.get("HELM_DB") or os.path.join(root, "data", "helm.db")
    assert len(FUNDS) == 24 and len({f[0] for f in FUNDS}) == 24, "fund list must be 24 distinct tickers"
    conn = sqlite3.connect(db)
    exists = conn.execute("select 1 from sqlite_master where type='table' and name='approved_funds'").fetchone()
    n = conn.execute("select count(*) from approved_funds").fetchone()[0] if exists else 0
    print("database:", db)
    print("approved_funds:", "exists with %d rows" % n if exists else "not there yet (will be created)")
    if n:
        print("REFUSED: approved_funds already holds rows. Nothing written.")
        return 1
    for t, b, name in FUNDS:
        print("  %-5s %-24s %s" % (t, b, name))
    if not apply:
        print("dry run: nothing written. Re-run with --apply to write %d rows." % len(FUNDS))
        return 0
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = db + ".bak-s134-funds-" + stamp
    dst = sqlite3.connect(bak)
    conn.backup(dst)
    dst.close()
    print("backup:", bak)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        conn.execute(DDL)
        conn.executemany("insert into approved_funds (ticker, bucket, approved_at, note) values (?,?,?,?)",
                         [(t, b, now, name) for t, b, name in FUNDS])
    got = conn.execute("select count(*) from approved_funds").fetchone()[0]
    print("written: %d rows%s" % (got, "" if got == len(FUNDS) else "  !! expected %d" % len(FUNDS)))
    return 0 if got == len(FUNDS) else 1


if __name__ == "__main__":
    sys.exit(main())
