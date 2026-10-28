"""helm puts -- today's cash-secured puts under the settled rules (s138).

    helm puts                 read the live chains, rank, record, show the top 5
    helm puts --last          show the latest recorded run (no IBKR)
    helm puts AAPL,XLV        only these names (recorded, marked partial)
    helm puts --all           show every name that had a contract, not just the top 5
    helm puts --why           list every name left off and why
    helm puts --no-record     run without recording
    helm puts --force         run inside a snapshot window anyway
    helm puts --selftest      the rules' self-test (no network, no database)

Offered: names graded A-C or approved funds, IV rank >= 50, no report within 10
days (a stock with no reliable date is left off), 0.25-0.30 delta, 30-45 days,
spread under 10% of mid. One contract per name -- the highest annualized return on
collateral -- ranked. Each line is sized by the sleeve and its limits; HELM never
places anything. The rules are helm/puttool.py.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from collections import Counter

from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

# Snapshot windows (standing rule): a run here competes with the snapshot for IBKR.
WINDOWS = (("10:00", "10:35"), ("12:30", "13:05"), ("15:15", "15:50"))


def _in_window(now=None):
    hm = (now or datetime.now()).strftime("%H:%M")
    for a, b in WINDOWS:
        if a <= hm < b:
            return "%s-%s" % (a, b)
    return None


def _usd(x):
    return "—" if x is None else "$" + format(int(round(float(x))), ",d")


def _show(run, show_all=False, why=False):
    from helm.puttool import decision_text, earnings_short, TOP_N
    when = run["run_at"]
    mk = {1: "market open", 0: "market closed -- last quotes"}.get(run.get("market_open"), "")
    console.print()
    console.print("[bold]New puts[/bold]  %s  %s  ·  IV ranks of %s  ·  sleeve free %s" % (
        when, ("(" + mk + ")") if mk else "", run.get("ivr_date"), _usd(run.get("sleeve_free"))))
    console.print("[dim]%d names offerable (A-C or fund) · %d passed IV rank and earnings · "
                  "%d had a contract inside every rule[/dim]" % (
                      run["n_universe"], run["n_checked"], run["n_found"]))
    if run.get("note"):
        console.print("[dim]%s[/dim]" % run["note"])
    cands = run["candidates"] if show_all else run["candidates"][:TOP_N]
    if not cands:
        console.print("\n  No put passes every rule today.")
    else:
        t = Table(box=box.SIMPLE_HEAD, pad_edge=False)
        for h, j in (("#", "right"), ("Name", "left"), ("Grade", "left"), ("Put", "left"),
                     ("Days", "right"), ("Delta", "right"), ("Premium", "right"), ("A year", "right"),
                     ("Secures", "right"), ("50% buy-back", "right"), ("IVR", "right"),
                     ("Earnings", "left"), ("Size", "left")):
            t.add_column(h, justify=j, no_wrap=True)
        for c in cands:
            t.add_row(str(c["rank"]), c["ticker"], c["grade"] or "",
                      "%s %g" % (c["expiry"], c["strike"]), str(c["dte"]),
                      "%.2f" % c["delta"] if c["delta"] is not None else "",
                      "$%.2f" % c["mid"], "%.0f%%" % (100 * c["ann_return"]),
                      _usd(c["collateral"]), "$%.2f" % c["rest_price"],
                      "%.0f" % c["iv_rank"] if c["iv_rank"] is not None else "",
                      earnings_short(c["earnings"]), decision_text(c).replace("would be declined", "declined"))
        console.print(t)
        if any(not c.get("size_n") for c in cands):
            note = next(c["size_note"] for c in cands if not c.get("size_n"))
            console.print("[dim]Declined = would be declined today, because %s. Shown so the list builds a record.[/dim]" % (note.split("; ", 1)[-1] if note else "no room"))
    rej = run.get("rejects") or {}
    if rej:
        def bucket(r):
            for k in ("grade", "IV rank", "no entry within", "reliable", "spread", "delta band", "no expiry",
                      "chain", "IBKR returned", "no contract"):
                if k in r:
                    return k
            return "other"
        cnt = Counter(bucket(r) for r in rej.values())
        lab = {"grade": "grade below C or none", "IV rank": "IV rank under 50 or missing",
               "no entry within": "earnings within 10 days", "reliable": "no reliable earnings date",
               "spread": "spread too wide", "delta band": "no put in the delta band",
               "no expiry": "no expiry in 30-45 days", "IBKR returned": "no IBKR quotes",
               "chain": "chain not read", "no contract": "no contract in the window", "other": "other"}
        console.print("[dim]Left off: " + " · ".join("%d %s" % (n, lab[k]) for k, n in cnt.most_common())
                      + ("" if why else "   (--why for each name)") + "[/dim]")
        if why:
            for tk in sorted(rej):
                console.print("  %-6s %s" % (tk, rej[tk]))
    console.print()


def run():
    args = sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1] == "puts" else sys.argv[1:]
    if "-h" in args or "--help" in args:
        console.print(__doc__)
        return
    from helm import puttool as P
    if "--selftest" in args:
        sys.exit(0 if P._selftest() else 1)
    from helm.db import get_conn
    flags = {a for a in args if a.startswith("--")}
    names = [a for a in args if not a.startswith("--")]
    tickers = [t.strip().upper() for n in names for t in n.split(",") if t.strip()] or None

    conn = get_conn()
    try:
        if "--last" in flags:
            r = P.latest(conn)
            if not r:
                console.print("No run recorded yet. Run [bold]helm puts[/bold].")
                return
            _show(r, "--all" in flags, "--why" in flags)
            return
        w = _in_window()
        if w and "--force" not in flags:
            console.print("[yellow]It's inside the %s snapshot window -- the run would compete with the "
                          "snapshot for IBKR. Try after it, or add --force.[/yellow]" % w)
            return
        console.print("[dim]Reading the live put chains from IBKR (about 10-15 seconds a name)...[/dim]")
        r = P.build(conn, date.today(), tickers)
        note = ("partial run: " + ",".join(tickers)) if tickers else None
        r["note"] = note
        if "--no-record" not in flags:
            P.record(conn, r, note)
        _show(r, "--all" in flags, "--why" in flags)
        if "--no-record" in flags:
            console.print("[dim]Not recorded (--no-record).[/dim]")
    finally:
        conn.close()
