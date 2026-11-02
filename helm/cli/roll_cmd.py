"""helm roll -- the roll choices for a position's short, priced at the bid/ask (s146).

    helm roll NVDA            every open put, covered call or PMCC short on NVDA: up to three rolls
                              for a net credit after fees, and the other choices in dollars
    helm roll AMZN,CSCO       several names
    helm roll NVDA --why      also list every contract left off, with its reason
    helm roll NVDA --no-record
    helm roll NVDA --force    run inside a snapshot window anyway
    helm roll --selftest

Read-only: HELM never places or books anything. A roll you make in Fidelity is booked
by the import ("Update book from Fidelity"). The rules are helm/rolltool.py.
(Replaces the old `helm roll`, which booked a close and handed off to `helm open`;
its file, helm/cli/roll.py, is no longer registered.)
"""
from __future__ import annotations

import sys
from datetime import date

from rich.console import Console
from rich.table import Table
from rich import box

console = Console()
WHAT = {"put": "put", "cc": "covered call", "pmcc": "PMCC short"}


def _usd(x):
    return "--" if x is None else ("-" if x < 0 else "") + "$" + format(int(round(abs(float(x)))), ",d")


def _show_item(it, why=False):
    console.print()
    cur = it.get("cur") or {}
    head = "[bold]%s %s[/bold]" % (it["ticker"], WHAT.get(it["kind"], it["kind"]))
    if it.get("strike") is not None:
        head += "  %g %s %s x%d · sold at $%.2f · %d days left" % (
            it["strike"], (it.get("opt_type") or "").lower(), it["expiry"], it["contracts"], it["credit"], it["dte"])
    console.print(head)
    if cur:
        console.print("[dim]now bid $%.2f / ask $%.2f · stock %s%s[/dim]" % (
            cur["bid"], cur["ask"], "%.2f" % it["spot"] if it.get("spot") else "--",
            (" · " + it["earnings"]) if it.get("earnings") else ""))
    for f in it.get("flags") or []:
        console.print("[yellow]  %s[/yellow]" % f)
    if it["rolls"]:
        up = "out and down" if it["kind"] == "put" else "up and out"
        console.print("Roll %s -- net credit at the bid/ask, after both legs' fees:" % up)
        t = Table(box=box.SIMPLE_HEAD, pad_edge=False)
        for col, j in (("#", "right"), ("New", "left"), ("Days", "right"), ("Delta", "right"), ("Sell at", "right"),
                       ("Buy back at", "right"), ("Net credit", "right"), ("Days added", "right"),
                       ("50% buy-back", "right"), ("Note", "left")):
            t.add_column(col, justify=j, no_wrap=True)
        for r in it["rolls"]:
            note = r["flags"] or ""
            ex = r.get("extra") or {}
            if "floor_strike_after" in ex:
                note = ("next floor $%.2f" % ex["floor_strike_after"]) + ("; " + note if note else "")
            if "collateral_change" in ex and ex["collateral_change"]:
                note = ("collateral %s" % _usd(ex["collateral_change"])) + ("; " + note if note else "")
            t.add_row(str(r["choice"]), "%s %g" % (r["expiry"], r["strike"]), str(r["dte"]),
                      "%.2f" % r["delta"] if r["delta"] is not None else "", "$%.2f" % r["bid"],
                      "$%.2f" % cur["ask"], _usd(r["net_credit"]), "+%d" % r["days_added"],
                      "$%.2f" % r["rest_price"] if r.get("rest_price") else "", note)
        console.print(t)
    elif it.get("why_none"):
        console.print("[bold]No roll:[/bold] %s" % it["why_none"])
    if it["alternatives"]:
        console.print("Or:")
        for a in it["alternatives"]:
            console.print("  [bold]%s[/bold] -- %s%s" % (a["label"], a["text"],
                                                       (".  " + a["lean"]) if a.get("lean") else ""))
            if a.get("then"):
                console.print("    [dim]then: %s[/dim]" % a["then"])
    if why and it.get("contracts_seen"):
        left = [c for c in it["contracts_seen"] if not c["choice"]]
        if left:
            console.print("[dim]Left off:[/dim]")
            for c in sorted(left, key=lambda c: (c["expiry"], c["strike"])):
                console.print("[dim]  %s %g  delta %s  bid/ask %s/%s  %s[/dim]" % (
                    c["expiry"], c["strike"], "%.2f" % c["delta"] if c["delta"] is not None else "--",
                    c["bid"], c["ask"], c["reason"]))


def _show(run, why=False):
    mk = {1: "market open", 0: "market closed -- last quotes"}.get(run.get("market_open"), "")
    console.print()
    console.print("[bold]Rolls[/bold]  %s  %s  · fees $%.2f a contract a leg" % (
        run["run_at"], ("(" + mk + ")") if mk else "", run["fee_per_contract"]))
    for it in run["items"]:
        _show_item(it, why)
    for t in run.get("missing") or []:
        console.print("\n[dim]%s: HELM has no open put, covered call or PMCC on it.[/dim]" % t)
    console.print()
    console.print("[dim]Prices are the bid and ask, not the mid. HELM never places anything; a roll made in Fidelity "
                  "is booked by the import. Days added = the new short's days minus the old one's.[/dim]")
    console.print()


def run():
    args = sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1] == "roll" else sys.argv[1:]
    if "-h" in args or "--help" in args:
        console.print(__doc__)
        return
    from helm import rolltool as R
    if "--selftest" in args:
        sys.exit(0 if R._selftest() else 1)
    flags = {a for a in args if a.startswith("--")}
    tickers = [t.strip().upper() for a in args if not a.startswith("--") for t in a.split(",") if t.strip()]
    if not tickers:
        console.print("[red]Name a ticker: cd ~/Projects/helm && helm roll NVDA[/red]")
        return
    from helm.db import get_conn
    from helm.config import get_active_account
    from helm.cli.puts_cmd import _in_window
    w = _in_window()
    if w and "--force" not in flags:
        console.print("[yellow]It's inside the %s snapshot window -- the run would compete with the snapshot for "
                      "IBKR. Try after it, or add --force.[/yellow]" % w)
        return
    conn = get_conn()
    try:
        r = R.build(conn, date.today(), tickers, account_id=get_active_account())
        if "--no-record" not in flags and r["items"]:
            R.record(conn, r, ",".join(tickers))
        _show(r, "--why" in flags)
        if "--no-record" in flags:
            console.print("[dim]Not recorded (--no-record).[/dim]")
    finally:
        conn.close()
