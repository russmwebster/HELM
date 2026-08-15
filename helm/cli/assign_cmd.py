"""
helm assign -- record a short put being assigned, and the shares it hands you.

The hinge of the wheel, and it was missing. Both halves already existed:
`stock_positions` with a `helm stock` command, and ASSIGNED in every status
vocabulary. Nothing connected them -- Position.mark_assigned() had zero
callers and the table had zero rows, so no assignment had ever been recorded.

ACCOUNTING, stated because it is the part that is easy to get wrong:
  * the shares are bought AT THE STRIKE -- that is the cash that leaves, and
    that is what goes in stock_positions.cost_basis, PER SHARE.
  * the premium was already earned. It is recorded as the CSP position's
    realized P&L. Folding it into the basis as well would count it twice.
  * the effective (wheel) basis -- strike minus premium per share -- is
    printed and stored in notes, never used as the cost basis.

Dry-run by default. --apply writes. It refuses rather than guesses when more
than one position could be meant (W7).
"""

import sys
from datetime import datetime

from rich.console import Console

from helm.db import get_conn
from helm.config import get_active_account

console = Console()


def _find(conn, ticker, position_id):
    q = ("SELECT id, ticker, total_contracts, net_premium, book, status "
         "FROM positions WHERE strategy = 'CSP' AND status = 'OPEN'")
    args = []
    if position_id:
        q += " AND id = ?"; args.append(position_id)
    elif ticker:
        q += " AND ticker = ?"; args.append(ticker.upper())
    return conn.execute(q, args).fetchall()


def run():
    # helm.py rewrites sys.argv to ["helm assign"] + rest, so the real
    # arguments start at index 1, not 2.
    args = sys.argv[1:]
    apply = "--apply" in args
    args = [a for a in args if a != "--apply"]
    position_id = None
    if "--position-id" in args:
        i = args.index("--position-id")
        position_id = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    ticker = args[0].upper() if args else None
    if not ticker and not position_id:
        console.print("[red]Usage:[/red] helm assign TICKER [--position-id ID] [--apply]")
        return 2

    conn = get_conn()
    rows = _find(conn, ticker, position_id)
    if not rows:
        console.print("[red]No open CSP found[/red] for %s." % (position_id or ticker))
        return 1
    if len(rows) > 1:
        console.print("[red]%d open CSPs match[/red] -- pass --position-id. Refusing to guess." % len(rows))
        for r in rows:
            console.print("   %s" % r["id"])
        return 1

    p = rows[0]
    leg = conn.execute(
        "SELECT strike, expiration, contracts FROM legs "
        "WHERE position_id = ? AND direction = 'SHORT' AND option_type = 'PUT'",
        (p["id"],)).fetchone()
    if not leg or leg["strike"] is None:
        console.print("[red]No short put leg with a strike[/red] on %s." % p["id"])
        return 1

    contracts = leg["contracts"] or p["total_contracts"] or 1
    shares = int(contracts) * 100
    strike = float(leg["strike"])
    premium = float(p["net_premium"] or 0.0)
    prem_ps = premium / shares if shares else 0.0
    effective = round(strike - prem_ps, 2)
    cash = round(strike * shares, 2)

    console.print()
    console.print("[bold]Assignment[/bold]  %s" % p["id"])
    console.print("  short put      %s x%d @ %s, expiring %s" % (p["ticker"], contracts, strike, leg["expiration"]))
    console.print("  shares in      %d" % shares)
  
    console.print("  cash out       $%s  (strike x shares)" % format(cash, ",.2f"))
    console.print("  cost basis     $%s per share  <- stored" % format(strike, ",.2f"))
    console.print("  premium kept   $%s  ($%s per share) -> realized on the CSP" % (format(premium, ",.2f"), format(prem_ps, ",.2f")))
    console.print("  effective basis $%s per share  (strike less premium) -- shown, not stored" % format(effective, ",.2f"))
    console.print("  covered calls  %d contract(s) writable once recorded" % (shares // 100))
    console.print()

    if not apply:
        console.print("[dim]dry run -- nothing written. Re-run with --apply.[/dim]")
        console.print()
        return 0

    now = datetime.now().isoformat()
    acct = get_active_account()
    # get_active_account() returns the account id as a STRING here, not a
    # row. Handle both rather than assume -- a wrong guess here writes the
    # shares against no account.
    acct_id = acct if isinstance(acct, str) else (acct["id"] if acct else None)
    prior = conn.execute("SELECT shares, cost_basis FROM stock_positions WHERE ticker = ?", (p["ticker"],)).fetchone()
    if prior and prior["shares"]:
        # weighted average per-share basis across both lots
        tot = int(prior["shares"]) + shares
        basis = round(((prior["cost_basis"] or 0) * int(prior["shares"]) + strike * shares) / tot, 4)
        new_shares = tot
    else:
        basis, new_shares = round(strike, 4), shares
    note = "assigned from %s on %s; effective basis %.2f after premium" % (p["id"], now[:10], effective)
    conn.execute(
        "INSERT INTO stock_positions (id, account_id, ticker, shares, cost_basis, acquired_at, notes, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET shares=excluded.shares, "
        "cost_basis=excluded.cost_basis, notes=excluded.notes, updated_at=excluded.updated_at",
        ("SP-" + p["ticker"], acct_id, p["ticker"], new_shares, basis, now, note, now))
    conn.execute("UPDATE positions SET status=?, closed_at=?, exit_reason=?, realized_pnl=?, updated_at=? WHERE id=?",
                 ("ASSIGNED", now, "ASSIGNED", premium, now, p["id"]))
    conn.commit()

    back = conn.execute("SELECT shares, cost_basis FROM stock_positions WHERE ticker = ?", (p["ticker"],)).fetchone()
    pos = conn.execute("SELECT status, exit_reason, realized_pnl FROM positions WHERE id = ?", (p["id"],)).fetchone()
    console.print("[green]applied.[/green]")
    console.print("  read back: %s shares %s at $%s per share" % (p["ticker"], back["shares"], format(back["cost_basis"], ",.2f")))
    console.print("  read back: position %s / %s / realized $%s" % (pos["status"], pos["exit_reason"], format(pos["realized_pnl"], ",.2f")))
    console.print()
    return 0
