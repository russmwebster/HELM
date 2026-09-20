"""W175 -- both-ways verification for helm/market_context.py.

Read-only against the live book: every case runs on a fresh `VACUUM INTO`
copy, and EVERY quote is faked, so a difference is the code and not the tape.
The unperturbed control runs LAST.

Usage:
    python3 tools/verify_market_context.py            # the suite
    python3 tools/verify_market_context.py --perturb bar_date
    python3 tools/verify_market_context.py --perturb dedupe
    python3 tools/verify_market_context.py --perturb empty_ok

A perturbation run is EXPECTED to fail; it proves the suite can fail on the
defect it was written for.  Restore and re-run to confirm PASS.
"""
import os, sys, sqlite3, json, datetime, argparse

ROOT = os.environ.get("HELM_ROOT", os.path.expanduser("~/Projects/helm"))
sys.path.insert(0, ROOT)
LIVE = os.path.join(ROOT, "data/helm.db")

from helm import market_context as mc          # noqa: E402

PASS = FAIL = 0
FAILED = []


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s: got %r want %r" % (name, got, want))


def check_in(name, needle, hay):
    global PASS, FAIL
    if needle in (hay or ""):
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s: %r not in %r" % (name, needle, (hay or "")[:120]))


# --- fixtures ---------------------------------------------------------------

TARGET = datetime.date(2026, 9, 18)


def bars(target, n, value_fn, skip_last=0):
    """n synthetic daily bars ending at `target` (weekdays only)."""
    out, d, made = {}, target, 0
    while made < n:
        if d.weekday() < 5:
            out[d] = value_fn(made)
            made += 1
        d -= datetime.timedelta(days=1)
    if skip_last:
        for _ in range(skip_last):
            out.pop(max(out))
    return out


def series(spx_last=100.0, spx_slope=0.0, vix=16.0, vix3m=18.0, n=260,
           skip_spx=0, skip_vix=0, drop=()):
    """`drop` names whole symbols that come back empty."""
    s = {
        "spx": bars(TARGET, n, lambda i: spx_last - spx_slope * i, skip_spx),
        "vix": bars(TARGET, n, lambda i: vix, skip_vix),
        "vix3m": bars(TARGET, n, lambda i: vix3m, 0),
    }
    for k in drop:
        s[k] = {}
    return s


def fresh_copy(suffix):
    path = "/tmp/mc_verify_%s.db" % suffix
    if os.path.exists(path):
        os.remove(path)
    src = sqlite3.connect("file:" + LIVE + "?mode=ro", uri=True)
    src.execute("VACUUM INTO ?", (path,))
    src.close()
    c = sqlite3.connect(path)
    cols = [r[1] for r in c.execute("PRAGMA table_info(market_context)")]
    if "iv_rank_median" not in cols:
        c.execute("ALTER TABLE market_context ADD COLUMN iv_rank_median REAL")
        c.execute("ALTER TABLE market_context ADD COLUMN iv_rank_ge50 INTEGER")
        c.commit()
    return c


# --- the loaded-code probe --------------------------------------------------

def probe():
    """Say WHICH code ran.  The VM cannot delete __pycache__ (s115), so a
    PASS from a module you cannot name is a claim about nothing."""
    import inspect
    src = inspect.getsource(mc.index_reading)
    print("LOADED  %s" % mc.__file__)
    print("PROBE   bar-date assertion present: %s"
          % ("no bar for" in src))
    print("PROBE   band set: %s" % mc.VIX_BAND_SET)
    print("PROBE   cross-section floor: %d" % mc.MIN_CROSS_SECTION)
    return "no bar for" in src


# --- cases ------------------------------------------------------------------

def case_trend_states():
    c = fresh_copy("trend")
    # above both -> UPTREND ; rising series means older bars are lower
    r, _ = mc.build_row(c, TARGET, series=series(100.0, 0.10))
    check("trend/UPTREND", r["index_trend"], "UPTREND")
    check("trend/UP vs50", r["spx_vs_sma50"], "ABOVE")
    # falling series -> today's price below both averages
    r, _ = mc.build_row(c, TARGET, series=series(100.0, -0.10))
    check("trend/DOWNTREND", r["index_trend"], "DOWNTREND")
    check("trend/DOWN vs200", r["spx_vs_sma200"], "BELOW")
    # above the 50 and below the 200.  The SMAs are CONSTRUCTED rather than
    # hoped for: 50 flat bars at 100 (today 101) under 150 older bars at 200
    # gives sma50 100.02 and sma200 175.0.
    def stepped(recent, today, older, n_recent=50, n=260):
        out, d, made = {}, TARGET, 0
        while made < n:
            if d.weekday() < 5:
                out[d] = today if made == 0 else (recent if made < n_recent else older)
                made += 1
            d -= datetime.timedelta(days=1)
        return out

    s = series()
    s["spx"] = stepped(100.0, 101.0, 200.0)
    r, _ = mc.build_row(c, TARGET, series=s)
    check("trend/RECOVERING", r["index_trend"], "RECOVERING")
    check("trend/REC above50", r["spx_vs_sma50"], "ABOVE")
    check("trend/REC below200", r["spx_vs_sma200"], "BELOW")

    # below the 50 and above the 200
    s2 = series()
    s2["spx"] = stepped(100.0, 99.0, 50.0)
    r, _ = mc.build_row(c, TARGET, series=s2)
    check("trend/PULLBACK", r["index_trend"], "PULLBACK")
    check("trend/PB below50", r["spx_vs_sma50"], "BELOW")
    check("trend/PB above200", r["spx_vs_sma200"], "ABOVE")
    c.close()


def case_bands():
    c = fresh_copy("bands")
    for v, want in ((14.99, "CALM"), (15.0, "NORMAL"), (19.99, "NORMAL"),
                    (20.0, "ELEVATED"), (29.99, "ELEVATED"), (30.0, "STRESSED")):
        r, _ = mc.build_row(c, TARGET, series=series(vix=v))
        check("band/%s" % v, r["vix_regime"], want)
    c.close()


def case_term_structure():
    c = fresh_copy("ts")
    r, _ = mc.build_row(c, TARGET, series=series(vix=16.0, vix3m=18.0))
    check("ts/contango label", r["term_structure"], "CONTANGO")
    check("ts/contango value", r["term_structure_value"], 2.0)
    r, _ = mc.build_row(c, TARGET, series=series(vix=30.0, vix3m=25.0))
    check("ts/backwardation", r["term_structure"], "BACKWARDATION")
    check("ts/backwardation value", r["term_structure_value"], -5.0)
    c.close()


def case_empty_frame():
    """THE SILENT ONE: yfinance returns an empty frame and raises nothing.
    A row must still be written, with NULLs and a named reason."""
    c = fresh_copy("empty")
    r, f = mc.build_row(c, TARGET, series=series(drop=("spx", "vix", "vix3m")))
    check("empty/vix null", r["vix"], None)
    check("empty/spx null", r["spx_price"], None)
    check("empty/trend null", r["index_trend"], None)
    check_in("empty/names the failure", "no data", r["data_source"])
    check_in("empty/failure in notes", "failed", r["notes"])
    n = mc.write_row(c, r, replace=True)
    check("empty/row IS written", n, 1)
    check("empty/row exists", mc.has_row(c, TARGET), True)
    # the cross-section is independent of the index fetch and must survive it
    check("empty/breadth survives the index failure", r["breadth"] is not None, True)
    check("empty/iv survives", r["iv_rank_median"] is not None, True)
    c.close()


def case_silent_fetch():
    """The wording of the SILENT failure, on the path that produces it:
    index_frame asking a fetcher that hands back an empty dict and raises
    nothing -- which is exactly what yfinance does for a symbol it cannot
    serve (proven 2026-09-19 against a junk symbol)."""
    c = fresh_copy("silent")

    def empty_fetcher(sym, start, end):
        return {}

    r, f = mc.build_row(c, TARGET, fetcher=empty_fetcher)
    check_in("silent/names it", "empty frame, no exception", r["data_source"])
    check("silent/vix null", r["vix"], None)
    check("silent/row still written", mc.write_row(c, r, replace=True), 1)

    def raising_fetcher(sym, start, end):
        raise ConnectionError("boom")

    r2, f2 = mc.build_row(c, TARGET, fetcher=raising_fetcher)
    check_in("silent/raise recorded", "fetch raised ConnectionError", r2["data_source"])
    check("silent/raise still writes", mc.write_row(c, r2, replace=True), 1)
    c.close()


def case_stale_frame():
    """THE DANGEROUS ONE: a NON-empty frame whose last bar is not the day
    being described.  It must never be taken as that day's close."""
    c = fresh_copy("stale")
    r, f = mc.build_row(c, TARGET, series=series(skip_spx=1, skip_vix=1))
    check("stale/vix null", r["vix"], None)
    check("stale/spx null", r["spx_price"], None)
    check("stale/trend null", r["index_trend"], None)
    check_in("stale/names the failure", "no bar for 2026-09-18", r["data_source"])
    c.close()


def case_partial_fetch():
    """One symbol missing must not take the others down with it."""
    c = fresh_copy("partial")
    r, f = mc.build_row(c, TARGET, series=series(drop=("vix3m",)))
    check("partial/vix kept", r["vix"], 16.0)
    check("partial/trend kept", r["index_trend"] is not None, True)
    check("partial/ts null", r["term_structure_value"], None)
    check_in("partial/names it", "vix3m", r["data_source"])
    c.close()


def case_double_scan():
    """2026-09-16 and 2026-09-11 carry 212 signals rows, not 106.  A per-day
    aggregate that does not dedupe doubles its own denominator."""
    c = fresh_copy("dedupe")
    day = "2026-09-16"
    raw = c.execute("select count(*) from signals where date(generated_at)=?",
                    (day,)).fetchone()[0]
    tickers = c.execute("select count(distinct ticker) from signals "
                        "where date(generated_at)=?", (day,)).fetchone()[0]
    xs, _ = mc.cross_section(c, datetime.date(2026, 9, 16))
    check("dedupe/raw rows are doubled", raw > tickers, True)
    check("dedupe/counts distinct tickers", xs["n_scanned"], tickers)
    c.close()


def case_cross_section_floor():
    """A five-name scan is not a breadth reading.

    The row is CLONED from a real one rather than invented: the first draft
    hand-built an INSERT and died on `signals.confirmed_bias NOT NULL` -- the
    harness did not know the table it was writing to.
    """
    c = fresh_copy("floor")
    c.row_factory = sqlite3.Row
    src = c.execute("select * from signals where date(generated_at)='2026-09-18'"
                    " limit 1").fetchone()
    cols = src.keys()
    d = "2026-02-02"
    c.execute("delete from signals where date(generated_at)=?", (d,))
    for i in range(5):
        vals = []
        for k in cols:
            if k == "id":
                vals.append("floorfix-%d" % i)
            elif k == "ticker":
                vals.append("TT%d" % i)
            elif k == "generated_at":
                vals.append(d + "T10:00:00")
            elif k == "spot_price":
                vals.append(10.0)
            elif k == "sma_50":
                vals.append(9.0)
            elif k == "sma_200":
                vals.append(8.0)
            else:
                vals.append(src[k])
        c.execute("insert into signals (%s) values (%s)"
                  % (", ".join(cols), ", ".join("?" * len(cols))), vals)
    c.commit()
    xs, f = mc.cross_section(c, datetime.date(2026, 2, 2))
    check("floor/breadth null", xs["breadth"], None)
    check("floor/count still recorded", xs["n_scanned"], 5)
    check("floor/says why", any("floor" in x for x in f), True)
    c.close()


def case_idempotent_and_backfill():
    c = fresh_copy("idem")
    r, _ = mc.build_row(c, TARGET, series=series())
    mc.write_row(c, r, replace=True)
    mc.write_row(c, r, replace=True)
    n = c.execute("select count(*) from market_context where as_of_date=?",
                  (TARGET.isoformat(),)).fetchone()[0]
    check("idem/one row per day", n, 1)

    # a backfill must NEVER overwrite a contemporaneous row
    rb, _ = mc.build_row(c, TARGET, series=series(vix=99.0), backfill=True)
    wrote = mc.write_row(c, rb, replace=False)
    check("backfill/ignored", wrote, 0)
    src = c.execute("select data_source, vix from market_context where "
                    "as_of_date=?", (TARGET.isoformat(),)).fetchone()
    check("backfill/original kept", src[1], 16.0)
    check("backfill/no BACKFILL prefix on the survivor",
          src[0].startswith("BACKFILL:"), False)

    # but it DOES write a day that has none, and marks itself.
    #
    # The case CLEARS the date first.  It used to assume 2026-09-17 was a gap
    # on the live book; the 2026-09-20 backfill filled it, and the assertion
    # flipped to FAIL while the code was behaving exactly as designed --
    # INSERT OR IGNORE declining to overwrite a row that now exists.  A
    # fixture that depends on live state breaks when the DATA moves rather
    # than when the CODE does, which is the one thing a regression test must
    # never do.  (The refusal itself is covered by `backfill/ignored` above.)
    d2 = datetime.date(2026, 9, 17)
    c.execute("delete from market_context where as_of_date = ?",
              (d2.isoformat(),))
    c.commit()
    rb2, _ = mc.build_row(c, d2, series=series(), backfill=True)
    check("backfill/writes a gap", mc.write_row(c, rb2, replace=False), 1)
    got = c.execute("select data_source from market_context where as_of_date=?",
                    (d2.isoformat(),)).fetchone()[0]
    check("backfill/marks itself", got.startswith("BACKFILL:"), True)
    c.close()


def case_session_days():
    cal = mc._calendar()
    check("cal/named", mc.calendar_name(cal) in ("XNYS", "weekday"), True)
    # Monday looks back to Friday
    check("cal/mon->fri",
          mc.previous_session_day(datetime.date(2026, 9, 21), cal),
          datetime.date(2026, 9, 18))
    # Saturday looks back to Friday
    check("cal/sat->fri",
          mc.previous_session_day(datetime.date(2026, 9, 19), cal),
          datetime.date(2026, 9, 18))
    if mc.calendar_name(cal) == "XNYS":
        # 2026-09-07 was Labor Day; the day after looks past it
        check("cal/skips Labor Day",
              mc.previous_session_day(datetime.date(2026, 9, 8), cal),
              datetime.date(2026, 9, 4))


def case_control_live_shape():
    """UNPERTURBED CONTROL, RUN LAST -- the real cross-section on the real
    book for a day we already know the answer for, from an independent hand
    query in the same session."""
    c = fresh_copy("control")
    xs, f = mc.cross_section(c, datetime.date(2026, 9, 18))
    check("control/n scanned", xs["n_scanned"], 107)
    check("control/n iv", xs["n_iv"], 107)
    check("control/median IVR", xs["iv_rank_median"], 35.5)
    check("control/IVR ge50", xs["iv_rank_ge50"], 13)
    check("control/no failures", f, [])
    r, rf = mc.build_row(c, datetime.date(2026, 9, 18), series=series())
    check("control/row complete", rf, [])
    check("control/id is the date", r["id"], "2026-09-18")
    check("control/notes parse", json.loads(r["notes"])["n_scanned"], 107)
    c.close()


CASES = [case_session_days, case_trend_states, case_bands,
         case_term_structure, case_partial_fetch, case_stale_frame,
         case_empty_frame, case_silent_fetch, case_double_scan,
         case_cross_section_floor,
         case_idempotent_and_backfill,
         case_control_live_shape]          # control LAST


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perturb", choices=("bar_date", "dedupe", "empty_ok"))
    a = ap.parse_args()

    ok = probe()
    if not ok:
        print("PROBE FAILED -- the loaded module has no bar-date assertion.")

    if a.perturb == "bar_date":
        # Blunt the assertion that the bar IS the target's bar: take the last
        # bar whatever its date.  This is the defect that writes a WRONG
        # number rather than a missing one.
        orig = mc.index_reading

        def blunted(series_, target):
            patched = {}
            for k, v in series_.items():
                if v and target not in v:
                    v = dict(v)
                    v[target] = v[max(v)]
                patched[k] = v
            return orig(patched, target)
        mc.index_reading = blunted
        print("PERTURBED: bar-date assertion blunted")
    elif a.perturb == "dedupe":
        orig_cs = mc.cross_section

        def nodedupe(conn, target):
            d = target.isoformat()
            rows = conn.execute(
                "SELECT spot_price, sma_50, sma_200 FROM signals "
                "WHERE date(generated_at)=?", (d,)).fetchall()
            r, f = orig_cs(conn, target)
            r["n_scanned"] = len(rows)
            return r, f
        mc.cross_section = nodedupe
        print("PERTURBED: per-day dedupe removed")
    elif a.perturb == "empty_ok":
        orig_w = mc.write_row

        def skip_on_failure(conn, row, replace=False):
            if "FAILED[" in (row["data_source"] or ""):
                return 0                      # the "just skip the day" defect
            return orig_w(conn, row, replace)
        mc.write_row = skip_on_failure
        print("PERTURBED: a failed day is skipped instead of recorded")

    for fn in CASES:
        try:
            fn()
        except Exception as e:
            global FAIL
            FAIL += 1
            FAILED.append("%s RAISED %s: %s" % (fn.__name__, type(e).__name__, e))

    print("\nPASS %d - FAIL %d" % (PASS, FAIL))
    for f in FAILED:
        print("  FAIL  " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
