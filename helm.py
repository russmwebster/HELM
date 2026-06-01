#!/usr/bin/env python3
# helm.py — HELM command-line entry point
# Symlinked or run directly as: python3 helm.py <command>
# Or via alias: alias helm='python3 ~/Projects/helm/helm.py'

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

COMMANDS = {
    'setup':     ('helm.cli.setup',     'run',  'Initialize HELM and create your account'),
    'watchlist': ('helm.cli.watchlist', 'run',  'Manage your ticker watchlist'),
    'scan':      ('helm.cli.scan',      'run',  'Scan watchlist for opportunities'),
    'open':      ('helm.cli.open_cmd',  'run',  'Open a new position'),
    'positions': ('helm.cli.positions', 'run',  'View open positions'),
    'check':     ('helm.cli.check',     'run',  'Run a health check on a position'),
    'close':     ('helm.cli.close_cmd', 'run',  'Close a position'),
    'roll':      ('helm.cli.roll',      'run',  'Roll a position'),
    'settings':  ('helm.cli.settings',  'run',  'View or edit strategy settings'),
    'analyze':   ('helm.cli.analyze',   'run',  'Analyze historical outcomes'),
    'status':    ('helm.cli.status',    'run',  'Show HELM system status'),
    'import':    ('helm.cli.import_cmd', 'run',  'Import positions from a broker export'),
    'screen':    ('helm.cli.screen',     'run',  'Screen watchlist tickers for optionability'),
    'ibkr':      ('helm.cli.ibkr_cmd',  'run',  'Manage IB Gateway connection'),
    'positions': ('helm.cli.positions_cmd', 'run', 'View open positions'),
    'check':     ('helm.cli.check_cmd',     'run', 'Health check on open positions'),
    'scan':      ('helm.cli.scan_cmd',      'run', 'Scan optionable tickers for opportunities'),
    'open':      ('helm.cli.open_cmd',      'run', 'Evaluate contracts for a new position'),
    'activity':  ('helm.cli.activity_cmd',  'run', 'Import Fidelity activity to close positions'),
    'reconcile': ('helm.cli.reconcile_cmd', 'run', 'Compare HELM positions to Fidelity portfolio'),
    'theme':     ('helm.cli.theme_cmd',     'run', 'Investment themes — setup, refresh, IPO tracking'),
    'ivr':       ('helm.cli.ivr_cmd',       'run', 'IV Rank / Percentile -- refresh, list, show'),
    'notify':    ('helm.cli.notify',        'run', 'Send portfolio summary notification'),
    'workflow':  ('helm.cli.workflow_cmd',  'run', 'Show trading workflow and command reference'),
    'stock':     ('helm.cli.stock_cmd',     'run', 'Manage stock positions for covered call sizing'),
    'status':    ('helm.cli.status_cmd',    'run', 'Portfolio dashboard — positions, P&L, activity'),
}

def print_help():
    from rich.console import Console
    from rich.table import Table
    from rich import box
    console = Console()
    console.print()
    console.print('[bold cyan]HELM[/bold cyan] [dim]v0.1.0 — High-conviction Entry & Lifecycle Manager[/dim]')
    console.print()
    console.print('[bold]Usage:[/bold]  helm <command> [args]')
    console.print()
    t = Table(box=box.SIMPLE, show_header=False, padding=(0,2))
    t.add_column(style='cyan bold')
    t.add_column(style='dim')
    for cmd, (_, _, desc) in COMMANDS.items():
        t.add_row(cmd, desc)
    console.print(t)
    console.print()
    console.print('[dim]First time? Run [bold cyan]helm setup[/bold cyan] to get started.[/dim]')
    console.print()

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('--help', '-h', 'help'):
        print_help()
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd not in COMMANDS:
        from rich.console import Console
        Console().print(f'[red]Unknown command:[/red] {cmd}')
        Console().print('[dim]Run [bold]helm help[/bold] to see available commands.[/dim]')
        sys.exit(1)

    sys.argv = [f'helm {cmd}'] + sys.argv[2:]

    module_path, fn_name, _ = COMMANDS[cmd]
    try:
        import importlib
        module = importlib.import_module(module_path)
        getattr(module, fn_name)()
    except ModuleNotFoundError:
        from rich.console import Console
        Console().print(f'[yellow]Command [bold]{cmd}[/bold] is not yet implemented.[/yellow]')
        sys.exit(1)
    except KeyboardInterrupt:
        from rich.console import Console
        Console().print()
        Console().print('[dim]Cancelled.[/dim]')
        sys.exit(0)

if __name__ == '__main__':
    main()
