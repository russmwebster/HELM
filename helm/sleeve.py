"""helm/sleeve.py -- HELM's share of the Rollover IRA and the three book limits.

Russ's decisions of 2026-10-24 (claude/HELM-revision-decisions-2026-10-24.md and
claude/HELM-rulebook-v0.2.md section 2, in the project):

  share   = $300,000 + the realized result of every REAL leg closed from 10/24 on
            (each leg when it closes, expires or is assigned; open positions'
            unrealized results never count; real book only). GROSS of fees until
            the Fidelity import exists -- then re-stated as net back to 10/24.
            Dividends on HELM-held shares will count when the import reads them.
  in use  = each open CSP's collateral (strike x 100 x contracts)
            + each open diagonal / PMCC / diagonal-put long at cost
            + assigned shares at their cost basis (stock_positions)
            + any open retired-structure long at cost, or its stored max loss
  limits  = in use <= share; one name <= 20% of the share; one sector <= 30%.
            A name's sector is the watchlist's; an approved fund's is its
            approved_funds bucket (so a sector fund shares its sector's limit
            with the stocks in it). A name with no sector counts as its own.

READ-ONLY. Nothing here writes, and nothing here blocks: a limit makes the
suggestion 0 with a reason; Russ can type a count, and the caller records it
as an override (HELM informs, never acts on the real book).

    python3 -m helm.sleeve --selftest
"""
import math
import os
import sqlite3

SLEEVE_BASE = 300_000.0
SLEEVE_START = "2026-10-24"        # legs closed on/after this date move the share
NAME_PCT = 0.20
SECTOR_PCT = 0.30

SLEEVE_TOTAL = "sleeve_total"
SLEEVE_NAME = "sleeve_name"
SLEEVE_SECTOR = "sleeve_sector"
BINDINGS = (SLEEVE_TOTAL, SLEEVE_NAME, SLEEVE_SECTOR)

DIAG_FAMILY = ("DIAGONAL", "DIAGONAL_PUT", "PMCC")


def _conn(db=None):
    if db is None:
        from helm.config import DB_PATH
        db = str(DB_PATH)
    c = sqlite3.connect("file:" + os.path.abspath(str(db)) + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _money(x):
    return "$" + format(int(round(float(x))), ",d")


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def sector_of(conn, ticker):
    """(sector, source). Approved fund bucket first, then the watchlist."""
    t = (ticker or "").upper()
    if _has_table(conn, "approved_funds"):
        r = conn.execute("SELECT bucket FROM approved_funds WHERE ticker=?", (t,)).fetchone()
        if r and r["bucket"]:
            return r["bucket"], "fund bucket"
    r = conn.execute("SELECT sector FROM watchlist WHERE ticker=?", (t,)).fetchone()
    if r and r["sector"]:
        return r["sector"], "watchlist"
    return "no sector: " + t, "missing"


def leg_realized(leg):
    """Gross realized dollars of one closed leg; None if it has no close price."""
    if leg["close_price"] is None or leg["open_price"] is None:
        return None
    sign = 1.0 if leg["direction"] == "LONG" else -1.0
    return sign * (leg["close_price"] - leg["open_price"]) * (leg["multiplier"] or 100) * (leg["contracts"] or 0)


def realized_since(conn, start=None):
    start = start or SLEEVE_START
    rows = conn.execute(
        "SELECT l.*, p.ticker FROM legs l JOIN positions p ON p.id = l.position_id "
        "WHERE p.book = 'REAL' AND l.status IN ('CLOSED','EXPIRED','ASSIGNED') "
        "AND l.close_date IS NOT NULL AND substr(l.close_date, 1, 10) >= ?", (start,)).fetchall()
    total, missing = 0.0, []
    for r in rows:
        v = leg_realized(r)
        if v is None:
            missing.append(r["id"])
        else:
            total += v
    return total, len(rows), missing


def in_use_items(conn, account_id=None):
    """One row per thing using the sleeve: (ticker, what, dollars, position_id)."""
    items = []
    for p in conn.execute("SELECT * FROM positions WHERE book='REAL' AND status='OPEN'"
                          + (" AND account_id=?" if account_id else ""),
                          ((account_id,) if account_id else ())).fetchall():
        legs = conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN'", (p["id"],)).fetchall()
        s = p["strategy"]
        if s == "CSP":
            amt = sum((l["strike"] or 0) * 100 * (l["contracts"] or 0) for l in legs if l["leg_role"] == "SHORT_PUT")
            what = "CSP collateral"
        elif s in DIAG_FAMILY or s in ("LONG_CALL", "LONG_PUT"):
            amt = sum((l["open_price"] or 0) * (l["multiplier"] or 100) * (l["contracts"] or 0)
                      for l in legs if l["direction"] == "LONG")
            what = ("%s long at cost" % s.lower().replace("_", " "))
        elif s == "COVERED_CALL":
            continue                 # the shares are counted from stock_positions
        else:
            amt = abs(p["max_loss"] or 0)
            what = "%s max loss" % s.lower().replace("_", " ")
        items.append({"ticker": p["ticker"], "what": what, "dollars": float(amt), "position_id": p["id"]})
    if _has_table(conn, "stock_positions"):
        for r in conn.execute("SELECT * FROM stock_positions"
                              + (" WHERE account_id=?" if account_id else ""),
                              ((account_id,) if account_id else ())).fetchall():
            items.append({"ticker": r["ticker"], "what": "shares at cost",
                          "dollars": float((r["cost_basis"] or 0) * (r["shares"] or 0)), "position_id": None})
    return items


def book(account_id=None, db=None):
    conn = _conn(db)
    try:
        realized, n_legs, missing = realized_since(conn)
        share = SLEEVE_BASE + realized
        items = in_use_items(conn, account_id)
        by_name, by_sector, sectors = {}, {}, {}
        for it in items:
            t = it["ticker"]
            sec, src = sector_of(conn, t)
            it["sector"], it["sector_source"] = sec, src
            sectors[t] = sec
            by_name[t] = by_name.get(t, 0.0) + it["dollars"]
            by_sector[sec] = by_sector.get(sec, 0.0) + it["dollars"]
        used = sum(it["dollars"] for it in items)
        name_cap, sector_cap = NAME_PCT * share, SECTOR_PCT * share
        return {
            "share": share, "base": SLEEVE_BASE, "start": SLEEVE_START,
            "realized": realized, "realized_legs": n_legs, "realized_missing_price": missing,
            "basis": "gross (until the Fidelity import)",
            "used": used, "free": share - used,
            "name_cap": name_cap, "sector_cap": sector_cap,
            "by_name": by_name, "by_sector": by_sector, "sector_of_name": sectors,
            "items": items,
            "over_name": sorted(t for t, v in by_name.items() if v > name_cap + 1e-6),
            "over_sector": sorted(s for s, v in by_sector.items() if v > sector_cap + 1e-6),
            "unsectored": sorted({it["ticker"] for it in items if it["sector_source"] == "missing"}),
        }
    finally:
        conn.close()


def room_for(ticker, account_id=None, db=None, b=None):
    """Room left for one more dollar of `ticker` under each limit, and which binds."""
    b = b or book(account_id, db)
    t = (ticker or "").upper()
    conn = _conn(db)
    try:
        sec, _src = sector_of(conn, t)
    finally:
        conn.close()
    rooms = {
        SLEEVE_TOTAL: b["share"] - b["used"],
        SLEEVE_NAME: b["name_cap"] - b["by_name"].get(t, 0.0),
        SLEEVE_SECTOR: b["sector_cap"] - b["by_sector"].get(sec, 0.0),
    }
    binding = min(BINDINGS, key=lambda k: rooms[k])     # ties: total, then name, then sector
    return {"ticker": t, "sector": sec, "rooms": rooms, "binding": binding,
            "room": rooms[binding], "book": b}


_LABEL = {SLEEVE_TOTAL: "the HELM sleeve", SLEEVE_NAME: "the 20% name limit",
          SLEEVE_SECTOR: "the 30% sector limit"}


def limit_text(binding, r):
    b = r["book"]
    if binding == SLEEVE_TOTAL:
        return "the HELM sleeve has %s free (%s used of %s)" % (
            _money(max(r["rooms"][SLEEVE_TOTAL], 0)), _money(b["used"]), _money(b["share"]))
    if binding == SLEEVE_NAME:
        return "%s has %s of room under the 20%% name limit (%s used of %s)" % (
            r["ticker"], _money(max(r["rooms"][SLEEVE_NAME], 0)),
            _money(b["by_name"].get(r["ticker"], 0)), _money(b["name_cap"]))
    return "%s has %s of room under the 30%% sector limit (%s used of %s)" % (
        r["sector"], _money(max(r["rooms"][SLEEVE_SECTOR], 0)),
        _money(b["by_sector"].get(r["sector"], 0)), _money(b["sector_cap"]))


def size(strategy, ticker, per_contract, account_id=None, db=None, ceiling=20, b=None):
    """(n, binding, note) for one more trade costing `per_contract` dollars of
    sleeve per contract (CSP: strike x 100; diagonal family: the long's cost).
    n == 0 is a decline; the note names the limit and the room left."""
    if not per_contract or per_contract <= 0 or not ticker:
        return None, None, None
    r = room_for(ticker, account_id, db, b)
    n = max(int(math.floor(r["room"] / float(per_contract) + 1e-9)), 0)
    what = "secures" if (strategy or "").upper() == "CSP" else "costs"
    if n == 0:
        return 0, r["binding"], "one contract %s %s; %s" % (what, _money(per_contract), limit_text(r["binding"], r))
    if ceiling is not None and n > ceiling:
        return ceiling, "ceiling", "sized to %d by the 20-contract sanity limit (%s)" % (
            ceiling, limit_text(r["binding"], r))
    return n, r["binding"], "sized to %d by %s -- %s" % (n, _LABEL[r["binding"]], limit_text(r["binding"], r))


# ---------------------------------------------------------------- self-test
def _selftest():
    import tempfile
    fails = []

    def ok(c, m):
        if not c:
            fails.append(m); print("FAIL", m)

    d = tempfile.mkdtemp()
    db = os.path.join(d, "t.db")
    c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE positions (id TEXT, account_id TEXT, strategy TEXT, ticker TEXT, status TEXT, book TEXT, max_loss REAL);
      CREATE TABLE legs (id TEXT, position_id TEXT, leg_role TEXT, direction TEXT, strike REAL, contracts INT,
                         multiplier INT, open_price REAL, close_price REAL, close_date TEXT, status TEXT);
      CREATE TABLE watchlist (ticker TEXT, sector TEXT);
      CREATE TABLE stock_positions (account_id TEXT, ticker TEXT, shares INT, cost_basis REAL);
      CREATE TABLE approved_funds (ticker TEXT, bucket TEXT, approved_at TEXT, note TEXT);
      INSERT INTO watchlist VALUES ('AAA','Tech'),('BBB','Tech'),('CCC','Energy'),('DDD',NULL);
      INSERT INTO approved_funds VALUES ('XLK','Tech','2026-10-24',''),('SPY','US equity index','2026-10-24','');
      INSERT INTO positions VALUES ('p1','a','CSP','AAA','OPEN','REAL',NULL),
                                   ('p2','a','PMCC','CCC','OPEN','REAL',NULL),
                                   ('p3','a','CSP','BBB','OPEN','PAPER',NULL),
                                   ('p4','a','CSP','CCC','CLOSED','REAL',NULL);
      INSERT INTO legs VALUES ('l1','p1','SHORT_PUT','SHORT',500,1,100,5,NULL,NULL,'OPEN'),
                              ('l2','p2','LONG_CALL','LONG',40,2,100,100,NULL,NULL,'OPEN'),
                              ('l2s','p2','SHORT_CALL','SHORT',60,2,100,2,0.5,'2026-10-25T10:00','CLOSED'),
                              ('l3','p3','SHORT_PUT','SHORT',900,9,100,5,NULL,NULL,'OPEN'),
                              ('l4','p4','SHORT_PUT','SHORT',100,1,100,3,1,'2026-10-23T10:00','CLOSED');
    """)
    c.commit(); c.close()
    b = book(db=db)
    ok(b["realized"] == 300.0, "only legs closed from 10/24 count (short call +$300; the 10/23 close does not): %s" % b["realized"])
    ok(b["share"] == 300300.0, "share = base + realized")
    ok(b["used"] == 50000 + 20000, "in use: CSP collateral + PMCC long at cost; paper excluded: %s" % b["used"])
    ok(b["by_sector"].get("Tech") == 50000 and b["by_sector"].get("Energy") == 20000, "sectors from the watchlist")
    # name limit edge: AAA has 60,060 - 50,000 = 10,060 of room
    n, bd, note = size("CSP", "AAA", 10060, db=db, b=b)
    ok(n == 1 and bd == SLEEVE_NAME, "exactly the room fits: %s %s" % (n, bd))
    n, bd, note = size("CSP", "AAA", 10061, db=db, b=b)
    ok(n == 0 and bd == SLEEVE_NAME and "20% name limit" in note, "$1 over the name room is declined: %s" % note)
    # sector limit: Tech cap 90,090 - 50,000 = 40,090; a fund in the Tech bucket shares it
    n, bd, _ = size("CSP", "XLK", 45000, db=db, b=b)
    ok(n == 0 and bd == SLEEVE_SECTOR, "a sector fund shares its sector's limit: %s" % bd)
    n, bd, _ = size("CSP", "BBB", 40000, db=db, b=b)
    ok(n == 1 and bd == SLEEVE_SECTOR, "sector room 40,090 fits 40,000 (BBB name room 60,060)")
    # missing sector -> its own bucket
    n, bd, _ = size("CSP", "DDD", 59000, db=db, b=b)
    ok(n == 1, "a name with no sector counts as its own")
    # sleeve total
    tight = dict(b, used=b["share"] - 1000.0)
    n, bd, note = size("PMCC", "SPY", 5000, db=db, b=tight)
    ok(n == 0 and bd == SLEEVE_TOTAL and "HELM sleeve" in note, "sleeve total binds when it is the smallest")
    print("sleeve selftest:", "PASS (%d checks)" % 10 if not fails else "%d FAIL" % len(fails))
    return 1 if fails else 0


def _report(account_id=None, db=None):
    b = book(account_id, db)
    print("HELM sleeve  %s  (base %s + realized since %s %s, %s)" % (
        _money(b["share"]), _money(b["base"]), b["start"], _money(b["realized"]), b["basis"]))
    print("in use       %s   free %s" % (_money(b["used"]), _money(b["free"])))
    print("limits       one name %s (20%%)   one sector %s (30%%)" % (_money(b["name_cap"]), _money(b["sector_cap"])))
    print("\nby name")
    for t, v in sorted(b["by_name"].items(), key=lambda kv: -kv[1]):
        print("  %-6s %10s  %-24s%s" % (t, _money(v), b["sector_of_name"][t], "  OVER the name limit" if t in b["over_name"] else ""))
    print("\nby sector")
    for s, v in sorted(b["by_sector"].items(), key=lambda kv: -kv[1]):
        print("  %-26s %10s%s" % (s, _money(v), "  OVER the sector limit" if s in b["over_sector"] else ""))
    longs = [it for it in b["items"] if "long at cost" in it["what"]]
    if longs:
        print("\nlongs at cost (diagonal / PMCC)")
        for it in longs:
            print("  %-6s %10s  %s" % (it["ticker"], _money(it["dollars"]), it["what"]))
    if b["unsectored"]:
        print("\nno sector (each counted on its own):", ", ".join(b["unsectored"]))
    if b["realized_missing_price"]:
        print("\nclosed legs with no close price (not counted):", len(b["realized_missing_price"]))
    return b


def run():
    import sys
    if "--selftest" in sys.argv:
        return _selftest()
    from helm.config import get_active_account
    _report(get_active_account())


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else (_report() and 0))
