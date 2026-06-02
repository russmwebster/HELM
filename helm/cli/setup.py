# helm/cli/setup.py
# helm setup — initialize HELM and create the first account
# First command any user runs. Sets up DB, account, strategy defaults.

import sys
import sqlite3
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.prompt import Prompt, Confirm

# Bootstrap: add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from helm.models.pathway import ImportPathway
from helm.config import (
    DB_PATH, SCHEMA_PATH, SEED_PATH, SCHEMA_VERSION,
    APP_NAME, APP_VERSION, APP_TAGLINE,
    set_active_account, get_active_account
)
from helm.db import init_db, db_summary, get_conn, transaction
from helm.models.account import Account
from helm.models.settings import StrategySettings

console = Console()

STRATEGIES = [
    'CSP','COVERED_CALL','LONG_CALL','PERM',
    'BULL_PUT_SPREAD','BEAR_CALL_SPREAD','IRON_CONDOR',
    'DIAGONAL','PMCC','SHORT_STRANGLE','JADE_LIZARD'
]

# Practitioner defaults per strategy
# (account_id substituted at runtime)
DEFAULTS = {
    'CSP': dict(
        risk_pct_per_trade=0.02,
        entry_iv_rank_min=20, entry_iv_rank_max=70,
        entry_delta_min=0.20, entry_delta_max=0.30,
        entry_dte_min=30, entry_dte_max=45,
        profit_target_pct=0.50, stop_loss_multiplier=2.0,
        dte_exit_threshold=7, dte_review_threshold=21,
        delta_drift_warning=0.20, delta_danger=0.65,
        iv_increase_warning=0.05,
    ),
    'COVERED_CALL': dict(
        risk_pct_per_trade=0.05,
        entry_iv_rank_min=20, entry_iv_rank_max=80,
        entry_delta_min=0.20, entry_delta_max=0.40,
        entry_dte_min=30, entry_dte_max=45,
        profit_target_pct=0.50,
        dte_exit_threshold=7, dte_review_threshold=21,
        delta_drift_warning=0.15, delta_danger=0.60,
        iv_increase_warning=0.08,
    ),
    'LONG_CALL': dict(
        risk_pct_per_trade=0.05,
        entry_iv_rank_max=40,
        entry_delta_min=0.40, entry_delta_max=0.70,
        entry_dte_min=60, entry_dte_max=180,
        profit_target_pct=0.75,
        dte_exit_threshold=21, dte_review_threshold=30,
        delta_drift_warning=0.20,
    ),
    'PERM': dict(
        risk_pct_per_trade=0.03,
        entry_iv_rank_min=30,
        entry_delta_min=0.40, entry_delta_max=0.70,
        entry_dte_min=7, entry_dte_max=21,
        profit_target_pct=0.25,
        dte_review_threshold=3,
        days_before_earnings_exit=1,
        perm_profit_target_pct=0.25,
    ),
    'BULL_PUT_SPREAD': dict(
        risk_pct_per_trade=0.05,
        entry_iv_rank_min=30, entry_iv_rank_max=80,
        entry_delta_min=0.20, entry_delta_max=0.30,
        entry_dte_min=30, entry_dte_max=45,
        profit_target_pct=0.50, stop_loss_multiplier=2.0,
        dte_exit_threshold=7, dte_review_threshold=21,
        delta_drift_warning=0.15, delta_danger=0.50,
        iv_increase_warning=0.05,
    ),
    'BEAR_CALL_SPREAD': dict(
        risk_pct_per_trade=0.05,
        entry_iv_rank_min=30, entry_iv_rank_max=80,
        entry_delta_min=0.15, entry_delta_max=0.25,
        entry_dte_min=30, entry_dte_max=45,
        profit_target_pct=0.50, stop_loss_multiplier=2.0,
        dte_exit_threshold=7, dte_review_threshold=21,
        delta_drift_warning=0.15, delta_danger=0.45,
        iv_increase_warning=0.05,
    ),
    'IRON_CONDOR': dict(
        risk_pct_per_trade=0.05,
        entry_iv_rank_min=40, entry_iv_rank_max=90,
        entry_delta_min=0.10, entry_delta_max=0.20,
        entry_dte_min=30, entry_dte_max=45,
        profit_target_pct=0.50, stop_loss_multiplier=2.0,
        dte_exit_threshold=21, dte_review_threshold=21,
        delta_drift_warning=0.15, delta_danger=0.35,
        iv_increase_warning=0.05, net_delta_warning=30,
    ),
    'DIAGONAL': dict(
        risk_pct_per_trade=0.05,
        entry_delta_min=0.20, entry_delta_max=0.35,
        entry_dte_min=30, entry_dte_max=45,
        profit_target_pct=0.50,
        dte_exit_threshold=30, dte_review_threshold=30,
        delta_danger=0.45,
    ),
    'PMCC': dict(
        risk_pct_per_trade=0.05,
        entry_delta_min=0.15, entry_delta_max=0.35,
        entry_dte_min=30, entry_dte_max=45,
        leaps_delta_min=0.70, leaps_dte_min=365,
        extrinsic_ratio_min=2.0,
        profit_target_pct=0.50,
        dte_exit_threshold=21, dte_review_threshold=90,
        delta_danger=0.50,
    ),
    'SHORT_STRANGLE': dict(
        risk_pct_per_trade=0.02,
        entry_iv_rank_min=40, entry_iv_rank_max=90,
        entry_delta_min=0.18, entry_delta_max=0.22,
        entry_dte_min=28, entry_dte_max=45,
        profit_target_pct=0.50, stop_loss_multiplier=2.0,
        dte_exit_threshold=7, dte_review_threshold=14,
        delta_drift_warning=0.12, delta_danger=0.35,
        iv_increase_warning=0.05, net_delta_warning=30,
    ),
    'JADE_LIZARD': dict(
        risk_pct_per_trade=0.02,
        entry_iv_rank_min=50, entry_iv_rank_max=90,
        entry_delta_min=0.20, entry_delta_max=0.30,
        entry_dte_min=30, entry_dte_max=60,
        profit_target_pct=0.50, stop_loss_multiplier=2.0,
        dte_exit_threshold=14, dte_review_threshold=21,
        delta_drift_warning=0.15, delta_danger=0.50,
        iv_increase_warning=0.05,
        enforce_credit_exceeds_width=1,
    ),
}

import uuid
from pathlib import Path

def seed_strategy_defaults(account_id: str) -> int:
    """Insert practitioner defaults for all 11 strategies."""
    seeded = 0
    with transaction() as conn:
        for strategy, defaults in DEFAULTS.items():
            setting_id = f'default_{strategy}_{account_id}'
            # Check if already exists
            existing = conn.execute(
                'SELECT id FROM strategy_settings WHERE account_id = ? AND strategy = ?',
                (account_id, strategy)
            ).fetchone()
            if existing:
                continue
            fields = ['id','account_id','strategy','is_default','last_modified']
            values = [setting_id, account_id, strategy, 1, datetime.now().isoformat()]
            for k, v in defaults.items():
                fields.append(k)
                values.append(v)
            placeholders = ','.join(['?' for _ in values])
            conn.execute(
                f'INSERT INTO strategy_settings ({",".join(fields)}) VALUES ({placeholders})',
                values
            )
            seeded += 1
    return seeded


def run():
    console.print()
    console.print(Panel.fit(
        f'[bold cyan]{APP_NAME}[/bold cyan] [dim]v{APP_VERSION}[/dim]\n'
        f'[dim]{APP_TAGLINE}[/dim]',
        border_style='cyan'
    ))
    console.print()

    # Check if already set up
    existing_account = get_active_account()
    if existing_account:
        acct = Account.get(existing_account)
        if acct:
            console.print(f'[yellow]HELM is already set up.[/yellow]')
            console.print(f'Active account: [bold]{acct.nickname}[/bold] ({acct.broker})')
            console.print()
            if not Confirm.ask('Re-run setup? This will not delete existing data'):
                console.print('[dim]Setup cancelled.[/dim]')
                return
            console.print()

    # Step 1: Initialize database
    console.print('[bold]Step 1 of 4:[/bold] Initializing database...')
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        init_db()
        console.print(f'  [green]OK[/green] Schema v{SCHEMA_VERSION} applied')
        console.print(f'  [green]OK[/green] Database at {DB_PATH}')
    except Exception as e:
        console.print(f'  [red]ERROR[/red] {e}')
        sys.exit(1)
    console.print()

    # Step 2: Create account
    console.print('[bold]Step 2 of 4:[/bold] Create your account')
    console.print('[dim]This is your broker account that HELM will manage.[/dim]')
    console.print()

    broker = Prompt.ask('  Broker name', default='Fidelity')
    nickname = Prompt.ask('  Account nickname', default='Main')

    bp_str = Prompt.ask(
        '  Starting buying power [dim](optional, press Enter to skip)[/dim]',
        default=''
    )
    buying_power = None
    if bp_str.strip():
        try:
            buying_power = float(bp_str.replace(',','').replace('$',''))
        except ValueError:
            console.print('  [yellow]Could not parse buying power — skipping.[/yellow]')

    pv_str = Prompt.ask(
        '  Portfolio value [dim](optional, press Enter to skip)[/dim]',
        default=''
    )
    portfolio_value = None
    if pv_str.strip():
        try:
            portfolio_value = float(pv_str.replace(',','').replace('$',''))
        except ValueError:
            console.print('  [yellow]Could not parse portfolio value — skipping.[/yellow]')

    # Reuse existing active account if re-running setup
    existing_id = get_active_account()
    existing_acct = Account.get(existing_id) if existing_id else None

    try:
        if existing_acct:
            # Update existing account with new details
            existing_acct.broker = broker
            existing_acct.nickname = nickname
            if buying_power is not None:
                existing_acct.buying_power = buying_power
            if portfolio_value is not None:
                existing_acct.portfolio_value = portfolio_value
            existing_acct.save()
            acct = existing_acct
            console.print()
            console.print(f'  [green]OK[/green] Account updated: [bold]{nickname}[/bold] ({broker})')
        else:
            account_id = broker.lower().replace(' ','_') + '_' + uuid.uuid4().hex[:6]
            acct = Account.create(
                broker=broker,
                nickname=nickname,
                id=account_id,
                buying_power=buying_power,
                portfolio_value=portfolio_value,
            )
            set_active_account(acct.id)
            console.print()
            console.print(f'  [green]OK[/green] Account created: [bold]{nickname}[/bold] ({broker})')
    except Exception as e:
        console.print(f'  [red]ERROR[/red] {e}')
        sys.exit(1)
    console.print()

    # Step 3: Seed strategy defaults
    console.print('[bold]Step 3 of 4:[/bold] Seeding strategy defaults...')
    try:
        seeded = seed_strategy_defaults(acct.id)
        console.print(f'  [green]OK[/green] {seeded} strategies configured with practitioner defaults')
        console.print(f'  [dim]Run [bold]helm settings[/bold] to view or customize any strategy.[/dim]')
    except Exception as e:
        console.print(f'  [red]ERROR[/red] {e}')
        sys.exit(1)
    console.print()

    # Step 4: Configure import pathway
    console.print('[bold]Step 4 of 4:[/bold] Configure your import pathway')
    console.print('[dim]HELM needs to know where your broker saves portfolio exports.[/dim]')
    console.print()

    setup_pathway = Confirm.ask('  Set up a Fidelity import pathway now?', default=True)
    if setup_pathway:
        default_folder = str(Path.home() / 'Downloads')
        watch_folder = Prompt.ask(
            '  Where does Fidelity save portfolio exports?',
            default=default_folder
        )
        file_pattern = Prompt.ask(
            '  File pattern to match',
            default='Portfolio_Positions_*.csv'
        )
        try:
            pathway = ImportPathway.create(
                account_id=acct.id,
                broker='fidelity',
                watch_folder=watch_folder,
                file_pattern=file_pattern,
                import_both_accounts=1,
            )
            # Check if any files already exist there
            latest = pathway.find_latest_file()
            if latest:
                console.print(f'  [green]OK[/green] Pathway saved — latest file found: [bold]{latest.name}[/bold]')
                console.print(f'  [dim]Run [bold]helm import fidelity[/bold] to import it now.[/dim]')
            else:
                console.print(f'  [green]OK[/green] Pathway saved: {watch_folder}/{file_pattern}')
                console.print(f'  [dim]No files found yet — export from Fidelity and run [bold]helm import fidelity[/bold][/dim]')
        except Exception as e:
            console.print(f'  [yellow]Warning:[/yellow] Could not save pathway: {e}')
    else:
        console.print('  [dim]Skipped. Run [bold]helm pathway add[/bold] anytime to configure.[/dim]')
    console.print()

    # Summary
    summary = db_summary()
    counts = summary['counts']

    # ── Optional: Investment Themes ─────────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Optional Component: Investment Themes[/bold cyan]\n\n"
        "[dim]Organize your watchlist by conviction areas — AI, Robotics, Nuclear, etc.\n"
        "HELM will track established leaders, emerging players, and pre-IPO companies\n"
        "in each theme, and nudge you when it's time to refresh.[/dim]",
        border_style="cyan"
    ))
    console.print()

    from rich.prompt import Confirm as _Confirm
    if _Confirm.ask("  Set up Investment Themes now?", default=False):
        try:
            from helm.cli.theme_cmd import cmd_setup
            cmd_setup([])
        except Exception as _e:
            console.print(f"[yellow]Theme setup skipped:[/yellow] {_e}")
    else:
        console.print("[dim]  Skipped — run [bold]helm theme setup[/bold] anytime to add themes.[/dim]")
    console.print()

    console.print(Panel.fit(
        f'[bold green]HELM is ready.[/bold green]\n\n'
        f'  Database:   {DB_PATH}\n'
        f'  Account:    {nickname} ({broker})\n'
        f'  Strategies: {counts["strategy_settings"]} configured\n\n'
        f'[dim]Next steps:[/dim]\n'
        f'  [cyan]helm watchlist add AAPL[/cyan]   Add tickers to your universe\n'
        f'  [cyan]helm scan[/cyan]                  Scan for opportunities\n'
        f'  [cyan]helm settings[/cyan]              View or adjust strategy rules
  [cyan]helm guide[/cyan]                 Understand how HELM selects strategies',
        border_style='green',
        title='Setup Complete'
    ))
    console.print()


if __name__ == '__main__':
    run()
