"""
helm settle -- ask what the broker actually did with positions that reached expiry.

HELM cannot see Fidelity. Inferring assignment from spot-versus-strike would
be a guess presented as a record, so this asks instead and writes only what
the trader confirms.

Scope is deliberately narrow:
  * a SINGLE short option that reached expiry has two plain outcomes, so it
    is asked as a closed question: assigned, or expired worthless.
  * anything MULTI-LEG is only flagged. A four-legged condor does not reduce
    to assigned/not -- one side can be breached, the short assigned and the
    long auto-exercised -- and pretending otherwise would record a shape the
    position never had.
  * ASSIGNED is never written here. It prints the `helm assign` command, so
    the cost-basis arithmetic lives in exactly one place and is seen before
    it is committed.

Nothing is written without an explicit keypress. Enter skips.
"""

import sys
from datetime import date, datetime

from rich.console import Console

from helm.db import get_conn

console = Console()


def _pending(conn, today):
    """OPEN positions whose last leg expiry is already past."""
    rows = conn.execute(
        "SELECT p.id, p.ticker, p.strategy, p.book, p.net_premium, "
        "       p.total_contracts, "
        "       (SELECT MAX(expiration) FROM legs l WHERE l.position_id = p.id) AS exp, "
        "       (SELECT COUNT(*) FROM legs l WHERE l.position_id = p.id) AS nlegs, "
        "       (SELECT COUNT(*) FROM legs l WHERE l.position_id = p.id "
        "        AND l.direction = 'SHORT') AS nshort "
        "FROM positions p WHERE p.status = 'OPEN'").fetchall()
    out = []
    for r in rows:
        if r["exp"] and str(r["exp"])[:10] < today:
            out.append(r)
    return sorted(out, key=lambda r: (str(r["exp"]), r["ticker"]))


def run():
    args = sys.argv[1:]
    today = date.today().isoformat()
    for a in args:
        if a.startswith("--date"):
            today = a.split("=", 1)[1] if "=" in a else today
    conn = get_conn()
    pend = _pending(conn, today)
    console.print()
    if not pend:
        console.print("[green]Nothing to settle.[/green] No open position has passed its expiry.")
        console.print()
        return 0

    console.print("[bold]%d position(s) reached expiry and are still open[/bold]" % len(pend))
    console.print("[dim]HELM cannot see the broker. Nothing below is written unless you say so.[/dim]")
    console.print()

    for r in pend:
        single = (r["nlegs"] == 1 and r["nshort"] == 1)
        console.print("  [bold]%s[/bold]  %s  %s  expired %s" % (
            r["ticker"], r["strategy"], r["book"], r["exp"]))
        if not single:
            console.print("    [yellow]multi-leg -- settle by hand.[/yellow] A %d-leg structure does not"
                          % r["nlegs"])
            console.print("    reduce to assigned/not. Fidelity records a closing price")
            console.print("    per leg, and `helm activity` reads that export and closes")
            console.print("    leg by leg -- prefer it:")
            console.print("      [dim]helm activity[/dim]   (reads the latest Accounts_History*.csv)")
            console.print("    If the export does not carry the expiry, record it by hand:")
            console.print("      [dim]helm close %s --position-id %s --reason EXPIRED[/dim]"
                          % (r["ticker"], r["id"]))
            console.print()
            continue
        try:
            ans = input("    assigned or expired worthless?  [a/e, Enter skips] ").strip().lower()
        except EOFError:
            ans = ""
        if ans == "a":
            console.print("    [cyan]Assignment is written by `helm assign`[/cyan], which shows the")
            console.print("    cost basis before committing. Run:")
            console.print("      [bold]helm assign %s --position-id %s --apply[/bold]"
                          % (r["ticker"], r["id"]))
        elif ans == "e":
            prem = float(r["net_premium"] or 0.0)
            now = datetime.now().isoformat()
            conn.execute(
                "UPDATE positions SET status=?, closed_at=?, exit_reason=?, "
                "realized_pnl=?, updated_at=? WHERE id=?",
                ("EXPIRED", now, "EXPIRED", prem, now, r["id"]))
            conn.commit()
            back = conn.execute("SELECT status, exit_reason, realized_pnl FROM positions "
                                "WHERE id = ?", (r["id"],)).fetchone()
            console.print("    [green]recorded EXPIRED[/green] -- %s / %s / realized $%s"
                          % (back["status"], back["exit_reason"],
                             format(back["realized_pnl"] or 0, ",.2f")))
        else:
            console.print("    [dim]skipped[/dim]")
        console.print()
    return 0
