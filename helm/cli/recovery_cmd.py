"""helm recovery -- "if assigned" odds for each open put (s154, Russ 2026-11-08).

If a put is assigned at today's price: how soon HELM could sell a covered call, the rent the calls would
earn, and the odds the trade gets back to its effective basis -- on price alone and with the calls. A model,
not a forecast (helm/recovery.py says what it assumes). Each put's card shows the latest recorded run.

    cd ~/Projects/helm && helm recovery              every open put
    cd ~/Projects/helm && helm recovery CSCO,DELL    named puts only
    helm recovery --last                             the latest recorded results, no new run
    helm recovery --paths 4000                       fewer simulated paths (faster, a little noisier)
    helm recovery --no-record                        run and show, record nothing
    helm recovery --force                            run inside a snapshot window anyway
    helm recovery --selftest

Reads a year of daily closes from IBKR (the chain reader's client number, one connection, released), on top
of the hand-refreshed price files; without IB Gateway it uses the files alone and says so. About 20 seconds
a put. Never places or books anything.
"""
import sys
from datetime import date, datetime

from rich.console import Console

console = Console()
WINDOWS = (("10:00", "10:35"), ("12:30", "13:05"), ("15:15", "15:50"))   # as helm puts


def _in_window(now=None):
    hm = (now or datetime.now()).strftime("%H:%M")
    for a, b in WINDOWS:
        if a <= hm < b:
            return "%s-%s" % (a, b)
    return None


def _open_puts(conn, tickers=None):
    rows = conn.execute("SELECT p.id, p.ticker FROM positions p JOIN legs l ON l.position_id = p.id "
                        "WHERE p.book='REAL' AND p.status='OPEN' AND p.strategy='CSP' AND l.status='OPEN' "
                        "AND l.direction='SHORT' AND l.option_type='PUT' ORDER BY p.ticker, p.id").fetchall()
    return [(r["id"], r["ticker"]) for r in rows if not tickers or r["ticker"] in tickers]


def _bars(tickers):
    """{ticker: [(date, close)]} from IBKR, and a note; {} with the reason when IB Gateway can't be reached."""
    from helm import pmcctool as PM
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect("127.0.0.1", 4002, clientId=PM.IBKR_CLIENT, readonly=True, timeout=15)
        ib.RequestTimeout = 30
    except Exception as e:                       # noqa: BLE001
        return {}, "IB Gateway not reachable (%s) -- the price files alone" % str(e)[:60]
    out, bad = {}, []
    try:
        for t in tickers:
            try:
                rows = PM._bars(ib, t)
                if rows:
                    out[t] = rows
                else:
                    bad.append(t)
            except Exception:                    # noqa: BLE001
                bad.append(t)
    finally:
        try:
            ib.disconnect()
        except Exception:                        # noqa: BLE001
            pass
    return out, ("no IBKR bars for " + ", ".join(bad)) if bad else None


def _p(x):
    return "--" if x is None else "%.0f%%" % x


def show(t, res, run_at=None):
    from helm import recovery as RC
    if res is None:
        return
    f, H = res["fit"], RC.HORIZ
    i90, iy = H.index(90), H.index(365)
    console.print("\n[bold]%s[/bold] put -- if assigned on %s at today's $%.2f (reading %s), effective basis $%.2f, "
                  "%d shares%s" % (t, res["start"], res["spot"], res["reading"], res["basis"], res["shares"],
                                   ("  [dim](run %s)[/dim]" % run_at[:16].replace("T", " ")) if run_at else ""))
    console.print("  vol now %.0f%% (%s) · normal %.0f%% · half a shock gone in ~%.0f trading days · implied/realized "
                  "%.2f (%s) · report-day move median %s · %s" % (
                      100 * f["start_vol"], f["start_note"], 100 * f["long_run_vol"], f["half_life_days"], f["ratio"],
                      f["ratio_note"], ("%.1f%%" % (100 * f["earn_move_median"])) if f["earn_move_median"] else "--",
                      res["earn_note"]))
    console.print("  closes: %s%s" % (f["closes"], " -- [yellow]short history[/yellow]" if f["short_history"] else ""))
    for k, name in (("now", "vol now, fading"), ("calm", "calm from the start")):
        r = res[k]
        console.print("  [bold]%s[/bold]" % name)
        console.print("    back to basis (price alone): 3 months %s · a year %s" % (_p(r["touch"][i90]), _p(r["touch"][iy])))
        console.print("    made whole (with covered calls): 3 months %s · a year %s · called away within a year %s" % (
            _p(r["whole"][i90]), _p(r["whole"][iy]), _p(r["called"])))
        gd = r["gate_day"]
        console.print("    covered calls: first sellable %s · rent in a year ~$%.2f a share ($%s)" % (
            ("on day %d" % gd) if gd is not None else "not within the year", r["rent"][iy],
            format(int(round(r["rent"][iy] * res["shares"])), ",d")))
    console.print("  [dim]A model, not a forecast: no view on direction, no skew, earnings dates estimated. "
                  "Gate (a 0.20-delta call at the basis) near $%.2f now, $%.2f calm.[/dim]" % (res["gate_now"], res["gate_calm"]))


def run():
    args = sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1] == "recovery" else sys.argv[1:]
    if "-h" in args or "--help" in args:
        console.print(__doc__)
        return
    from helm import recovery as RC
    if "--selftest" in args:
        sys.exit(RC._selftest())
    from helm.db import get_conn
    from helm.config import PROJECT_ROOT as HELM_ROOT
    flags = {a for a in args if a.startswith("--")}
    n = RC.N_PATHS
    if "--paths" in args:
        n = int(args[args.index("--paths") + 1])
    names = [a for i, a in enumerate(args) if not a.startswith("--") and not (i > 0 and args[i - 1] == "--paths")]
    tickers = {t.strip().upper() for x in names for t in x.split(",") if t.strip()} or None
    conn = get_conn()
    try:
        puts = _open_puts(conn, tickers)
        if not puts:
            console.print("No open put%s." % ((" on " + ", ".join(sorted(tickers))) if tickers else ""))
            return
        if "--last" in flags:
            for pid, t in puts:
                lt = RC.latest_for(conn, pid)
                if lt is None:
                    console.print("\n%s: no run recorded -- cd ~/Projects/helm && helm recovery %s" % (t, t))
                elif lt["error"]:
                    console.print("\n%s (run %s): %s" % (t, lt["run_at"][:16], lt["error"]))
                else:
                    show(t, lt["result"], lt["run_at"])
            return
        w = _in_window()
        if w and "--force" not in flags:
            console.print("[yellow]It's inside the %s snapshot window -- the run would compete with the snapshot "
                          "for IBKR. Try after it, or add --force.[/yellow]" % w)
            return
        tk = sorted({t for _, t in puts})
        console.print("[dim]Reading a year of daily closes from IBKR for %s ...[/dim]" % ", ".join(tk))
        bars, bnote = _bars(tk)
        if bnote:
            console.print("[yellow]%s[/yellow]" % bnote)
        console.print("[dim]Simulating %d paths, two cases a put (about 20 seconds a put) ...[/dim]" % n)
        out = []
        for pid, t in puts:
            try:
                res = RC.assess(conn, pid, str(HELM_ROOT), bars.get(t), n=n, today=date.today())
                out.append((pid, t, res, None))
                show(t, res)
            except Exception as e:               # noqa: BLE001 -- one put's failure never stops the run
                out.append((pid, t, None, "%s: %s" % (type(e).__name__, e)))
                console.print("\n[bold]%s[/bold]: [yellow]not run -- %s: %s[/yellow]" % (t, type(e).__name__, e))
        note = "; ".join(x for x in (("partial run: " + ",".join(sorted(tickers))) if tickers else None, bnote) if x) or None
        if "--no-record" in flags:
            console.print("\n[dim]Not recorded (--no-record).[/dim]")
        else:
            RC.record(conn, out, n, note)
            console.print("\n[dim]Recorded; each put's card shows it.[/dim]")
    finally:
        conn.close()
