"""W175 -- backfill market_context from official closes.  Dry-run unless --apply.

Two halves, and only one of them is a one-way loss:

  * The INDEX columns are not perishable.  ^GSPC / ^VIX / ^VIX3M official
    daily closes exist for every session day the book has lived through, and
    can be reconstructed at any time, forever.
  * The CROSS-SECTION is ours and cannot be reconstructed: `signals` and
    `iv_history` only have the days the scan and the refresh actually ran.
    Where they are absent, the columns stay NULL.  Nothing is modelled,
    interpolated or inferred.

Every row written here is marked TWICE -- `data_source` carries a
`BACKFILL:` prefix, and `created_at` sits months after `as_of_date` -- and
uses INSERT OR IGNORE, so a contemporaneous row can never be overwritten.

    python3 tools/mc_backfill.py [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--apply]
"""
import os, sys, sqlite3, argparse, datetime, shutil

ROOT = os.environ.get("HELM_ROOT", os.path.expanduser("~/Projects/helm"))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data/helm.db")

from helm import market_context as mc          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    conn = sqlite3.connect(DB)
    cal = mc._calendar()

    if a.start:
        start = datetime.date.fromisoformat(a.start)
    else:
        row = conn.execute("select min(date(opened_at)) from positions").fetchone()
        start = datetime.date.fromisoformat(row[0])
    end = (datetime.date.fromisoformat(a.end) if a.end
           else mc.previous_session_day(datetime.date.today(), cal))

    days = mc.session_days_between(start, end, cal)
    missing = [d for d in days if not mc.has_row(conn, d)]
    if a.limit:
        missing = missing[-a.limit:]

    print("MODE       %s" % ("APPLY" if a.apply else "DRY RUN"))
    print("calendar   %s" % mc.calendar_name(cal))
    print("range      %s .. %s   session days %d   missing %d"
          % (start, end, len(days), len(missing)))
    if not missing:
        print("nothing to do")
        return 0

    # ONE network trip for the whole range.
    span = (end - start).days + mc.LOOKBACK_DAYS
    print("fetching   %s over %d calendar days ..." % (
        ", ".join(mc.SYMBOLS.values()), span))
    series, fetch_failures = mc.index_frame(end, lookback_days=span)
    for k, v in series.items():
        print("           %-6s %5d bars  %s .. %s"
              % (k, len(v), min(v) if v else "-", max(v) if v else "-"))
    if fetch_failures:
        print("FETCH FAILURES", fetch_failures)

    rows, stats = [], {"index_ok": 0, "breadth_ok": 0, "iv_ok": 0, "failed": 0}
    for d in missing:
        r, f = mc.build_row(conn, d, series=series, backfill=True, cal=cal,
                            fetch_failures=fetch_failures)
        rows.append(r)
        if r["vix"] is not None and r["spx_price"] is not None:
            stats["index_ok"] += 1
        if r["breadth"] is not None:
            stats["breadth_ok"] += 1
        if r["iv_rank_median"] is not None:
            stats["iv_ok"] += 1
        if f:
            stats["failed"] += 1

    print("\nwould write %d rows" % len(rows))
    print("  index columns present   %d of %d" % (stats["index_ok"], len(rows)))
    print("  breadth present         %d of %d" % (stats["breadth_ok"], len(rows)))
    print("  iv cross-section present %d of %d" % (stats["iv_ok"], len(rows)))
    print("  rows recording a source failure %d" % stats["failed"])
    print("\nfirst / last:")
    for r in (rows[0], rows[-1]):
        print("   %s vix %-6s %-9s %-10s breadth %-6s ivr %-6s n>=50 %s"
              % (r["as_of_date"], r["vix"], r["vix_regime"], r["index_trend"],
                 r["breadth"], r["iv_rank_median"], r["iv_rank_ge50"]))

    if not a.apply:
        print("\nDry run only.  Re-run with --apply.")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = DB + ".bak-w175bf-" + stamp
    shutil.copy2(DB, bak)
    print("\nBACKUP %s" % os.path.basename(bak))
    before = conn.execute("select count(*) from market_context").fetchone()[0]
    written = sum(mc.write_row(conn, r, replace=False) for r in rows)
    after = conn.execute("select count(*) from market_context").fetchone()[0]
    print("WROTE %d   rows %d -> %d" % (written, before, after))
    bf = conn.execute("select count(*) from market_context where "
                      "data_source like 'BACKFILL:%'").fetchone()[0]
    print("READBACK backfilled rows %d, contemporaneous %d" % (bf, after - bf))
    return 0


if __name__ == "__main__":
    sys.exit(main())
