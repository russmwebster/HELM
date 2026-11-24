"""helm spreads -- today's bull put spreads under Russ's rules (s165, 2026-11-23). Display only.

    helm spreads              read the live chains, rank, record, show the top 5
    helm spreads --last       show the latest recorded run (no IBKR)
    helm spreads SPY,QQQ      only these names (recorded, marked partial)
    helm spreads --all        every name that had a spread, not just the top 5; every index width
    helm spreads --why        every name left off and why
    helm spreads --no-record  run without recording
    helm spreads --force      run inside a snapshot window anyway
    helm spreads --selftest   the rules' self-test (no network, no database)

Offered: the put list's names (graded A-C or approved funds, IV rank >= 50, no report within 10
days). Sell a 0.25-0.30 delta put 30-60 days out; buy the put about 5% of the stock's price below
it, same expiry. The whole spread's market -- worst fill to best -- must be under 10% of the mid
credit. One spread per name, ranked by the yearly return on the most it can lose, sized by that
loss against the sleeve and its limits. Shown with each: the 50% buy-back, the day 21 days are
left, and the stock you would buy if every sold put at the suggested size were assigned. The broad index funds are
shown beside the list at 2%, 3% and 5% widths whatever their IV rank. HELM never places anything.
The rules are helm/spreadtool.py.
"""
from __future__ import annotations

import sys
from datetime import date
from collections import Counter

from rich.console import Console
from rich.table import Table
from rich import box

console = Console()


def _usd(x):
    return "—" if x is None else "$" + format(int(round(float(x))), ",d")


def _md(d):
    return str(d)[5:10] if d else ""


def _table(rows, held, show_width=False):
    from helm.puttool import earnings_short
    from helm.spreadtool import decision_text, _pct
    t = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    cols = [("#", "right"), ("Name", "left"), ("Held", "left"), ("Grade", "left"), ("Spread", "left")]
    if show_width:
        cols.append(("Width", "right"))
    cols += [("Days", "right"), ("Delta", "right"), ("Credit", "right"), ("Can lose", "right"),
             ("A year", "right"), ("Market", "right"), ("50% buy-back", "right"), ("Out by", "left"),
             ("If all assigned", "right"), ("IVR", "right"), ("Earnings", "left"), ("Size", "left")]
    for h, j in cols:
        t.add_column(h, justify=j, no_wrap=True)
    for c in rows:
        cells = [str(c["rank"]) if c.get("rank") is not None else "—", c["ticker"],
                 held.get(c["ticker"], ""), c.get("grade") or "",
                 "%s %g/%g" % (c["expiry"], c["short_strike"], c["long_strike"])]
        if show_width:
            cells.append("%.0f%%" % (100 * c["width_pct"]))
        cells += [str(c["dte"]), "%.2f" % c["short_delta"] if c.get("short_delta") is not None else "",
                  "$%.2f" % c["credit"], _usd(c["max_loss"]), "%.0f%%" % (100 * c["ann_return"]),
                  "%s%%" % _pct(c["gap_pct"]), "$%.2f" % c["rest_price"], _md(c["exit_date"]),
                  _usd(c["stock_if_assigned"] * c["size_n"]) if c.get("size_n") else "—",   # Russ 11-24: at the suggested size
                  "%.0f" % c["iv_rank"] if c.get("iv_rank") is not None else "",
                  earnings_short(c.get("earnings")), decision_text(c).replace("would be declined", "declined")]
        t.add_row(*cells)
    console.print(t)


def _held(run):
    try:
        from helm.todayview import held_on, _held_conn
        hc = _held_conn()
        try:
            names = {c["ticker"] for c in run["candidates"]} | {x["ticker"] for x in run.get("index") or []}
            return {t: held_on(hc, t) for t in names}
        finally:
            hc.close()
    except Exception as e:
        console.print("[dim]Held not read (%s)[/dim]" % str(e)[:60])
        return {}


def _show(run, show_all=False, why=False):
    from helm.spreadtool import TOP_N, fitting, declined_summary, WIDTH_PCT
    from helm.puttool import IVR_MIN
    mk = {1: "market open", 0: "market closed -- last quotes"}.get(run.get("market_open"), "")
    console.print()
    console.print("[bold]New bull put spreads[/bold]  %s  %s  ·  IV ranks of %s  ·  sleeve free %s" % (
        run["run_at"], ("(" + mk + ")") if mk else "", run.get("ivr_date"), _usd(run.get("sleeve_free"))))
    console.print("[dim]%d names offerable (A-C or fund) · %d passed IV rank and earnings · "
                  "%d had a spread inside every rule · bought put %.0f%% of the price below[/dim]" % (
                      run["n_universe"], run["n_checked"], run["n_found"], 100 * WIDTH_PCT))
    if run.get("note"):
        console.print("[dim]%s[/dim]" % run["note"])
    held = _held(run)
    cands = run["candidates"] if show_all else run["candidates"][:TOP_N]
    if not cands:
        console.print("\n  No spread passes every rule today.")
    else:
        _table(cands, held)
        if not show_all and any(not c.get("size_n") for c in cands):
            fit = fitting(run["candidates"])
            console.print()
            if fit:
                console.print("[bold]Best that fit your limits[/bold]  [dim](# is the rank on the full list)[/dim]")
                _table(fit, held)
            else:
                console.print("[bold]Best that fit your limits[/bold]  none -- no name below the top 5 fits the limits.")
            summ = declined_summary(run["candidates"])
            if summ:
                console.print("[dim]%s   (--all for the whole list)[/dim]" % summ)
    idx = run.get("index") or []
    if idx:
        have = [x for x in idx if x.get("short_strike") is not None]
        console.print()
        console.print("[bold]Index funds[/bold]  [dim](read whatever their IV rank -- the bar is %.0f; 2%%, 3%% and 5%% "
                      "widths for comparison; # only when it is also on the list)[/dim]" % IVR_MIN)
        if have:
            _table(sorted(have, key=lambda x: (x["ticker"], x["width_pct"])), held, show_width=True)
        else:
            console.print("  no index fund had a spread inside the rules")
        for x in idx:
            if x.get("short_strike") is None and (show_all or why or not have):
                console.print("  %-6s %2.0f%%  %s" % (x["ticker"], 100 * (x.get("width_pct") or 0), x.get("note") or ""))
    rej = run.get("rejects") or {}
    if rej:
        def bucket(r):
            for k in ("IB Gateway", "grade", "IV rank", "no entry within", "reliable", "market on the spread",
                      "delta band", "no expiry", "bought put", "credit", "chain", "IBKR returned", "two-sided"):
                if k in r:
                    return k
            return "other"
        lab = {"grade": "grade below C or none", "IV rank": "IV rank under 50 or missing",
               "no entry within": "earnings within 10 days", "reliable": "no reliable earnings date",
               "market on the spread": "market too wide", "delta band": "no put in the delta band",
               "no expiry": "no expiry in 30-60 days", "bought put": "no bought put listed",
               "credit": "credit too small", "IBKR returned": "no IBKR quotes", "two-sided": "no two-sided market",
               "IB Gateway": "not read: IB Gateway unreachable", "chain": "chain not read", "other": "other"}
        cnt = Counter(bucket(r) for r in rej.values())
        console.print("[dim]Left off: " + " · ".join("%d %s" % (n, lab[k]) for k, n in cnt.most_common())
                      + ("" if why else "   (--why for each name)") + "[/dim]")
        if why:
            for tk in sorted(rej):
                console.print("  %-6s %s" % (tk, rej[tk]))
    console.print()


def run():
    args = sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1] == "spreads" else sys.argv[1:]
    if "-h" in args or "--help" in args:
        console.print(__doc__)
        return
    from helm import spreadtool as SP
    if "--selftest" in args:
        sys.exit(0 if SP._selftest() else 1)
    from helm.db import get_conn
    from helm.cli.puts_cmd import _in_window
    flags = {a for a in args if a.startswith("--")}
    names = [a for a in args if not a.startswith("--")]
    tickers = [t.strip().upper() for n in names for t in n.split(",") if t.strip()] or None
    conn = get_conn()
    try:
        if "--last" in flags:
            r = SP.latest(conn)
            if not r:
                console.print("No run recorded yet. Run [bold]cd ~/Projects/helm && helm spreads[/bold].")
                return
            _show(r, "--all" in flags, "--why" in flags)
            return
        w = _in_window()
        if w and "--force" not in flags:
            console.print("[yellow]It's inside the %s snapshot window -- the run would compete with the "
                          "snapshot for IBKR. Try after it, or add --force.[/yellow]" % w)
            return
        console.print("[dim]Reading the live put chains from IBKR (about 15-20 seconds a name)...[/dim]")
        r = SP.build(conn, date.today(), tickers)
        note = ("partial run: " + ",".join(tickers)) if tickers else None
        if r.get("stopped"):
            note = ((note + "; ") if note else "") + (
                "stopped: IB Gateway unreachable, %d names not read" % len(r["stopped"]))
        r["note"] = note
        if "--no-record" not in flags:
            SP.record(conn, r, note)
        _show(r, "--all" in flags, "--why" in flags)
        if "--no-record" in flags:
            console.print("[dim]Not recorded (--no-record).[/dim]")
        if r.get("stopped"):
            console.print("\n[bold yellow]Stopped early: IB Gateway could not be reached for %d names in a row.[/bold yellow] "
                          "Check IB Gateway, then rerun just those:" % 3)
            console.print("  cd ~/Projects/helm && helm spreads %s --why" % ",".join(r["stopped"]), soft_wrap=True)
    finally:
        conn.close()


if __name__ == "__main__":
    run()
