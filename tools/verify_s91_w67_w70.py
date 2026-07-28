#!/usr/bin/env python3
"""Behavioural checks for W70/HELM-125 (two-scan confirmation) and
W67/HELM-121 (buy-wing paper routing + origin_screen provenance).

Behavioural, not textual: every check drives paper_generate() or
_lc_confirmed_survivors() against a sandboxed copy of the live database and
asserts on what came out. Run it BEFORE applying the patch as a control -- the
checks that describe new behaviour must fail, or they are not testing anything.

  python3 tools/verify_s91_w67_w70.py           # run
  python3 tools/verify_s91_w67_w70.py -v        # show every check

The sandbox is a VACUUM INTO snapshot (the W52 technique, used in anger for the
W13 repair), so the real schema is exercised and data/helm.db is never touched.
"""

import os
import re
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SANDBOX = Path(tempfile.mkdtemp(prefix="helm-s91-")) / "test.db"
src = sqlite3.connect(str(ROOT / "data" / "helm.db"))
src.execute("VACUUM INTO ?", (str(SANDBOX),))
src.close()

os.environ["HELM_DB"] = str(SANDBOX)
os.environ["HELM_ROOT"] = str(ROOT)

import helm.cli._paper_generate as pg          # noqa: E402
from helm.models.position import Position      # noqa: E402

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((True, name, ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc) or "assertion failed"))
    except Exception as exc:
        RESULTS.append((False, name, "%s: %s" % (type(exc).__name__, exc)))


def db():
    c = sqlite3.connect(str(SANDBOX), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _enums(conn, table):
    """column -> first allowed literal, read off the table's CHECK (col IN ...)
    constraints. Filling NOT NULL text columns with a placeholder is not enough:
    several of them are enumerations, and a rejected insert leaves the
    connection mid-transaction, which locks the file for every later check.
    That cascade is what made the first two control runs unreadable."""
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0] or ""
    out = {}
    for col, body in re.findall(r"(\w+)\s+IN\s*\(([^)]*)\)", sql, re.I):
        vals = re.findall(r"'([^']*)'", body)
        if vals:
            out[col] = vals[0]
    return out


def _required(conn, table):
    """NOT NULL columns with no default. Discovered from the schema rather than
    hardcoded, so a column added later fails loudly here instead of silently
    turning this suite into a cascade of lock errors."""
    enums = _enums(conn, table)
    out = {}
    for r in conn.execute("PRAGMA table_info(%s)" % table):
        if r["notnull"] and r["dflt_value"] is None and not r["pk"]:
            name = r["name"]
            if name in enums:
                out[name] = enums[name]
            elif r["type"].upper() in ("REAL", "INTEGER"):
                out[name] = 0
            else:
                out[name] = "x"
    return out


def wipe_signals():
    c = db()
    try:
        c.execute("DELETE FROM signals")
        c.commit()
    finally:
        c.close()


def add_batch(generated_at, rows, ratio=0.850):
    """rows: list of (ticker, lc_pass, top_strategy, russ_action).

    ratio sets signals.iv_hv90_ratio for every row in the batch. It defaults to
    0.850 -- comfortably inside the G3 gate AND inside the routing margin -- so
    a check that does not care about the vol gate is not silently blocked by it.
    Pass None to test the fails-closed path."""
    c = db()
    cols = {r[1] for r in c.execute("PRAGMA table_info(signals)")}
    for i, (ticker, lc_pass, top, action) in enumerate(rows):
        f = {
            "id": "%s-%s" % (generated_at[-8:].replace(":", ""), ticker),
            "ticker": ticker,
            "generated_at": generated_at,
            "created_at": generated_at,
            "lc_screen_pass": 1 if lc_pass else 0,
            "iv_hv90_ratio": ratio,
            "lc_screen_rank": i + 1 if lc_pass else None,
            "spot_price": 100.0,
            "top_strategy": top,
            "russ_action": action,
        }
        base = _required(c, "signals")
        base.update(f)
        if ratio is None:
            base.pop("iv_hv90_ratio", None)   # leave it NULL, do not backfill
        f = {k: v for k, v in base.items() if k in cols}
        try:
            c.execute(
                "INSERT INTO signals (%s) VALUES (%s)"
                % (",".join(f), ",".join("?" * len(f))),
                tuple(f.values()),
            )
        except Exception:
            c.close()
            raise
    c.commit()
    c.close()


def clear_positions():
    c = db()
    try:
        c.execute("DELETE FROM positions")
        c.commit()
    finally:
        c.close()


def add_position(ticker, strategy, book, status="OPEN"):
    c = db()
    f = _required(c, "positions")
    f.update({"id": "%s-%s-%s" % (ticker, strategy, book), "ticker": ticker,
              "strategy": strategy, "book": book, "status": status,
              "account_id": (c.execute("SELECT id FROM accounts LIMIT 1").fetchone() or ["a"])[0]})
    try:
        c.execute("INSERT INTO positions (%s) VALUES (%s)"
                  % (",".join(f), ",".join("?" * len(f))), tuple(f.values()))
        c.commit()
    finally:
        c.close()


BOOKED = []


def fake_booker(ticker, strategy, spot, scan_data=None, **kw):
    """Stands in for the real chain-dependent bookers. Inserts a positions row
    so the origin_screen UPDATE has something to hit, and records the call."""
    pid = "TEST-%s-%s-%d" % (ticker, strategy, len(BOOKED))
    c = db()
    f = _required(c, "positions")
    f.update({"id": pid, "ticker": ticker, "strategy": strategy,
              "book": "PAPER", "status": "OPEN",
              "account_id": (c.execute("SELECT id FROM accounts LIMIT 1").fetchone() or ["a"])[0]})
    try:
        c.execute("INSERT INTO positions (%s) VALUES (%s)"
                  % (",".join(f), ",".join("?" * len(f))), tuple(f.values()))
        c.commit()
    finally:
        c.close()
    BOOKED.append((ticker, strategy, pid))
    return pid


def arm():
    """Market open, every strategy paperable, no real chain needed."""
    BOOKED.clear()
    pg.is_market_open = lambda: True
    for k in list(pg._PAPER_BOOKERS):
        pg._PAPER_BOOKERS[k] = fake_booker
    pg._PAPER_BOOKERS.setdefault("LONG_CALL", fake_booker)
    pg.paperable_strategies = lambda: set(pg._PAPER_BOOKERS)
    # backfill_entry_vol (HELM-081) reaches yfinance for hv_30d and skew. In a
    # sandbox with no live IBKR connection that is a network round trip per
    # booking and the suite hangs. It is imported inside the booking function,
    # so the stub has to land on the module it is imported from.
    import helm.vol_context
    helm.vol_context.backfill_entry_vol = lambda *a, **k: None
    # backfill_entry_vol and the console are harmless; leave them alone.


def origin_of(pos_id):
    c = db()
    r = c.execute("SELECT origin_screen FROM positions WHERE id=?", (pos_id,)).fetchone()
    c.close()
    return r["origin_screen"] if r else None


# --------------------------------------------------------------- W70 checks

def t_route_margin_exists():
    from helm.lc_screen import ROUTE_MARGIN, G3_RATIO_MAX
    assert ROUTE_MARGIN > 0, "a margin of 0 is the knife-edge gate again"
    assert ROUTE_MARGIN < G3_RATIO_MAX, "margin must be smaller than the gate itself"


def t_confirmation_rule_is_gone():
    """s91 shipped CONFIRM_SCANS and s91c replaced it. The constant must not
    linger as a live binding -- a dead knob that looks configurable is worse
    than none (W54's shape)."""
    txt = (ROOT / "helm" / "lc_screen.py").read_text()
    assert chr(10) + "CONFIRM_SCANS" not in txt, "the CONFIRM_SCANS binding survives"
    pgtxt = (ROOT / "helm" / "cli" / "_paper_generate.py").read_text()
    assert "_lc_confirmed_survivors" not in pgtxt, "old helper name still referenced"


def t_lc_screen_stays_db_free():
    """The screen must not learn about the database. W66 made it a pure
    function of a scan row; the routing test belongs to the router."""
    txt = (ROOT / "helm" / "lc_screen.py").read_text()
    for bad in ("import sqlite3", "get_conn", "helm.db"):
        assert bad not in txt, "lc_screen.py must stay DB-free, found %r" % bad


def t_name_on_the_line_does_not_route():
    """GE's real case: 0.899 against a 0.900 gate. It passes the screen and is
    published as a pass -- and it must not book."""
    wipe_signals()
    add_batch("2026-07-27T15:24:00", [("GE", True, None, None)], ratio=0.899)
    out = [s["ticker"] for s in pg._lc_routable_survivors()]
    assert out == [], "a name 0.001 inside the gate must not route, got %s" % out


def t_name_clearly_inside_routes():
    wipe_signals()
    add_batch("2026-07-27T15:24:00", [("NOW", True, None, None)], ratio=0.856)
    out = [s["ticker"] for s in pg._lc_routable_survivors()]
    assert out == ["NOW"], "a name well inside the gate must route, got %s" % out


def t_margin_boundary_is_inclusive():
    """At exactly gate - margin the name routes. Stated so the boundary is a
    decision rather than an accident of <~ versus <."""
    from helm.lc_screen import G3_RATIO_MAX, ROUTE_MARGIN
    wipe_signals()
    add_batch("2026-07-27T15:24:00", [("XX", True, None, None)],
              ratio=round(G3_RATIO_MAX - ROUTE_MARGIN, 6))
    out = [s["ticker"] for s in pg._lc_routable_survivors()]
    assert out == ["XX"], "exactly at gate-margin must route, got %s" % out


def t_failing_the_screen_never_routes():
    """The margin is additional to the screen, not a replacement for it. A
    name with a beautiful ratio that fails G1/G4/G5 must still not book."""
    wipe_signals()
    add_batch("2026-07-27T15:24:00", [("AMD", False, None, None)], ratio=0.500)
    out = [s["ticker"] for s in pg._lc_routable_survivors()]
    assert out == [], "a screen failure must not route on ratio alone, got %s" % out


def t_missing_ratio_fails_closed():
    """No stored ratio, no routing. The screen cannot have passed a name on G3
    without one, so a NULL means something upstream is wrong."""
    wipe_signals()
    add_batch("2026-07-27T15:24:00", [("GE", True, None, None)], ratio=None)
    out = [s["ticker"] for s in pg._lc_routable_survivors()]
    assert out == [], "a NULL ratio must fail closed, got %s" % out


def t_board_verdict_is_not_narrowed():
    """The margin gates ROUTING only. A name between gate-margin and gate must
    still be published as a G3 pass -- the board keeps telling the truth about
    what the screen thinks, and only the money waits. Asserted against the real
    screen, not against a stored flag, so a change to G3 itself is caught here."""
    from helm.lc_screen import evaluate_gates, G3_RATIO_MAX, ROUTE_MARGIN
    ratio = 0.899
    assert G3_RATIO_MAX - ROUTE_MARGIN < ratio <= G3_RATIO_MAX, \
        "fixture must sit inside the gate but inside the margin"
    gates = evaluate_gates({"iv_hv90_ratio": ratio, "hv_90_ex_earn": 30.0})
    g3 = gates.get("g3") if isinstance(gates, dict) else None
    if g3 is None and isinstance(gates, tuple):
        for part in gates:
            if isinstance(part, dict) and "g3" in part:
                g3 = part["g3"]
    assert g3 is not None, "could not locate the g3 verdict in evaluate_gates output"
    assert g3.get("ok") is True, \
        "the board must still publish G3 pass at %s, got %r" % (ratio, g3.get("ok"))


def t_taken_real_is_excluded():
    wipe_signals()
    add_batch("2026-07-27T15:24:00", [("GE", True, None, "OPEN")], ratio=0.850)
    out = [s["ticker"] for s in pg._lc_routable_survivors()]
    assert out == [], "a survivor taken real must not also book to paper, got %s" % out


def t_no_screened_batch_routes_nothing():
    wipe_signals()
    out = pg._lc_routable_survivors()
    assert out == [], "no screened batch must route nothing, got %s" % out


# --------------------------------------------------------------- W67 checks

def _two_confirming_batches(rows_latest, rows_prior=None):
    """Kept under its original name so the W67 checks read unchanged, but it is
    now a single batch: s91c replaced the two-scan confirmation with a margin,
    so a second batch confirms nothing. The ratio default puts every name inside
    the routing margin, which is what these checks assume."""
    wipe_signals()
    add_batch("2026-07-27T15:19:00", rows_latest)


def t_confirmed_survivor_books_long_call():
    arm(); clear_positions()
    _two_confirming_batches([("GE", True, None, None)])
    pg.paper_generate()
    assert ("GE", "LONG_CALL") in [(t, s) for t, s, _ in BOOKED], \
        "a confirmed survivor must book a LONG_CALL, booked %s" % BOOKED


def t_buy_booking_is_stamped_lc_screen():
    arm(); clear_positions()
    _two_confirming_batches([("GE", True, None, None)])
    pg.paper_generate()
    pid = [p for t, s, p in BOOKED if t == "GE" and s == "LONG_CALL"][0]
    got = origin_of(pid)
    assert got == "LC_SCREEN", "buy-wing position must read LC_SCREEN, got %r" % got


def t_sell_booking_is_stamped_sell_screen():
    arm(); clear_positions()
    _two_confirming_batches([("BAC", False, "CSP", None)])
    pg.paper_generate()
    pid = [p for t, s, p in BOOKED if t == "BAC" and s == "CSP"][0]
    got = origin_of(pid)
    assert got == "SELL_SCREEN", "sell-wing position must read SELL_SCREEN, got %r" % got


def t_no_position_is_left_unattributed():
    arm(); clear_positions()
    _two_confirming_batches([("GE", True, None, None), ("BAC", False, "CSP", None)])
    pg.paper_generate()
    bad = [p for _, _, p in BOOKED if origin_of(p) is None]
    assert not bad, "every booked position must carry an origin_screen, NULL on %s" % bad


def t_real_book_does_not_block_the_buy_pass():
    """W75 / HELM-130: reversed deliberately. This check used to assert the
    opposite. Russ's call -- a real options position must not stop the paper
    book taking its own view on the same underlying. GE held real as a CSP is
    the exact case that surfaced it: short premium real, long call on paper."""
    arm(); clear_positions()
    add_position("GE", "CSP", "REAL")
    _two_confirming_batches([("GE", True, None, None)])
    pg.paper_generate()
    assert ("GE", "LONG_CALL") in [(t, s) for t, s, _ in BOOKED], \
        "a real position must not block the paper buy pass, booked %s" % BOOKED


def t_real_book_does_not_block_the_sell_pass():
    """Same rule, other wing -- applied to both by Russ's call, so both are
    asserted. One wing silently keeping the old rule is the W13 shape."""
    arm(); clear_positions()
    add_position("BAC", "CSP", "REAL")
    _two_confirming_batches([("BAC", False, "CSP", None)])
    pg.paper_generate()
    assert ("BAC", "CSP") in [(t, s) for t, s, _ in BOOKED], \
        "a real position must not block the paper sell pass, booked %s" % BOOKED


def t_paper_still_does_not_stack_a_duplicate():
    """The guard that stays. Removing deference to the REAL book must not also
    remove paper's guard against duplicating itself."""
    arm(); clear_positions()
    add_position("GE", "LONG_CALL", "PAPER")
    _two_confirming_batches([("GE", True, None, None)])
    pg.paper_generate()
    assert not [t for t, s, _ in BOOKED if t == "GE" and s == "LONG_CALL"], \
        "paper must still refuse to stack a duplicate, booked %s" % BOOKED


def t_already_open_in_paper_blocks_the_buy_pass():
    arm(); clear_positions()
    add_position("GE", "LONG_CALL", "PAPER")
    _two_confirming_batches([("GE", True, None, None)])
    pg.paper_generate()
    assert not [t for t, s, _ in BOOKED if t == "GE" and s == "LONG_CALL"], \
        "must not double-book a name already open in paper, booked %s" % BOOKED


def t_sell_route_is_not_overwritten():
    """The dual-book A/B: a name can be a sell candidate AND a buy survivor on
    the same scan, and both routes must survive. Writing top_strategy would
    make this impossible to detect."""
    arm(); clear_positions()
    _two_confirming_batches([("GE", True, "CSP", None)])
    pg.paper_generate()
    pairs = [(t, s) for t, s, _ in BOOKED]
    assert ("GE", "CSP") in pairs, "the sell route must still book, got %s" % pairs
    assert ("GE", "LONG_CALL") in pairs, "the buy route must also book, got %s" % pairs
    c = db()
    row = c.execute("SELECT top_strategy FROM signals WHERE ticker='GE' "
                    "ORDER BY generated_at DESC LIMIT 1").fetchone()
    c.close()
    assert row["top_strategy"] == "CSP", \
        "the buy pass must not overwrite top_strategy, now %r" % row["top_strategy"]


def t_summary_reports_both_fields_separately():
    arm(); clear_positions()
    _two_confirming_batches([("GE", True, None, None), ("BAC", False, "CSP", None)])
    out = pg.paper_generate()
    assert "lc_field" in out, "the summary must report the buy field, got %s" % sorted(out)
    assert out["lc_field"] == 1, "lc_field should be 1, got %r" % out["lc_field"]
    assert out["field"] != out["lc_field"] + out["field"], "fields must not be summed"


def t_market_closed_books_nothing():
    arm(); clear_positions()
    pg.is_market_open = lambda: False
    _two_confirming_batches([("GE", True, None, None)])
    pg.paper_generate()
    assert BOOKED == [], "market closed must book nothing, booked %s" % BOOKED
    pg.is_market_open = lambda: True


def t_booker_failure_does_not_kill_the_batch():
    arm(); clear_positions()

    def boom(ticker, strategy, spot, scan_data=None, **kw):
        if ticker == "GE":
            raise RuntimeError("no chain")
        return fake_booker(ticker, strategy, spot, scan_data, **kw)

    for k in list(pg._PAPER_BOOKERS):
        pg._PAPER_BOOKERS[k] = boom
    _two_confirming_batches([("GE", True, None, None), ("BAC", False, "CSP", None)])
    out = pg.paper_generate()
    assert ("BAC", "CSP") in [(t, s) for t, s, _ in BOOKED], \
        "one failing ticker must not kill the batch, booked %s" % BOOKED
    assert any("no chain" in str(r) for _, _, r in out["skipped"]), \
        "the failure must surface as a skip reason, got %s" % out["skipped"]


def t_live_db_untouched():
    """The sandbox must be the only thing written. If HELM_DB were ignored,
    every check above would have been booking into the real paper book."""
    assert os.environ["HELM_DB"] == str(SANDBOX)
    from helm.db import get_conn
    c = get_conn()
    try:
        path = [r for r in c.execute("PRAGMA database_list")][0][2]
    finally:
        c.close()
    assert str(SANDBOX) in str(path), \
        "get_conn resolved to %r, not the sandbox -- checks were not isolated" % path


CHECKS = [
    ("W70  ROUTE_MARGIN exists and is sane", t_route_margin_exists),
    ("W70  the confirmation rule is gone", t_confirmation_rule_is_gone),
    ("W70  lc_screen stays DB-free", t_lc_screen_stays_db_free),
    ("W70  a name on the line does not route", t_name_on_the_line_does_not_route),
    ("W70  a name clearly inside routes", t_name_clearly_inside_routes),
    ("W70  the margin boundary is inclusive", t_margin_boundary_is_inclusive),
    ("W70  failing the screen never routes", t_failing_the_screen_never_routes),
    ("W70  a missing ratio fails closed", t_missing_ratio_fails_closed),
    ("W70  the board verdict is not narrowed", t_board_verdict_is_not_narrowed),
    ("W70  survivor taken real is excluded", t_taken_real_is_excluded),
    ("W70  no screened batch routes nothing", t_no_screened_batch_routes_nothing),
    ("W67  confirmed survivor books a LONG_CALL", t_confirmed_survivor_books_long_call),
    ("W67  buy booking stamped LC_SCREEN", t_buy_booking_is_stamped_lc_screen),
    ("W67  sell booking stamped SELL_SCREEN", t_sell_booking_is_stamped_sell_screen),
    ("W67  no position left unattributed", t_no_position_is_left_unattributed),
    ("W75  real book does not block the buy pass", t_real_book_does_not_block_the_buy_pass),
    ("W75  real book does not block the sell pass", t_real_book_does_not_block_the_sell_pass),
    ("W75  paper still refuses a duplicate", t_paper_still_does_not_stack_a_duplicate),
    ("W67  already open in paper blocks the buy pass", t_already_open_in_paper_blocks_the_buy_pass),
    ("W67  sell route is not overwritten", t_sell_route_is_not_overwritten),
    ("W67  summary reports both fields separately", t_summary_reports_both_fields_separately),
    ("W67  market closed books nothing", t_market_closed_books_nothing),
    ("W67  booker failure does not kill the batch", t_booker_failure_does_not_kill_the_batch),
    ("ENV  live database untouched", t_live_db_untouched),
]


def main():
    verbose = "-v" in sys.argv
    import io
    import contextlib
    for name, fn in CHECKS:
        buf = io.StringIO()
        print('  ..', name, file=sys.stderr, flush=True)
        with contextlib.redirect_stdout(buf):
            check(name, fn)

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    for ok, name, msg in RESULTS:
        if ok and not verbose:
            continue
        print("  [%s] %-48s %s" % ("ok" if ok else "XX", name, msg))
    print()
    print("  %d checks, %d passed, %d failed" % (len(RESULTS), passed, failed))
    print("  sandbox: %s" % SANDBOX)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
