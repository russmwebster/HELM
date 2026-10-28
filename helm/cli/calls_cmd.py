"""helm calls -- covered calls on HELM's shares under the settled rules (s138).

    helm calls                      read the live chains for every HELM holding, record, show
    helm calls --if-assigned GOOGL  as if the open GOOGL put were assigned (nothing booked)
    helm calls --last               the latest recorded run (no IBKR); --last --if-assigned for that kind
    helm calls AAPL                 only these holdings
    helm calls --why                why each holding has no call
    helm calls --no-record          run without recording
    helm calls --force              run inside a snapshot window anyway
    helm calls --selftest           the rules' self-test

Rules: strike at or above the effective basis (strike paid less put premium),
0.20-0.35 delta, 30-45 days, spread under 10% of mid, no earnings inside the
trade (failing closed). Three choices per holding, ranked by the premium's
yearly return on the shares' value -- the first is the likeliest to be called
away. HELM never places anything. The rules are helm/calltool.py.
"""
from __future__ import annotations

import sys
from datetime import date

from rich.console import Console
from rich.table import Table
from rich import box

console = Console()


def _usd(x):
    return "—" if x is None else ("-" if x < 0 else "") + "$" + format(int(round(abs(float(x)))), ",d")


def _show(run, why=False):
    mk = {1: "market open", 0: "market closed -- last quotes"}.get(run.get("market_open"), "")
    kind = "IF ASSIGNED -- nothing is booked" if run.get("hypothetical") else "HELM's shares"
    console.print()
    console.print("[bold]Covered calls[/bold]  %s  %s  ·  %s" % (run["run_at"], ("(" + mk + ")") if mk else "", kind))
    if run.get("note"):
        console.print("[dim]%s[/dim]" % run["note"])
    if not run["n_holdings"] and not run["rejects"]:
        console.print("\n  HELM holds no shares, so there is nothing to cover. "
                      "[dim](--if-assigned TICKER shows an open put as if assigned.)[/dim]\n")
        return
    by = {}
    for c in run["candidates"]:
        by.setdefault(c["ticker"], []).append(c)
    for tk in sorted(by):
        cs = by[tk]
        h = cs[0]
        console.print()
        console.print("[bold]%s[/bold]  %d shares · %d contract%s to sell · cost $%.2f · effective basis $%.2f%s" % (
            tk, h["shares"], h["contracts"], "" if h["contracts"] == 1 else "s", h["cost_basis"],
            h["effective_basis"], "" if not h.get("basis_note") else "  [dim](%s)[/dim]" % h["basis_note"]))
        console.print("[dim]%s · price %.2f[/dim]" % (h["earnings"], h["spot"] or 0))
        t = Table(box=box.SIMPLE_HEAD, pad_edge=False)
        for col, j in (("#", "right"), ("Call", "left"), ("Days", "right"), ("Delta", "right"),
                       ("Premium", "right"), ("Collect", "right"), ("A year", "right"),
                       ("If called", "right"), ("50% buy-back", "right"), ("Note", "left")):
            t.add_column(col, justify=j, no_wrap=True)
        for c in cs:
            t.add_row(str(c["choice"]), "%s %g" % (c["expiry"], c["strike"]), str(c["dte"]),
                      "%.2f" % c["delta"] if c["delta"] is not None else "", "$%.2f" % c["mid"],
                      _usd(c["mid"] * 100 * c["contracts"]), "%.0f%%" % (100 * c["ann_return"]),
                      _usd(c["if_called"]), "$%.2f" % c["rest_price"], c.get("exdiv") or "")
        console.print(t)
    rej = run.get("rejects") or {}
    if rej:
        console.print("[dim]No call: %s[/dim]" % ", ".join(sorted(rej)) + ("" if why else "  [dim](--why)[/dim]"))
        if why:
            for tk in sorted(rej):
                console.print("  %-6s %s" % (tk, rej[tk]))
    console.print("[dim]Choice 1 pays most and is the likeliest to be called away; a higher strike keeps the "
                  "shares more often. 'If called' = (strike - effective basis) x shares + the premium.[/dim]")
    console.print()


def run():
    args = sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1] == "calls" else sys.argv[1:]
    if "-h" in args or "--help" in args:
        console.print(__doc__)
        return
    from helm import calltool as C
    if "--selftest" in args:
        sys.exit(0 if C._selftest() else 1)
    from helm.db import get_conn
    from helm.config import get_active_account
    from helm.cli.puts_cmd import _in_window
    flags = {a for a in args if a.startswith("--")}
    assigned = None
    if "--if-assigned" in args and "--last" not in flags:
        i = args.index("--if-assigned")
        val = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else ""
        assigned = [t.strip().upper() for t in val.split(",") if t.strip()]
        if not assigned:
            console.print("[red]--if-assigned needs a ticker, e.g. --if-assigned GOOGL,JPM[/red]")
            return
        args = args[:i] + args[i + 2:]
    names = [a for a in args if not a.startswith("--")]
    tickers = [t.strip().upper() for n in names for t in n.split(",") if t.strip()] or None

    conn = get_conn()
    try:
        if "--last" in flags:
            r = C.latest(conn, 1 if "--if-assigned" in sys.argv else 0)
            if not r:
                console.print("No run recorded yet. Run [bold]helm calls[/bold].")
                return
            _show(r, "--why" in flags)
            return
        w = _in_window()
        if w and "--force" not in flags:
            console.print("[yellow]It's inside the %s snapshot window -- the run would compete with the "
                          "snapshot for IBKR. Try after it, or add --force.[/yellow]" % w)
            return
        r = C.build(conn, date.today(), tickers, assigned, account_id=get_active_account())
        if r["n_holdings"]:
            console.print("[dim]Read the live call chains from IBKR.[/dim]")
        note = ("if assigned: " + ",".join(assigned)) if assigned else (
            ("partial run: " + ",".join(tickers)) if tickers else None)
        r["note"] = note
        if "--no-record" not in flags and (r["n_holdings"] or r["rejects"]):
            C.record(conn, r, note)
        _show(r, "--why" in flags)
        if "--no-record" in flags:
            console.print("[dim]Not recorded (--no-record).[/dim]")
    finally:
        conn.close()
