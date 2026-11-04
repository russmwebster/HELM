"""helm pmcc -- new PMCC entries under the settled rules (s147).

    helm pmcc                 read the live chains, rank, record, show the top 5
    helm pmcc --last          show the latest recorded run (no IBKR)
    helm pmcc NVDA,SPY        only these names (recorded, marked partial)
    helm pmcc --all           show every name that had a pair, not just the top 5
    helm pmcc --why           list every name left off and why
    helm pmcc --no-record     run without recording
    helm pmcc --force         run inside a snapshot window anyway
    helm pmcc --selftest      the rules' self-test (no network, no database)

Offered: names graded A-C or approved funds with no PMCC open. Long 270-730 days,
0.75-0.85 delta; short 30-60 days, 0.20-0.30 delta; both spreads under 10% of mid;
the width test passes with the long at its ask. Per name, the pair with the highest
rent cover (the short's monthly rent over the long's monthly loss of time value);
ranked by rent cover, ties by yearly return on the long's cost. Trend, IV rank and
earnings inside the short are shown and flagged, never gates. One contract per name,
checked against the sleeve and its limits; HELM never places anything. The rules
are helm/pmcctool.py.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import date

from rich.console import Console

console = Console()


def _usd(x):
    return "—" if x is None else ("-" if x < 0 else "") + "$" + format(int(round(abs(float(x)))), ",d")


def _exp(e):
    return str(e)[:10]


def _show(run, show_all=False, why=False):
    from helm.pmcctool import decision_text, TOP_N, RENT_COVER_FLAG
    from helm.puttool import earnings_short
    mk = {1: "market open", 0: "market closed -- last quotes"}.get(run.get("market_open"), "")
    console.print()
    console.print("[bold]New PMCCs[/bold]  %s  %s  ·  sleeve free %s%s" % (
        run["run_at"], ("(" + mk + ")") if mk else "", _usd(run.get("sleeve_free")),
        ("  ·  took %.0f min" % (run["elapsed_s"] / 60.0)) if run.get("elapsed_s") else ""))
    console.print("[dim]%d names offerable (A-C or fund) · %d with no PMCC open, chains read · "
                  "%d had a pair inside every rule[/dim]" % (run["n_universe"], run["n_checked"], run["n_found"]))
    if run.get("note"):
        console.print("[dim]%s[/dim]" % run["note"])
    cands = run["candidates"] if show_all else run["candidates"][:TOP_N]
    if not cands:
        console.print("\n  No PMCC passes every rule today.")
    for c in cands:
        rc = c.get("rent_cover")
        rct = ("rent cover %.1fx" % rc) if rc is not None else "rent cover -- (no time value at mid)"
        if rc is not None and rc < RENT_COVER_FLAG:
            rct = "[yellow]%s (under 2x)[/yellow]" % rct
        console.print()
        console.print("[bold]%d. %s[/bold]  (%s, %s)  —  %s · %.0f%% a year on the long's cost" % (
            c["rank"], c["ticker"], c["grade"] or "", c["sector"] or "", rct, 100 * c["ann_return"]))
        console.print("   long   %s %g call  Δ%.2f  $%.2f mid (bid %.2f / ask %.2f)  ·  time value $%.2f a share, "
                      "$%.2f a month  ·  %d days" % (
                          _exp(c["long_expiry"]), c["long_strike"], c["long_delta"] or 0, c["long_mid"],
                          c["long_bid"], c["long_ask"], c["long_time_value"], c["long_decay_month"], c["long_dte"]))
        console.print("   short  %s %g call  Δ%.2f  $%.2f mid (bid %.2f / ask %.2f)  ·  rent $%.2f a month  ·  "
                      "%d days  ·  50%% buy-back $%.2f" % (
                          _exp(c["short_expiry"]), c["short_strike"], c["short_delta"] or 0, c["short_mid"],
                          c["short_bid"], c["short_ask"], c["rent_month"], c["short_dte"], c["rest_price"]))
        console.print("   net debit %s  ·  width test: gap %g passes the long's ask %.2f by $%.2f  ·  "
                      "loss limit %s  ·  stock $%.2f" % (
                          _usd(c["net_debit"]), c["short_strike"] - c["long_strike"], c["width_floor"],
                          c["width_room"], _usd(c["loss_limit"]), c["spot"] or 0))
        tt = c.get("trend_text") or ""
        if c.get("downtrend"):
            tt = "[yellow]%s[/yellow]" % tt
        ivr = ("IV rank %.0f" % c["iv_rank"]) if c.get("iv_rank") is not None else "IV rank not stored"
        er = c.get("earnings") or ""
        if "INSIDE" in er:
            er = "[yellow]%s[/yellow]" % er
        console.print("   %s  ·  %s  ·  %s" % (tt, ivr, er))
        d = decision_text(c)
        console.print("   %s" % (d if c.get("size_n") else "[yellow]%s[/yellow] -- %s" % (d, c.get("size_note") or "")))
    rej = run.get("rejects") or {}
    if rej:
        def bucket(r):
            for k in ("IB Gateway", "grade", "already open", "same company", "width test", "short call", "long call",
                      "no expiry", "chain not read"):
                if k in r:
                    return k
            return "other"
        lab = {"grade": "grade below C or none", "already open": "a PMCC already open",
               "same company": "same company ranks higher", "width test": "the width test",
               "short call": "no short call inside the rules", "long call": "no long call inside the rules",
               "no expiry": "no expiry in the window", "IB Gateway": "not read: IB Gateway unreachable", "chain not read": "chain not read", "other": "other"}
        cnt = Counter(bucket(r) for r in rej.values())
        console.print()
        console.print("[dim]Left off: " + " · ".join("%d %s" % (n, lab[k]) for k, n in cnt.most_common())
                      + ("" if why else "   (--why for each name)") + "[/dim]")
        if why:
            for tk in sorted(rej):
                console.print("  %-6s %s" % (tk, rej[tk]))
    console.print()


def run():
    args = sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1] == "pmcc" else sys.argv[1:]
    if "-h" in args or "--help" in args:
        console.print(__doc__)
        return
    from helm import pmcctool as M
    if "--selftest" in args:
        sys.exit(0 if M._selftest() else 1)
    from helm.db import get_conn
    from helm.cli.puts_cmd import _in_window
    flags = {a for a in args if a.startswith("--")}
    names = [a for a in args if not a.startswith("--")]
    tickers = [t.strip().upper() for n in names for t in n.split(",") if t.strip()] or None
    conn = get_conn()
    try:
        if "--last" in flags:
            r = M.latest(conn)
            if not r:
                console.print("No run recorded yet. Run [bold]cd ~/Projects/helm && helm pmcc[/bold].")
                return
            _show(r, "--all" in flags, "--why" in flags)
            return
        w = _in_window()
        if w and "--force" not in flags:
            console.print("[yellow]It's inside the %s snapshot window -- the run would compete with the "
                          "snapshot for IBKR. Try after it, or add --force.[/yellow]" % w)
            return
        console.print("[dim]Reading trends, then the live call chains from IBKR, name by name "
                      "(a full run can take a while)...[/dim]")

        def prog(t, why, secs):
            console.print("[dim]  %-6s %4.0fs  %s[/dim]" % (t, secs, ("left off: " + why) if why else "pair found"))
        r = M.build(conn, date.today(), tickers, progress=prog)
        note = ("partial run: " + ",".join(tickers)) if tickers else None
        if r.get("stopped"):                     # s149: the gateway went away mid-run
            note = ((note + "; ") if note else "") + (
                "stopped: IB Gateway unreachable, %d names not read" % len(r["stopped"]))
        r["note"] = note
        if "--no-record" not in flags:
            M.record(conn, r, note)
        _show(r, "--all" in flags, "--why" in flags)
        if "--no-record" in flags:
            console.print("[dim]Not recorded (--no-record).[/dim]")
        if r.get("stopped"):                     # s149
            console.print("\n[bold yellow]Stopped early: IB Gateway could not be reached for %d names in a row, "
                          "so %d names were not read.[/bold yellow] Check IB Gateway, then rerun just those:"
                          % (M.P.GATEWAY_STOP_AFTER, len(r["stopped"])))
            console.print("  cd ~/Projects/helm && helm pmcc %s --why" % ",".join(r["stopped"]), soft_wrap=True)
    finally:
        conn.close()
