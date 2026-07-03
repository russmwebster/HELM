
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
from rich.prompt import Prompt
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


def import_stage4_position(account_id: str, tx: dict) -> bool:
    """
    Import a trade executed directly in Fidelity (Stage 4 only — skipped Stage 3).
    Creates an OPEN position with a partial entry snapshot using current market data.
    """
    import uuid
    from datetime import datetime, date
    from helm.db import transaction as _tx
    from helm.models.position import Position
    from helm.models.leg import Leg

    ticker    = tx["ticker"]
    strike    = tx["strike"]
    exp       = tx["expiration"]
    opt_type  = tx["opt_type"]
    contracts = tx["contracts"]
    fill      = tx["price"]
    tx_date   = tx["date"]

    # Determine strategy from option type and direction
    direction = tx.get("direction", "SHORT")
    if opt_type == "PUT" and direction == "SHORT":
        strategy = "CSP"
        leg_role = "SHORT_PUT"
    elif opt_type == "CALL" and direction == "SHORT":
        strategy = "COVERED_CALL"
        leg_role = "SHORT_CALL"
    elif opt_type == "CALL" and direction == "LONG":
        strategy = "LONG_CALL"
        leg_role = "LONG_CALL"
    elif opt_type == "PUT" and direction == "LONG":
        strategy = "LONG_PUT"
        leg_role = "LONG_PUT"
    else:
        strategy = "CSP"
        leg_role = "SHORT_PUT"

    net_premium = round(fill * 100 * contracts, 2)
    if direction == "LONG":
        net_premium = -abs(net_premium)

    now = datetime.now().isoformat()
    pos_id = f"{ticker}-{strategy}-{date.today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    leg_id = pos_id + f"-{leg_role[:2]}-{uuid.uuid4().hex[:4].upper()}"

    try:
        with _tx() as conn:
            # Create position
            conn.execute("""
                INSERT INTO positions
                (id, account_id, strategy, ticker, status, opened_at,
                 total_contracts, net_premium, notes, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                pos_id, account_id, strategy, ticker, "OPEN",
                f"{tx_date}T00:00:00",
                contracts, net_premium,
                f"Imported from Fidelity activity — Stage 4 trade (no HELM confirmation). "
                f"Entry snapshot is partial (market data at import time).",
                now, now
            ))

            # Create leg
            conn.execute("""
                INSERT INTO legs
                (id, position_id, leg_role, option_type, direction,
                 strike, expiration, contracts, multiplier,
                 open_price, open_date, status, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                leg_id, pos_id, leg_role, opt_type, direction,
                strike, exp, contracts, 100,
                fill, tx_date, "OPEN", now
            ))

            # Create lifecycle event
            conn.execute("""
                INSERT INTO lifecycle_events
                (id, position_id, event_type, occurred_at, narrative)
                VALUES (?,?,?,?,?)
            """, (
                "EVT-" + uuid.uuid4().hex[:8].upper(),
                pos_id, "OPENED", now,
                f"Imported from Fidelity activity: {contracts}x {leg_role} "
                f"${strike} {exp} @ ${fill:.2f} — Stage 4 trade"
            ))

        # Try to fetch partial entry snapshot (current market data as proxy)
        try:
            import yfinance as yf
            import warnings
            warnings.filterwarnings("ignore")
            tk = yf.Ticker(ticker)
            hist = tk.history(period="5d")
            spot = float(hist["Close"].iloc[-1]) if not hist.empty else None

            if spot:
                snap_id = "snap-" + uuid.uuid4().hex[:8].upper()
                with _tx() as conn:
                    conn.execute("""
                        INSERT INTO entry_snapshots
                        (id, position_id, snap_type, spot_price, created_at)
                        VALUES (?,?,?,?,?)
                    """, (snap_id, pos_id, "PARTIAL", spot, now))
        except Exception:
            pass  # Partial snapshot is best-effort

        return pos_id

    except Exception as e:
        console.print(f"[red]Error importing {ticker}:[/red] {e}")
        return None


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

    # --- HELM-038 Gap 2: group multi-leg spreads before per-tx single-leg import ---
    # Imported spreads arrive as separate transactions; partition_new_opens infers
    # credit verticals / iron condors from leg structure so they persist as ONE
    # multi-leg position (real trade date + Fidelity provenance) instead of
    # fragmenting into misclassified single-leg rows. Unrecognized legs fall
    # through unchanged to the single-leg path below.
    if new_opens:
        from helm.cli.activity_grouping import partition_new_opens
        _spreads, _singles = partition_new_opens(new_opens)
        if _spreads:
            from helm.cli.entry_snapshot import open_multileg_with_snapshot
            console.print(f"[bold yellow]Multi-leg spreads detected ({len(_spreads)}):[/bold yellow]")
            console.print("[dim]These import as single multi-leg positions (grouped from their legs).[/dim]")
            console.print()
            _sp_imported = 0
            for _sp in _spreads:
                _legs = _sp["legs"]
                _pf = _sp["position_fields"]
                _pretty = _sp["strategy"].replace("_", " ").title()
                _strikes = "/".join(f"{_l['strike']:.0f}" for _l in _legs)
                _exp = _sp["expiration"][5:]
                _credit = _pf.get("max_profit") or 0.0
                _risk = _pf.get("max_loss") or 0.0
                _trade_date = _sp["source_txs"][0]["date"]
                _ans = Prompt.ask(
                    f"  Import [bold cyan]{_sp['ticker']}[/bold cyan] "
                    f"{_pretty} {_strikes} exp {_exp} x{_sp['contracts']} "
                    f"[dim](credit ${_credit:.0f} / max loss ${_risk:.0f})[/dim]?",
                    choices=["y", "n", "s"], default="y",
                    show_choices=False, show_default=False,
                )
                if _ans == "s":
                    console.print("  [dim]Skipping remaining spreads.[/dim]")
                    break
                if _ans != "y":
                    continue
                try:
                    _pos_id, _leg_ids, _snap_id = open_multileg_with_snapshot(
                        ticker=_sp["ticker"], strategy=_sp["strategy"],
                        legs=_legs, contracts=_sp["contracts"],
                        spot=None, scan_data=None, book="REAL",
                        position_fields=_pf,
                        pricing_source="fidelity_import",
                        opened_at=f"{_trade_date}T00:00:00",
                        notes=("Imported from Fidelity activity — Stage 4 spread "
                               "(no HELM confirmation). Grouped from single-leg "
                               "transactions; entry snapshot is partial."),
                    )
                    _sp_imported += 1
                    console.print(
                        f"  [green]✓[/green] {_sp['ticker']} {_pretty} "
                        f"imported — [dim]{_pos_id}[/dim]"
                    )
                except Exception as _e:
                    console.print(f"  [red]✗[/red] {_sp['ticker']} {_pretty} failed: {_e}")
            if _sp_imported:
                console.print()
                console.print(f"  [green]{_sp_imported} spread(s) imported.[/green]")
            console.print()
        # Only unrecognized single legs continue to the per-transaction path below.
        new_opens = _singles
    # --- end HELM-038 Gap 2 ---

    if new_opens:
        console.print(f"[bold yellow]New positions not in HELM ({len(new_opens)}):[/bold yellow]")
        console.print("[dim]These were opened in Fidelity but not through HELM — skipping Stage 3.[/dim]")
        console.print()

        t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1))
        t.add_column("Ticker",    style="bold cyan", width=8)
        t.add_column("Contract",  width=12)
        t.add_column("Contracts", justify="right", width=10)
        t.add_column("Fill @",    justify="right", width=8)
        t.add_column("Premium",   justify="right", width=10)
        t.add_column("Date",      width=12)

        for tx in new_opens:
            contract_str = f"{tx['opt_type'][0]}{tx['strike']:.0f} {tx['expiration'][5:]}"
            net = tx['price'] * 100 * tx['contracts']
            t.add_row(
                tx['ticker'],
                contract_str,
                str(tx['contracts']),
                f"${tx['price']:.2f}",
                f"${net:.0f}",
                tx['date'],
            )
        console.print(t)
        console.print()

        # Offer to import each new position
        imported = []
        skipped  = []

        console.print("[dim]Import these into HELM? You can accept or skip each one.[/dim]")
        console.print()

        for tx in new_opens:
            contract_str = f"{tx['opt_type'][0]}{tx['strike']:.0f} {tx['expiration'][5:]}"
            answer = Prompt.ask(
                f"  Import [bold cyan]{tx['ticker']}[/bold cyan] "
                f"{contract_str} x{tx['contracts']} @ ${tx['price']:.2f}?",
                choices=["y", "n", "s"],
                default="y",
                show_choices=False,
                show_default=False,
            )
            if answer == "s":
                console.print("  [dim]Skipping remaining imports.[/dim]")
                skipped.extend(new_opens[new_opens.index(tx):])
                break
            if answer == "y":
                pos_id = import_stage4_position(account_id, tx)
                if pos_id:
                    imported.append((tx, pos_id))
                    console.print(
                        f"  [green]✓[/green] {tx['ticker']} {contract_str} "
                        f"imported — [dim]{pos_id}[/dim]"
                    )
            else:
                skipped.append(tx)

        if imported:
            console.print()
            console.print(
                f"  [green]{len(imported)} position(s) imported[/green] "
                f"with partial entry snapshot."
            )
            console.print(
                "  [dim]Note: entry snapshot uses current market data as proxy. "
                "Run helm check to monitor.[/dim]"
            )
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
