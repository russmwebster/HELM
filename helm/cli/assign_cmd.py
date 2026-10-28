"""
helm assign -- record a short put being assigned, and the shares it hands you.

    helm assign TICKER [--position-id ID] [--date YYYY-MM-DD] [--apply]

The fallback for the Fidelity import, which books assignments itself from the
download (s138). Dry-run by default; --apply writes. Refuses rather than guesses
when more than one open put could be meant (W7).

The booking is helm/assignment.py, shared with the import (s138, Russ 10-28):
the put closes at $0.00 on the assignment date (premium kept and counted in the
sleeve's realized total); the shares go in at the strike; the effective basis
(strike less premium per share) is stored as a number for the covered-call rule.
--date is the assignment date (default today); the time is 16:00, date only.
"""

import sys
from datetime import date

from rich.console import Console

from helm.db import get_conn
from helm import assignment as A

console = Console()


def _find(conn, ticker, position_id):
    q = ("SELECT id FROM positions WHERE strategy = 'CSP' AND status = 'OPEN' AND book = 'REAL'")
    args = []
    if position_id:
        q += " AND id = ?"; args.append(position_id)
    elif ticker:
        q += " AND ticker = ?"; args.append(ticker.upper())
    return conn.execute(q, args).fetchall()


def _opt(args, name):
    if name in args:
        i = args.index(name)
        val = args[i + 1] if i + 1 < len(args) else None
        return val, args[:i] + args[i + 2:]
    return None, args


def run():
    # helm.py rewrites sys.argv to ["helm assign"] + rest, so the real
    # arguments start at index 1, not 2.
    args = sys.argv[1:]
    apply = "--apply" in args
    args = [a for a in args if a != "--apply"]
    position_id, args = _opt(args, "--position-id")
    day, args = _opt(args, "--date")
    ticker = args[0].upper() if args else None
    if not ticker and not position_id:
        console.print("[red]Usage:[/red] helm assign TICKER [--position-id ID] [--date YYYY-MM-DD] [--apply]")
        return 2
    try:
        day = date.fromisoformat(day).isoformat() if day else date.today().isoformat()
    except ValueError:
        console.print("[red]--date must be YYYY-MM-DD[/red]")
        return 2
    when = day + "T16:00:00"

    conn = get_conn()
    try:
        rows = _find(conn, ticker, position_id)
        if not rows:
            console.print("[red]No open real CSP found[/red] for %s." % (position_id or ticker))
            return 1
        if len(rows) > 1:
            console.print("[red]%d open CSPs match[/red] -- pass --position-id. Refusing to guess." % len(rows))
            for r in rows:
                console.print("   %s" % r["id"])
            return 1
        A.ensure_columns(conn)
        try:
            v = A.preview(conn, rows[0]["id"])
        except ValueError as e:
            console.print("[red]%s[/red]" % e)
            return 1

        console.print()
        console.print("[bold]Assignment[/bold]  %s  on %s" % (v["position"]["id"], day))
        console.print("  short put       %s x%d @ %g, expiring %s" % (v["ticker"], v["contracts"], v["strike"], v["expiry"]))
        console.print("  shares in       %d  (holding becomes %d)" % (v["shares"], v["new_shares"]))
        console.print("  cash out        $%s  (strike x shares)" % format(v["cash"], ",.2f"))
        console.print("  premium kept    $%s  ($%.2f a share) -- the put closes at $0.00, realized" % (
            format(v["premium"], ",.2f"), v["premium_ps"]))
        console.print("  cost basis      $%s a share  (the strike%s)" % (
            format(v["cost_basis"], ",.2f"), "; averaged with the shares already held" if v["prior_shares"] else ""))
        console.print("  effective basis $%s a share  (strike less premium) -- the covered-call strike floor" % format(
            v["effective_basis"], ",.2f"))
        console.print()
        if not apply:
            console.print("[dim]dry run -- nothing written. Re-run with --apply.[/dim]")
            console.print()
            return 0
        A.book(conn, v["position"]["id"], when, "booked by hand (helm assign)")
        back = conn.execute("SELECT shares, cost_basis, effective_basis FROM stock_positions WHERE ticker=? "
                            "AND account_id=?", (v["ticker"], v["position"]["account_id"])).fetchone()
        console.print("[green]applied.[/green]  %s: %d shares, cost $%.2f, effective $%.2f a share" % (
            v["ticker"], back["shares"], back["cost_basis"], back["effective_basis"]))
        console.print()
        return 0
    finally:
        conn.close()
