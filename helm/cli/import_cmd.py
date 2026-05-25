# helm/cli/import_cmd.py
# helm import fidelity <file> — import positions from a Fidelity portfolio export
#
# Fidelity CSV format:
#   Columns: Account Number, Account Name, Symbol, Description, Quantity,
#            Last Price, Last Price Change, Current Value, Today's G/L $,
#            Today's G/L %, Total G/L $, Total G/L %, % of Account,
#            Cost Basis Total, Average Cost Basis, Type
#
#   Option symbol format: ' -TICKER[YYMMDD][C/P][STRIKE]'
#   e.g. ' -APH260717P125' = short put, APH, Jul 17 2026, $125 strike
#   Quantity negative = short position
#
# What HELM imports:
#   - Options positions as OPEN positions with legs
#   - Stock positions as LONG_STOCK legs (for Covered Call tracking)
#   - Underlying tickers added to watchlist automatically
#   - Strategy inferred from position structure, confirmed by user
#
# What HELM skips:
#   - Money market (SPAXX**)
#   - Mutual funds / ETFs without options intent (FXAIX)
#   - Pending activity rows
#   - Disclaimer rows

import sys
import re
from pathlib import Path
from datetime import datetime, date
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box

from helm.config import get_active_account
from helm.db import get_conn, transaction
from helm.models.account import Account
from helm.models.position import Position
from helm.models.leg import Leg
from helm.models.lifecycle import LifecycleEvent
from helm.models.watchlist import WatchlistItem
from helm.models.pathway import ImportPathway

console = Console()

# ── Fidelity option symbol parser ────────────────────────────────────────────

OPTION_RE = re.compile(
    r'^-?\s*-?([A-Z]+)'       # ticker (after leading dash/space)
    r'(\d{2})(\d{2})(\d{2})'  # YYMMDD
    r'([CP])'                   # C=call, P=put
    r'(\d+\.?\d*)$'          # strike (may have decimal)
)

MONTH_MAP = {
    'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
    'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12
}

def parse_option_symbol(symbol: str) -> Optional[dict]:
    s = symbol.strip().lstrip('-').strip()
    m = OPTION_RE.match(s)
    if not m:
        return None
    ticker, yy, mm, dd, cp, strike = m.groups()
    expiry = f'20{yy}-{mm}-{dd}'
    return {
        'ticker': ticker,
        'expiration': expiry,
        'option_type': 'CALL' if cp == 'C' else 'PUT',
        'strike': float(strike),
    }

def parse_desc_option(description: str) -> Optional[dict]:
    # e.g. 'APH JUL 17 2026 $125 PUT'
    parts = description.strip().split()
    if len(parts) < 5:
        return None
    try:
        ticker = parts[0]
        month = MONTH_MAP.get(parts[1].upper())
        if not month:
            return None
        day = int(parts[2])
        year = int(parts[3])
        strike = float(parts[4].replace('$',''))
        option_type = 'CALL' if parts[5].upper() == 'CALL' else 'PUT'
        expiry = f'{year}-{month:02d}-{day:02d}'
        return {
            'ticker': ticker,
            'expiration': expiry,
            'option_type': option_type,
            'strike': strike,
        }
    except (IndexError, ValueError):
        return None

def clean_money(val) -> Optional[float]:
    if val is None or str(val).strip() in ('', 'nan', 'NaN'):
        return None
    s = str(val).replace('$','').replace(',','').replace('+','').strip()
    try:
        return float(s)
    except ValueError:
        return None

def clean_qty(val) -> Optional[float]:
    if val is None or str(val).strip() in ('', 'nan', 'NaN'):
        return None
    try:
        return float(str(val).strip())
    except ValueError:
        return None

# ── Strategy inference ────────────────────────────────────────────────────────

def infer_strategy(ticker: str, rows: list) -> str:
    """
    Given all rows for a ticker, infer the most likely strategy.
    rows: list of parsed position dicts for this ticker
    """
    option_rows = [r for r in rows if r['is_option']]
    stock_rows  = [r for r in rows if not r['is_option']]

    has_stock = len(stock_rows) > 0
    short_puts  = [r for r in option_rows if r['option_type']=='PUT'  and r['direction']=='SHORT']
    short_calls = [r for r in option_rows if r['option_type']=='CALL' and r['direction']=='SHORT']
    long_calls  = [r for r in option_rows if r['option_type']=='CALL' and r['direction']=='LONG']
    long_puts   = [r for r in option_rows if r['option_type']=='PUT'  and r['direction']=='LONG']

    n_sp = len(short_puts)
    n_sc = len(short_calls)
    n_lc = len(long_calls)
    n_lp = len(long_puts)

    # Covered Call: stock + short call
    if has_stock and n_sc == 1 and n_sp == 0 and n_lc == 0:
        return 'COVERED_CALL'
    # CSP: short put only
    if n_sp == 1 and n_sc == 0 and n_lc == 0 and n_lp == 0 and not has_stock:
        return 'CSP'
    # Bull Put Spread: short put + long put
    if n_sp == 1 and n_lp == 1 and n_sc == 0 and n_lc == 0:
        return 'BULL_PUT_SPREAD'
    # Bear Call Spread: short call + long call
    if n_sc == 1 and n_lc == 1 and n_sp == 0 and n_lp == 0:
        return 'BEAR_CALL_SPREAD'
    # Iron Condor: short put + long put + short call + long call
    if n_sp == 1 and n_lp == 1 and n_sc == 1 and n_lc == 1:
        return 'IRON_CONDOR'
    # Short Strangle: short put + short call
    if n_sp == 1 and n_sc == 1 and n_lc == 0 and n_lp == 0:
        return 'SHORT_STRANGLE'
    # Long Call: long call only
    if n_lc == 1 and n_sp == 0 and n_sc == 0 and n_lp == 0:
        return 'LONG_CALL'
    # PMCC: long call (LEAPS) + short call
    if n_lc == 1 and n_sc == 1 and n_sp == 0 and n_lp == 0:
        return 'PMCC'
    # Diagonal: long call/put + short call/put different expiry
    if (n_lc == 1 and n_sc == 1) or (n_lp == 1 and n_sp == 1):
        return 'DIAGONAL'

    return 'CSP'  # fallback

# ── Main parser ───────────────────────────────────────────────────────────────

def parse_fidelity_csv(filepath: str) -> tuple[list, list]:
    """
    Parse a Fidelity portfolio CSV.
    Returns (accounts, positions) where:
      accounts = [{number, name}]
      positions = [parsed position dicts]
    """
    import pandas as pd

    # Fidelity CSV has account number as first column (acts as row index)
    # With index_col=0, columns shift: index=acct_num, col[0]=acct_name, col[1]=symbol, etc.
    df = pd.read_csv(filepath, encoding='utf-8-sig', index_col=0)

    # Column mapping after index_col=0 shift
    col_acct_name = 'Account Number'   # account name is in this shifted column
    col_symbol    = 'Account Name'     # symbol is here
    col_desc      = 'Symbol'           # description is here
    col_qty       = 'Description'      # quantity is here
    col_price     = 'Quantity'         # last price is here
    col_value     = 'Last Price Change' # current value
    col_cost      = 'Percent Of Account'  # cost basis total
    col_avg_cost  = 'Cost Basis Total'    # average cost basis

    accounts_seen = {}
    parsed = []

    for idx, row in df.iterrows():
        acct_num  = str(idx).strip()
        acct_name = str(row.get(col_acct_name, '')).strip()
        symbol    = str(row.get(col_symbol, '')).strip()
        desc      = str(row.get(col_desc, '')).strip()
        qty_raw   = row.get(col_qty)
        price_raw = row.get(col_price)
        cost_raw  = row.get(col_cost)
        avg_raw   = row.get(col_avg_cost)

        # Skip junk rows
        if not acct_num or acct_num in ('nan',''):
            continue
        if any(x in symbol.upper() for x in ['SPAXX', 'HELD IN', 'PENDING']):
            continue
        if 'data and information' in desc.lower():
            continue
        if 'data and information' in symbol.lower():
            continue
        if symbol in ('nan', '') or desc in ('nan', ''):
            continue

        # Track accounts
        if acct_num not in accounts_seen:
            accounts_seen[acct_num] = acct_name

        qty = clean_qty(qty_raw)
        price = clean_money(price_raw)
        cost_basis = clean_money(cost_raw)
        avg_cost = clean_money(avg_raw)

        # Determine if option
        opt = parse_option_symbol(symbol)
        if opt is None:
            opt = parse_desc_option(desc)

        if opt:
            direction = 'SHORT' if (qty is not None and qty < 0) else 'LONG'
            contracts = int(abs(qty)) if qty is not None else 1
            open_price = avg_cost if avg_cost else (cost_basis / (contracts * 100) if cost_basis else price)

            parsed.append({
                'account_number': acct_num,
                'account_name': acct_name,
                'symbol': symbol.strip().lstrip('-').strip(),
                'ticker': opt['ticker'],
                'expiration': opt['expiration'],
                'option_type': opt['option_type'],
                'strike': opt['strike'],
                'direction': direction,
                'contracts': contracts,
                'open_price': open_price,
                'current_price': price,
                'cost_basis': cost_basis,
                'is_option': True,
                'is_stock': False,
                'description': desc,
            })
        else:
            # Stock/ETF position — skip funds (FXAIX etc.)
            skip_keywords = ['INDEX FUND', 'ETF', 'GOLD TR', 'SPDR', 'FIDELITY']
            if any(k in desc.upper() for k in skip_keywords):
                continue
            if qty is None or qty == 0:
                continue
            parsed.append({
                'account_number': acct_num,
                'account_name': acct_name,
                'symbol': symbol,
                'ticker': symbol,
                'direction': 'LONG' if (qty > 0) else 'SHORT',
                'contracts': int(abs(qty)),
                'open_price': avg_cost or price,
                'current_price': price,
                'cost_basis': cost_basis,
                'is_option': False,
                'is_stock': True,
                'description': desc,
            })

    accounts = [{'number': k, 'name': v} for k, v in accounts_seen.items()]
    return accounts, parsed

# ── Main command ──────────────────────────────────────────────────────────────

def run():
    args = sys.argv[1:]

    if not args or args[0] in ('--help', '-h'):
        console.print()
        console.print('[bold]Usage:[/bold]  helm import fidelity <path/to/portfolio.csv>')
        console.print()
        console.print('Imports open positions from a Fidelity portfolio export into HELM.')
        console.print('[dim]Export from Fidelity: Accounts > Portfolio > Download (CSV)[/dim]')
        console.print()
        return

    if args[0].lower() != 'fidelity':
        console.print(f'[red]Unknown broker:[/red] {args[0]}')
        console.print('[dim]Currently supported: fidelity[/dim]')
        return

    # Get active account early -- needed for pathway lookup
    active_id = get_active_account()
    if not active_id:
        console.print('[red]No active account.[/red] Run [bold]helm setup[/bold] first.')
        return

    if len(args) < 2:
        # No file specified — use configured pathway
        pathways = ImportPathway.for_broker('fidelity', active_id)
        if not pathways:
            console.print('[yellow]No import pathway configured for Fidelity.[/yellow]')
            console.print('[dim]Run [bold]helm setup[/bold] to configure one, or specify a file:')
            console.print('[dim]  helm import fidelity ~/Downloads/Portfolio_Positions_*.csv[/dim]')
            return
        pathway = pathways[0]
        filepath = pathway.find_latest_file()
        if not filepath:
            folder = pathway.resolve_folder()
            console.print(f'[yellow]No files matching[/yellow] [bold]{pathway.file_pattern}[/bold] found in {folder}')
            console.print('[dim]Export your portfolio from Fidelity and try again.[/dim]')
            return
        console.print(f'Using pathway: [bold]{filepath.name}[/bold]')
        console.print(f'[dim]From: {pathway.watch_folder}[/dim]')
        console.print()
    else:
        filepath = Path(args[1]).expanduser()
        # Handle glob pattern if user passed one
        if '*' in str(filepath) or '?' in str(filepath):
            matches = sorted(filepath.parent.glob(filepath.name),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            if not matches:
                console.print(f'[red]No files found matching:[/red] {filepath}')
                return
            filepath = matches[0]
            console.print(f'Using: [bold]{filepath.name}[/bold]')
        elif not filepath.exists():
            console.print(f'[red]File not found:[/red] {filepath}')
            return
        pathway = None  # no pathway for explicit file imports


    helm_account = Account.get(active_id)
    if not helm_account:
        console.print('[red]Active account not found in database.[/red]')
        return

    console.print()
    console.print(Panel.fit(
        f'[bold cyan]HELM Import[/bold cyan] — Fidelity Portfolio\n'
        f'[dim]{filepath.name}[/dim]',
        border_style='cyan'
    ))
    console.print()

    # Parse the file
    console.print(f'Reading [bold]{filepath.name}[/bold]...')
    try:
        fidelity_accounts, rows = parse_fidelity_csv(str(filepath))
    except Exception as e:
        console.print(f'[red]Parse error:[/red] {e}')
        import traceback
        traceback.print_exc()
        return

    if not rows:
        console.print('[yellow]No importable positions found in file.[/yellow]')
        return

    # Show Fidelity accounts found
    console.print(f'  Found [bold]{len(fidelity_accounts)}[/bold] Fidelity account(s) in file:')
    for a in fidelity_accounts:
        count = len([r for r in rows if r['account_number'] == a['number']])
        console.print(f'  [cyan]{a["number"]}[/cyan]  {a["name"]}  ({count} positions)')
    console.print()

    # If multiple Fidelity accounts, ask which to import
    if len(fidelity_accounts) > 1:
        acct_nums = [a['number'] for a in fidelity_accounts]
        choice = Prompt.ask(
            'Import which account? (or [bold]all[/bold])',
            choices=acct_nums + ['all'],
            default='all'
        )
        if choice != 'all':
            rows = [r for r in rows if r['account_number'] == choice]
    console.print()

    # Group positions by ticker for strategy inference
    from collections import defaultdict
    ticker_rows = defaultdict(list)
    for r in rows:
        ticker_rows[r['ticker']].append(r)

    # Build position preview
    positions_to_import = []
    for ticker, ticker_row_list in sorted(ticker_rows.items()):
        strategy = infer_strategy(ticker, ticker_row_list)
        positions_to_import.append({
            'ticker': ticker,
            'strategy': strategy,
            'rows': ticker_row_list,
        })

    # Show preview table
    console.print(f'Found [bold]{len(positions_to_import)}[/bold] position(s) to import:')
    console.print()

    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1))
    t.add_column('#', style='dim', width=3)
    t.add_column('Ticker', style='bold cyan')
    t.add_column('Strategy', style='yellow')
    t.add_column('Legs', style='dim')
    t.add_column('Details')

    for i, pos in enumerate(positions_to_import, 1):
        legs_desc = []
        for r in pos['rows']:
            if r['is_option']:
                dir_str = 'S' if r['direction'] == 'SHORT' else 'L'
                legs_desc.append(
                    f'{dir_str} {r["option_type"][0]} {r["strike"]} {r["expiration"][5:]} x{r["contracts"]}'
                )
            else:
                legs_desc.append(f'{r["contracts"]} shares @ {r["open_price"]}')
        t.add_row(
            str(i),
            pos['ticker'],
            pos['strategy'],
            str(len(pos['rows'])),
            ' | '.join(legs_desc)
        )

    console.print(t)
    console.print()
    console.print('[dim]Strategy is inferred from position structure. You can correct it below.[/dim]')
    console.print()

    if not Confirm.ask(f'Import these {len(positions_to_import)} positions into [bold]{helm_account.nickname}[/bold] ({helm_account.broker})?'):
        console.print('[dim]Import cancelled.[/dim]')
        return

    console.print()

    # Import each position
    imported = 0
    tickers_added = set()
    today = datetime.now().isoformat()
    errors = []

    for pos in positions_to_import:
        ticker = pos['ticker']
        strategy = pos['strategy']

        # Allow strategy correction
        corrected = Prompt.ask(
            f'  [cyan]{ticker}[/cyan] strategy',
            default=strategy
        )
        if corrected.upper() in [
            'CSP','COVERED_CALL','LONG_CALL','PERM','BULL_PUT_SPREAD',
            'BEAR_CALL_SPREAD','IRON_CONDOR','DIAGONAL','PMCC',
            'SHORT_STRANGLE','JADE_LIZARD'
        ]:
            strategy = corrected.upper()

        try:
            # Create position
            p = Position.create(
                account_id=active_id,
                strategy=strategy,
                ticker=ticker,
                opened_at=today,
                notes='Imported from Fidelity portfolio export'
            )

            # Create legs
            net_premium = 0.0
            for r in pos['rows']:
                if r['is_option']:
                    leg_role = (
                        ('SHORT_' if r['direction']=='SHORT' else 'LONG_') +
                        r['option_type']
                    )
                    open_price = r['open_price'] or 0.0
                    leg = Leg.create(
                        position_id=p.id,
                        leg_role=leg_role,
                        direction=r['direction'],
                        open_price=open_price,
                        open_date=today[:10],
                        option_type=r['option_type'],
                        strike=r['strike'],
                        expiration=r['expiration'],
                        contracts=r['contracts'],
                    )
                    # Net premium: SHORT legs collect (positive), LONG pay (negative)
                    if r['direction'] == 'SHORT':
                        net_premium += open_price * r['contracts'] * 100
                    else:
                        net_premium -= open_price * r['contracts'] * 100
                else:
                    leg = Leg.create(
                        position_id=p.id,
                        leg_role='LONG_STOCK',
                        direction='LONG',
                        open_price=r['open_price'] or 0.0,
                        open_date=today[:10],
                        option_type='STOCK',
                        contracts=r['contracts'],
                        multiplier=1,
                    )

            # Update position net premium
            p.net_premium = round(net_premium, 2)
            p.save()

            # Record lifecycle event
            LifecycleEvent.record(
                position_id=p.id,
                event_type='OPENED',
                occurred_at=today,
                narrative=f'Imported from Fidelity portfolio export ({datetime.now().strftime("%Y-%m-%d")})'
            )

            # Add ticker to watchlist
            if WatchlistItem.get(ticker) is None:
                WatchlistItem.add(ticker, willing_to_own=1)
                tickers_added.add(ticker)

            console.print(f'  [green]✓[/green] {ticker} {strategy} imported (id: {p.id[:20]}...)')
            imported += 1

        except Exception as e:
            import traceback
            traceback.print_exc()
            errors.append(f'{ticker}: {e}')
            console.print(f'  [red]✗[/red] {ticker}: {e}')

    # Record on pathway if used
    if 'pathway' in dir() and pathway is not None and imported > 0:
        try:
            pathway.record_import(filepath.name)
        except Exception:
            pass  # non-fatal

    console.print()

    # Summary
    summary_lines = [
        f'[bold green]{imported} position(s) imported[/bold green]',
        f'{len(tickers_added)} ticker(s) added to watchlist: {", ".join(sorted(tickers_added)) or "none"}',
    ]
    if errors:
        summary_lines.append(f'[red]{len(errors)} error(s):[/red] ' + '; '.join(errors))

    summary_lines += [
        '',
        '[dim]Next steps:[/dim]',
        '  [cyan]helm positions[/cyan]       View imported positions',
        '  [cyan]helm check <id>[/cyan]      Run a health check',
        '  [cyan]helm scan[/cyan]            Scan watchlist for new opportunities',
    ]

    console.print(Panel(
        '\n'.join(summary_lines),
        title='Import Complete',
        border_style='green'
    ))
    console.print()


if __name__ == '__main__':
    run()
