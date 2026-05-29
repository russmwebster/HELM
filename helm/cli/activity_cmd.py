
# helm/cli/activity_cmd.py
# helm activity -- import Fidelity account history to close/update positions
#
# Reads the most recent Accounts_History*.csv from Downloads and:
#   - Matches CLOSING transactions to open HELM positions
#   - Marks matched positions as CLOSED with actual fill, commission, P&L
#   - Reports OPENING transactions not yet in HELM (for awareness)
#   - Skips OPENING transactions already in HELM
#
# Usage:
#   helm activity                    Auto-find latest Accounts_History*.csv
#   helm activity ~/path/to/file.csv Explicit file

import sys
import re
import glob
import logging
import warnings
from pathlib import Path
from datetime import datetime, date
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.prompt import Confirm

from helm.config import get_active_account
from helm.db import get_conn, transaction

console = Console()

# ── Option symbol parser ──────────────────────────────────────────────────────

def parse_option_symbol(symbol: str) -> Optional[dict]:
    """
    Parse a Fidelity option symbol like -AVGO260618P400
    Returns dict with ticker, expiration, opt_type, strike or None.
    """
    symbol = symbol.strip().lstrip("-")
    # Pattern: TICKER + YYMMDD + C/P + STRIKE
    m = re.match(r'^([A-Z]+)(\d{6})([CP])(\d+(?:\.\d+)?)$', symbol)
    if not m:
        return None
    ticker, date_str, cp, strike_str = m.groups()
    try:
        exp = datetime.strptime(date_str, "%y%m%d").strftime("%Y-%m-%d")
        return {
            "ticker": ticker,
            "expiration": exp,
            "opt_type": "CALL" if cp == "C" else "PUT",
            "strike": float(strike_str),
        }
    except Exception:
        return None


# ── Fidelity activity CSV parser ──────────────────────────────────────────────

def parse_activity_csv(filepath: str) -> list:
    """
    Parse a Fidelity Accounts_History CSV.
    Returns list of transaction dicts.
    """
    import csv

    transactions = []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        # Skip blank lines before header
        lines = [l for l in f.readlines() if l.strip()]

    reader = csv.DictReader(lines)
    for row in reader:
        if row is None:
            continue
        # Skip rows where all values are None or empty
        if not any(v and str(v).strip() for v in row.values()):
            continue
        def safe(key, default=""):
            v = row.get(key)
            return str(v).strip() if v is not None else default
        action = safe("Action")
        symbol = safe("Symbol")
        price_str = safe("Price ($)")
        qty_str = safe("Quantity")
        comm_str = safe("Commission ($)")
        fees_str = safe("Fees ($)")
        amount_str = safe("Amount ($)")
        run_date = safe("Run Date")
        account_num = safe("Account Number").strip('"')

        # Only process option transactions
        parsed = parse_option_symbol(symbol)
        if not parsed:
            continue

        # Determine if opening or closing
        if "OPENING" in action.upper():
            tx_type = "OPEN"
        elif "CLOSING" in action.upper():
            tx_type = "CLOSE"
        else:
            continue

        # Determine direction
        if "YOU SOLD" in action.upper():
            direction = "SHORT"
        elif "YOU BOUGHT" in action.upper():
            direction = "LONG"
        else:
            continue

        try:
            price = float(price_str) if price_str else None
            qty = int(float(qty_str)) if qty_str else None
            if qty is not None:
                qty = abs(qty)  # always positive
            commission = float(comm_str) if comm_str else 0.0
            fees = float(fees_str) if fees_str else 0.0
            amount = float(amount_str) if amount_str else None
        except (ValueError, TypeError):
            continue

        try:
            run_date_parsed = datetime.strptime(run_date, "%m/%d/%Y").strftime("%Y-%m-%d")
        except Exception:
            run_date_parsed = run_date

        transactions.append({
            "date": run_date_parsed,
            "account_num": account_num,
            "action": action,
            "tx_type": tx_type,
            "direction": direction,
            "symbol": symbol,
            "ticker": parsed["ticker"],
            "expiration": parsed["expiration"],
            "opt_type": parsed["opt_type"],
            "strike": parsed["strike"],
            "price": price,
            "contracts": qty,
            "commission": commission,
            "fees": fees,
            "amount": amount,
        })

    return transactions


def make_tx_hash(tx: dict) -> str:
    """Create a unique hash for a transaction to detect duplicates."""
    import hashlib
    key = f"{tx['date']}|{tx['symbol']}|{tx['action'][:20]}|{tx['contracts']}|{tx['price']}"
    return hashlib.md5(key.encode()).hexdigest()


def filter_unprocessed(transactions: list) -> tuple[list, list]:
    """
    Filter out already-processed transactions.
    Returns (unprocessed, already_processed).
    """
    from helm.db import get_conn
    conn = get_conn()
    try:
        unprocessed = []
        already_processed = []
        for tx in transactions:
            tx_hash = make_tx_hash(tx)
            tx['_hash'] = tx_hash
            exists = conn.execute(
                "SELECT 1 FROM processed_transactions WHERE tx_hash=?",
                (tx_hash,)
            ).fetchone()
            if exists:
                already_processed.append(tx)
            else:
                unprocessed.append(tx)
        return unprocessed, already_processed
    finally:
        conn.close()


def mark_processed(transactions: list) -> None:
    """Mark transactions as processed so they won't be re-processed."""
    from helm.db import transaction as _tx
    import uuid
    from datetime import datetime
    now = datetime.now().isoformat()
    with _tx() as conn:
        for tx in transactions:
            tx_hash = tx.get('_hash') or make_tx_hash(tx)
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO processed_transactions
                    (id, run_date, account_num, symbol, action, quantity, price, amount, tx_hash, processed_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    'PTX-' + uuid.uuid4().hex[:8].upper(),
                    tx['date'], tx.get('account_num', ''),
                    tx['symbol'], tx['action'][:40],
                    tx.get('contracts'), tx.get('price'),
                    tx.get('amount'), tx_hash, now
                ))
            except Exception:
                pass


# ── Position matching ─────────────────────────────────────────────────────────

def find_matching_position(account_id: str, ticker: str, expiration: str,
                           strike: float, opt_type: str) -> Optional[dict]:
    """
    Find an open or pending HELM position matching the given contract details.
    Matches on ticker + expiration + strike + opt_type.
    """
    conn = get_conn()
    try:
        # Find open or pending positions for this ticker
        positions = conn.execute(
            "SELECT * FROM positions WHERE account_id=? AND ticker=? AND status IN ('OPEN','PENDING')",
            (account_id, ticker)
        ).fetchall()

        for pos in positions:
            # Check legs for matching strike/expiration/opt_type
            legs = conn.execute(
                "SELECT * FROM legs WHERE position_id=? AND status='OPEN'",
                (pos["id"],)
            ).fetchall()
            for leg in legs:
                if (leg["expiration"] == expiration and
                    abs(leg["strike"] - strike) < 0.01 and
                    leg["option_type"] == opt_type):
                    return {"position": dict(pos), "leg": dict(leg)}
        return None
    finally:
        conn.close()



def confirm_pending_position(position_id: str, leg_id: str, actual_price: float,
                              contracts: int, commission: float, fees: float,
                              trade_date: str, direction: str) -> None:
    """
    Transition a PENDING position to OPEN with actual fill price.
    Updates the leg open_price and position net_premium with actuals.
    """
    # Recalculate net premium with actual fill
    net_premium = actual_price * 100 * contracts
    if direction == "LONG":
        net_premium = -net_premium

    now = __import__("datetime").datetime.now().isoformat()

    with transaction() as conn:
        # Update leg with actual fill price
        conn.execute(
            "UPDATE legs SET open_price=?, open_date=? WHERE id=?",
            (actual_price, trade_date, leg_id)
        )
        # Transition position to OPEN with actual premium
        conn.execute(
            "UPDATE positions SET status='OPEN', net_premium=?, notes=? WHERE id=?",
            (round(net_premium, 2),
             f"Confirmed via activity import @ ${actual_price:.2f} on {trade_date}",
             position_id)
        )
        # Log lifecycle event
        conn.execute(
            """INSERT INTO lifecycle_events
               (id, position_id, event_type, occurred_at, option_price, narrative)
               VALUES (?,?,?,?,?,?)""",
            (
                "EVT-" + __import__("uuid").uuid4().hex[:8].upper(),
                position_id, "OPENED", now,
                actual_price,
                f"Confirmed execution @ ${actual_price:.2f} | commission ${commission:.2f} | fees ${fees:.2f}"
            )
        )


def close_position(position_id: str, leg_id: str, close_price: float,
                   contracts: int, commission: float, fees: float,
                   close_date: str, direction: str, open_price: float) -> float:
    """
    Mark a position and its leg as CLOSED. Returns realized P&L.
    """
    # P&L calculation:
    # SHORT (sold to open): P&L = (open_price - close_price) * contracts * 100
    # LONG (bought to open): P&L = (close_price - open_price) * contracts * 100
    if direction == "SHORT":
        pnl = (open_price - close_price) * contracts * 100
    else:
        pnl = (close_price - open_price) * contracts * 100

    # Subtract commissions and fees
    pnl -= (commission + fees)
    pnl = round(pnl, 2)

    now = datetime.now().isoformat()

    with transaction() as conn:
        # Close the leg
        conn.execute(
            "UPDATE legs SET status='CLOSED', close_price=?, close_date=? WHERE id=?",
            (close_price, close_date, leg_id)
        )
        # Close the position
        conn.execute(
            """UPDATE positions SET status='CLOSED', closed_at=?,
               realized_pnl=? WHERE id=?""",
            (close_date + "T00:00:00", pnl, position_id)
        )
        # Log lifecycle event
        conn.execute(
            """INSERT INTO lifecycle_events
               (id, position_id, event_type, occurred_at, option_price, pnl_at_event, narrative)
               VALUES (?,?,?,?,?,?,?)""",
            (
                "EVT-" + __import__("uuid").uuid4().hex[:8].upper(),
                position_id, "CLOSED", now,
                close_price, pnl,
                f"Closed via activity import @ ${close_price:.2f} | P&L: ${pnl:.2f}"
            )
        )

    return pnl


# ── Main command ──────────────────────────────────────────────────────────────

def run():
    args = sys.argv[1:]

    if not get_active_account():
        console.print("[red]No active account. Run helm setup first.[/red]")
        return

    account_id = get_active_account()

    # Find activity file
    if args and not args[0].startswith("--"):
        filepath = Path(args[0]).expanduser()
        if not filepath.exists():
            console.print(f"[red]File not found:[/red] {filepath}")
            return
    else:
        # Auto-find latest Accounts_History*.csv in Downloads
        pattern = str(Path.home() / "Downloads" / "Accounts_History*.csv")
        matches = sorted(
            [Path(p) for p in glob.glob(pattern)],
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        if not matches:
            console.print("[red]No Accounts_History*.csv found in Downloads.[/red]")
            console.print("[dim]Export from Fidelity: Accounts -> History -> Download[/dim]")
            return
        filepath = matches[0]

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Activity Import[/bold cyan]\n"
        f"[dim]{filepath.name}[/dim]",
        border_style="cyan"
    ))
    console.print()
    console.print(f"Reading [bold]{filepath.name}[/bold]...")

    try:
        transactions = parse_activity_csv(str(filepath))
    except Exception as e:
        console.print(f"[red]Parse error:[/red] {e}")
        return

    if not transactions:
        console.print("[yellow]No option transactions found in file.[/yellow]")
        return

    # Filter out already-processed transactions
    transactions, already_done = filter_unprocessed(transactions)
    
    total_found = len(transactions) + len(already_done)
    console.print(f"Found [bold]{total_found}[/bold] option transaction(s) — [bold]{len(transactions)}[/bold] new, [dim]{len(already_done)} already processed[/dim].")
    console.print()
    
    if not transactions:
        console.print("[dim]All transactions already processed. Nothing to do.[/dim]")
        console.print()
        return
    
    # Mark all transactions as seen immediately -- prevents re-processing
    # even if user cancels. We've already evaluated them.
    mark_processed(transactions)
    
    # Mark all transactions as seen immediately -- prevents re-processing
    # even if user cancels. We've already evaluated them.
    mark_processed(transactions)

    # Separate opens and closes
    closes = [t for t in transactions if t["tx_type"] == "CLOSE"]
    opens  = [t for t in transactions if t["tx_type"] == "OPEN"]

    # Process closing transactions
    matched_closes = []
    unmatched_closes = []

    for tx in closes:
        match = find_matching_position(
            account_id, tx["ticker"], tx["expiration"],
            tx["strike"], tx["opt_type"]
        )
        if match:
            tx["_match"] = match
            matched_closes.append(tx)
        else:
            unmatched_closes.append(tx)

    # Process opening transactions -- check if already in HELM
    already_open = []
    new_opens = []

    pending_confirms = []
    for tx in opens:
        match = find_matching_position(
            account_id, tx["ticker"], tx["expiration"],
            tx["strike"], tx["opt_type"]
        )
        if match:
            if match["position"]["status"] == "PENDING":
                tx["_match"] = match
                pending_confirms.append(tx)
            else:
                already_open.append(tx)
        else:
            new_opens.append(tx)

    # Display summary
    if matched_closes:
        console.print(f"[bold]Positions to close ({len(matched_closes)}):[/bold]")
        console.print()
        t = Table(box=box.SIMPLE_HEAD, padding=(0,1))
        t.add_column("Ticker",  style="bold cyan", width=7)
        t.add_column("Strategy", width=14)
        t.add_column("Contract", width=20)
        t.add_column("Open @", justify="right", width=8)
        t.add_column("Close @", justify="right", width=8)
        t.add_column("Contracts", justify="right", width=9)
        t.add_column("P&L", justify="right", width=10)

        for tx in matched_closes:
            pos = tx["_match"]["position"]
            leg = tx["_match"]["leg"]
            open_price = leg["open_price"]
            close_price = tx["price"]
            direction = leg["direction"]
            contracts = tx["contracts"]

            if direction == "SHORT":
                pnl = (open_price - close_price) * contracts * 100
            else:
                pnl = (close_price - open_price) * contracts * 100
            pnl -= (tx["commission"] + tx["fees"])

            pnl_str = f"[green]+${pnl:.0f}[/green]" if pnl >= 0 else f"[red]-${abs(pnl):.0f}[/red]"
            contract_str = f"{tx['opt_type'][0]}{tx['strike']:.0f} {tx['expiration'][5:]}"

            t.add_row(
                tx["ticker"], pos["strategy"], contract_str,
                f"${open_price:.2f}", f"${close_price:.2f}",
                str(contracts), pnl_str
            )

        console.print(t)
        console.print()

    if pending_confirms:
        console.print(f"[bold green]Pending positions to confirm ({len(pending_confirms)}):[/bold green]")
        console.print("[dim]These were logged in HELM and are now confirmed executed in Fidelity.[/dim]")
        console.print()
        t2 = Table(box=box.SIMPLE_HEAD, padding=(0,1))
        t2.add_column("Ticker",   style="bold cyan", width=7)
        t2.add_column("Strategy", width=14)
        t2.add_column("Contract", width=20)
        t2.add_column("HELM @",   justify="right", width=8)
        t2.add_column("Actual @", justify="right", width=9)
        t2.add_column("Contracts",justify="right", width=9)
        for tx in pending_confirms:
            pos = tx["_match"]["position"]
            leg = tx["_match"]["leg"]
            helm_price = leg["open_price"]
            actual_price = tx["price"]
            diff = actual_price - helm_price
            diff_str = f"[green]+${diff:.2f}[/green]" if diff > 0 else f"[red]-${abs(diff):.2f}[/red]" if diff < 0 else "[dim]exact[/dim]"
            contract_str = f"{tx['opt_type'][0]}{tx['strike']:.0f} {tx['expiration'][5:]}"
            t2.add_row(
                tx["ticker"], pos["strategy"], contract_str,
                f"${helm_price:.2f}", f"${actual_price:.2f} ({diff_str})",
                str(tx["contracts"])
            )
        console.print(t2)
        console.print()

    if new_opens:
        console.print(f"[bold yellow]New positions not in HELM ({len(new_opens)}):[/bold yellow]")
        console.print("[dim]These were opened in Fidelity but not through HELM.[/dim]")
        console.print("[dim]Run helm import fidelity to bring them in.[/dim]")
        console.print()
        for tx in new_opens:
            contract_str = f"{tx['opt_type'][0]}{tx['strike']:.0f} {tx['expiration'][5:]}"
            console.print(f"  [cyan]{tx['ticker']}[/cyan]  {contract_str}  x{tx['contracts']}  @ ${tx['price']:.2f}  ({tx['date']})")
        console.print()

    if already_open:
        console.print(f"[dim]{len(already_open)} opening transaction(s) already in HELM — skipped.[/dim]")
        console.print()

    if unmatched_closes:
        console.print(f"[dim]{len(unmatched_closes)} closing transaction(s) had no matching open position in HELM.[/dim]")
        console.print()

    if not matched_closes and not pending_confirms:
        console.print("[dim]No positions to close or confirm.[/dim]")
        console.print()
        return

    # Confirm pending positions
    if pending_confirms:
        if Confirm.ask(f"Confirm {len(pending_confirms)} pending position(s) as executed?", default=True):
            console.print()
            for tx in pending_confirms:
                pos = tx["_match"]["position"]
                leg = tx["_match"]["leg"]
                try:
                    confirm_pending_position(
                        position_id=pos["id"],
                        leg_id=leg["id"],
                        actual_price=tx["price"],
                        contracts=tx["contracts"],
                        commission=tx["commission"],
                        fees=tx["fees"],
                        trade_date=tx["date"],
                        direction=leg["direction"],
                    )
                    console.print(f"  [green]✓[/green] {tx['ticker']} {tx['opt_type'][0]}{tx['strike']:.0f} {tx['expiration'][5:]} confirmed @ ${tx['price']:.2f} → [green]OPEN[/green]")
                except Exception as e:
                    console.print(f"  [red]✗[/red] {tx['ticker']}: {e}")
            console.print()

    if not matched_closes:
        console.print("[dim]No positions to close.[/dim]")
        console.print()
        return

    # Confirm
    if not Confirm.ask(f"Close {len(matched_closes)} position(s) in HELM?", default=True):
        console.print("[dim]Cancelled.[/dim]")
        return

    # Apply closings
    console.print()
    closed = 0
    total_pnl = 0.0

    for tx in matched_closes:
        pos = tx["_match"]["position"]
        leg = tx["_match"]["leg"]
        try:
            pnl = close_position(
                position_id=pos["id"],
                leg_id=leg["id"],
                close_price=tx["price"],
                contracts=tx["contracts"],
                commission=tx["commission"],
                fees=tx["fees"],
                close_date=tx["date"],
                direction=leg["direction"],
                open_price=leg["open_price"],
            )
            pnl_str = f"[green]+${pnl:.0f}[/green]" if pnl >= 0 else f"[red]-${abs(pnl):.0f}[/red]"
            console.print(f"  [green]✓[/green] {tx['ticker']} {tx['opt_type'][0]}{tx['strike']:.0f} {tx['expiration'][5:]} closed @ ${tx['price']:.2f}  P&L: {pnl_str}")
            closed += 1
            total_pnl += pnl
        except Exception as e:
            console.print(f"  [red]✗[/red] {tx['ticker']}: {e}")

    console.print()
    total_str = f"[green]+${total_pnl:.0f}[/green]" if total_pnl >= 0 else f"[red]-${abs(total_pnl):.0f}[/red]"
    # Mark ALL processed transactions (closes, confirms, new opens) as done
    all_processed = matched_closes + pending_confirms
    mark_processed(all_processed)
    # Also mark skipped ones so they don't keep showing up
    mark_processed([t for t in already_open])

    console.print(Panel.fit(
        f"[bold green]{closed} position(s) closed[/bold green]\n"
        f"Total realized P&L: {total_str}\n\n"
        f"[dim]Run [bold]helm positions --all[/bold] to see closed positions.[/dim]",
        border_style="green",
        title="Activity Import Complete"
    ))
    console.print()


if __name__ == "__main__":
    run()
