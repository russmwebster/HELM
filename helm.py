#!/usr/bin/env python3
# helm.py — HELM command-line entry point
# Symlinked or run directly as: python3 helm.py <command>
# Or via alias: alias helm='python3 ~/Projects/helm/helm.py'

import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

COMMANDS = {
    'setup':     ('helm.cli.setup',     'run',  'Initialize HELM and create your account'),
    'watchlist': ('helm.cli.watchlist', 'run',  'Manage your ticker watchlist'),
    'health':    ('helm.cli.health_cmd', 'run',  'Portfolio CSP health map (opens in browser)'),
    'close':     ('helm.cli.close_cmd', 'run',  'Close a position'),
    'roll':      ('helm.cli.roll_cmd',  'run',  'The roll choices for a short put, covered call or PMCC short, at the bid/ask (s146)'),
    'settings':  ('helm.cli.settings',  'run',  'View or edit strategy settings'),
    'analyze':   ('helm.cli.analyze',   'run',  'Analyze historical outcomes'),
    'import':    ('helm.cli.import_cmd', 'run',  'RETIRED -- trades come in with helm fills'),
    'confirm-fills': ('helm.cli.confirmfills_cmd', 'run', 'RETIRED -- helm fills corrects entry prices'),
    'fills':     ('helm.cli.fills_cmd',  'run',  'Book real trades from a Fidelity activity download (s136)'),
    'gwwatch':   ('helm.cli.gwwatch_cmd', 'run',  'Is the IB gateway answering? (runs itself every 10 min)'),
    'ibkr':      ('helm.cli.ibkr_cmd',  'run',  'Manage IB Gateway connection'),
    'positions': ('helm.cli.positions_cmd', 'run', 'View open positions'),
    'check':     ('helm.cli.check_cmd',     'run', 'Health check on open positions'),
    'quality':   ('helm.cli.quality_cmd', 'run',  'Ownership-quality grade (CSP assign/bail read)'),
    'snapshot':  ('helm.cli.check_cmd',     'run_snapshot', 'Persist a live health snapshot (scheduled writer)'),
    'scan':      ('helm.cli.scan_cmd',      'run', 'Scan optionable tickers for opportunities'),
    'open':      ('helm.cli.open_cmd',      'run', 'Evaluate contracts for a new position (books nothing)'),
    'activity':  ('helm.cli.activity_cmd',  'run', 'RETIRED -- helm fills books closes'),
    'reconcile': ('helm.cli.reconcile_cmd', 'run', 'Compare HELM positions to Fidelity portfolio (read-only)'),
    'theme':     ('helm.cli.theme_cmd',     'run', 'Investment themes — setup, refresh, IPO tracking'),
    'ivr':       ('helm.cli.ivr_cmd',       'run', 'IV Rank / Percentile -- refresh, list, show'),
    'guide':    ('helm.cli.guide_cmd',    'run', 'Strategy guide — matrix, conviction, workflow'),
    'workflow':  ('helm.cli.workflow_cmd',  'run', 'Show trading workflow and command reference'),
    'assign':    ('helm.cli.assign_cmd',     'run', 'Record a short put assignment and the shares it hands you'),
    'settle':    ('helm.cli.settle_cmd',     'run', 'Ask what the broker did with positions that reached expiry'),
    'stock':     ('helm.cli.stock_cmd',     'run', 'Manage stock positions for covered call sizing'),
    'exposure':  ('helm.cli.exposure_cmd',  'run', 'What a candidate would be joining — committed capital by exposure group'),
    'risk':      ('helm.cli.risk_cmd',     'run', 'W160/W194 -- per-trade risk cap breaches and the long-premium sleeve %'),
    'sleeve':    ('helm.sleeve',           'run', 'HELM sleeve -- share, in use, free; each name and sector against its limit'),
    'book':      ('helm.bookview',         'run', 'The book on one screen -- sleeve, limits, Greeks, what-ifs, expiries, resting orders'),
    'puts':      ('helm.cli.puts_cmd',     'run', 'New cash-secured puts under the settled rules -- ranked, sized, recorded (s138)'),
    'pmcc':      ('helm.cli.pmcc_cmd',     'run', 'New PMCCs under the settled rules -- ranked by rent cover, sized, recorded (s147)'),
    'calls':     ('helm.cli.calls_cmd',    'run', 'Covered calls on HELM-held shares (or --if-assigned) under the settled rules (s138)'),
    'recovery':  ('helm.cli.recovery_cmd', 'run', 'If a put is assigned: odds of getting back to basis, covered-call rent -- a model, recorded (s154)'),
    'advise':    ('helm.advice',           'run', 'One plain recommendation per real position, with its reason (s135)'),
    'resell':    ('helm.cli.resell_cmd',    'run', 'Sell the next short against a diagonal\'s long (W180 step 6)'),
    'pending':   ('helm.cli.pending_cmd',   'run', 'Exit flags awaiting a decision — act, or log why you are holding'),
    'status':    ('helm.cli.status_cmd',    'run', 'Portfolio dashboard — positions, P&L, activity'),
    'paper':     ('helm.cli.paper_cmd',   'run',  'Paper book — generate picks from the latest scan'),
    'audit':     ('helm.cli.audit_cmd',   'run', 'End-of-day collection audit -- did the day collect as designed?'),
    'restart':   ('helm.cli.server_cmd',   'run', 'Restart a HELM service — pg (web dashboard) | all; default engine server'),
}

# s142 (Russ, 2026-10-31): `helm help` lists the commands of the strategies HELM
# trades now (puts, covered calls, PMCCs) and the ones that keep it running. The
# rest still run exactly as before -- the board and the scheduled jobs call some of
# them -- but are listed only by `helm legacy`, and go on LEGACY_UNTIL unless Russ
# says otherwise.
MAIN = (
    ("Trading", ("advise", "puts", "calls", "pmcc", "roll", "recovery", "open", "quality", "book", "sleeve")),
    ("Booking", ("fills",)),
    ("Data and checks", ("check", "snapshot", "ivr", "watchlist", "reconcile", "audit")),
    ("Running HELM", ("ibkr", "gwwatch", "restart")),
)
LEGACY_UNTIL = "2026-12-01"
LEGACY = (
    ("Replaced by the import (still run, as fallbacks)", ("close", "assign", "settle", "resell",
                                                          "import", "confirm-fills", "activity")),
    ("Replaced by the board or other commands", ("status", "positions", "health", "pending", "workflow", "guide")),
    ("Retired strategies and old tools", ("scan", "paper", "theme", "exposure", "risk", "stock",
                                          "analyze", "settings", "setup")),
)


def _table(names):
    from rich.table import Table
    from rich import box
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column(style='cyan bold')
    t.add_column(style='dim')
    for cmd in names:
        t.add_row(cmd, COMMANDS[cmd][2])
    return t


def print_help():
    from rich.console import Console
    console = Console()
    console.print()
    console.print('[bold cyan]HELM[/bold cyan] [dim]v0.1.0 — High-conviction Entry & Lifecycle Manager[/dim]')
    console.print()
    console.print('[bold]Usage:[/bold]  cd ~/Projects/helm && helm <command> \\[args]')
    for title, names in MAIN:
        console.print()
        console.print('[bold]%s[/bold]' % title)
        console.print(_table(names))
    console.print('[dim]Older commands still run: [bold]helm legacy[/bold] lists them (due to go on %s).[/dim]'
                  % LEGACY_UNTIL)
    console.print()


def print_legacy():
    from rich.console import Console
    console = Console()
    console.print()
    console.print('[bold]Legacy commands[/bold] [dim]-- each still runs exactly as before; due to go on %s '
                  'unless you keep it.[/dim]' % LEGACY_UNTIL)
    for title, names in LEGACY:
        console.print()
        console.print('[bold]%s[/bold]' % title)
        console.print(_table(names))
    console.print()


# s142 (Russ, 2026-10-31): no double entry -- every trade enters HELM from the
# Fidelity download (helm fills). These three booked trades by other routes; they
# are switched off, not deleted (other commands import their functions).
# HELM_RETIRED_ON=1 runs them for an old harness on a database copy.
RETIRED = {
    'import': 'helm import (the original positions onboarding) is retired',
    'confirm-fills': 'helm confirm-fills is retired -- helm fills corrects entry prices from the download',
    'activity': 'helm activity is retired -- helm fills books closes, expiries and assignments',
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('--help', '-h', 'help'):
        print_help()
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == 'legacy':
        print_legacy()
        sys.exit(0)

    if cmd not in COMMANDS:
        from rich.console import Console
        Console().print(f'[red]Unknown command:[/red] {cmd}')
        Console().print('[dim]Run [bold]helm help[/bold] to see available commands.[/dim]')
        sys.exit(1)

    if cmd in RETIRED and os.environ.get('HELM_RETIRED_ON') != '1':
        from rich.console import Console
        Console().print(f'[yellow]{RETIRED[cmd]}.[/yellow]')
        Console().print('[dim]Trades enter HELM from Fidelity: download Activity & Orders, then '
                        '[bold]cd ~/Projects/helm && helm fills[/bold] (or "Update book from Fidelity" on Today).[/dim]')
        sys.exit(1)

    if cmd in {c for _, names in LEGACY for c in names} and cmd not in RETIRED and sys.stderr.isatty():
        # one dim line for a person at the terminal only -- never on a pipe, so the
        # board's and the scheduled jobs' output is byte-for-byte what it was
        sys.stderr.write('\033[2m(%s is a legacy command, due to go on %s -- helm legacy)\033[0m\n'
                         % (cmd, LEGACY_UNTIL))

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
