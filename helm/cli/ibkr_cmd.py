
# helm/cli/ibkr_cmd.py
# helm ibkr -- IBKR Gateway connection management
#
# Commands:
#   helm ibkr status           Check connection status
#   helm ibkr connect          Test and cache a connection
#   helm ibkr disconnect       Disconnect cleanly
#   helm ibkr free             Free a hogged clientId slot
#
# IB Gateway must be running with API connections enabled.
# Settings: Edit -> Global Configuration -> API -> Settings
#   Socket port: 4002 (live) or 4003 (paper)
#   Enable ActiveX and Socket Clients: checked
#   Read-Only API: checked (recommended for HELM data queries)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

HELP = """
[bold]Usage:[/bold]  helm ibkr <command>

  [cyan]status[/cyan]        Check if IB Gateway is reachable
  [cyan]connect[/cyan]       Test connection and show account info
  [cyan]disconnect[/cyan]    Disconnect cleanly
  [cyan]free[/cyan]          Free a hogged clientId slot

[dim]IB Gateway must be running with API connections enabled.
Default: 127.0.0.1:4002 (live) | ClientId: 10[/dim]
"""


def cmd_status(args):
    from helm.ibkr import check_connection, is_connected, IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID

    console.print()

    # Parse optional port override
    port = IBKR_PORT
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            port = int(args[idx + 1])

    paper = "--paper" in args
    if paper:
        port = 4003

    console.print(f"Checking IB Gateway at [bold]{IBKR_HOST}:{port}[/bold] (clientId {IBKR_CLIENT_ID})...")
    console.print()

    result = check_connection(host=IBKR_HOST, port=port, client_id=IBKR_CLIENT_ID)

    if result["connected"]:
        accounts = ", ".join(result["accounts"]) if result["accounts"] else "unknown"
        console.print(Panel.fit(
            f"[bold green]IB Gateway Connected[/bold green]\n\n"
            f"  Host:      {result['host']}:{result['port']}\n"
            f"  ClientId:  {result['client_id']}\n"
            f"  Accounts:  {accounts}\n\n"
            f"[dim]HELM is ready to use IBKR for live market data.[/dim]",
            border_style="green",
            title="IBKR Status"
        ))
    else:
        error = result.get("error") or "Could not reach IB Gateway"
        console.print(Panel.fit(
            f"[bold red]IB Gateway Not Connected[/bold red]\n\n"
            f"  Host:   {result['host']}:{result['port']}\n"
            f"  Error:  {error}\n\n"
            f"[dim]Make sure IB Gateway is running and API connections are enabled.\n"
            f"Gateway: Edit -> Global Configuration -> API -> Settings\n"
            f"  Socket port: 4002 (live) | 4003 (paper)\n"
            f"  Enable ActiveX and Socket Clients: checked[/dim]",
            border_style="red",
            title="IBKR Status"
        ))
    console.print()


def cmd_connect(args):
    from helm.ibkr import get_ib, IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID

    paper = "--paper" in args
    port = 4003 if paper else IBKR_PORT

    console.print()
    console.print(f"Connecting to IB Gateway at [bold]{IBKR_HOST}:{port}[/bold]...")

    try:
        ib = get_ib(port=port)
        accounts = list(ib.managedAccounts())
        console.print(f"[green]Connected.[/green] Accounts: {', '.join(accounts)}")
        console.print(f"[dim]Connection cached for this session.[/dim]")
    except ConnectionError as e:
        console.print(f"[red]Failed:[/red] {e}")
    console.print()


def cmd_disconnect(args):
    from helm.ibkr import disconnect_ib, is_connected

    console.print()
    if is_connected():
        disconnect_ib()
        console.print("[green]Disconnected from IB Gateway.[/green]")
    else:
        console.print("[dim]Not currently connected.[/dim]")
    console.print()


def cmd_free(args):
    from helm.ibkr import free_client_id, kick_client_id, IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID

    paper = "--paper" in args
    port = 4003 if paper else IBKR_PORT

    console.print()
    console.print(f"Checking clientId {IBKR_CLIENT_ID} on {IBKR_HOST}:{port}...")

    if free_client_id(host=IBKR_HOST, port=port, client_id=IBKR_CLIENT_ID):
        console.print(f"[green]ClientId {IBKR_CLIENT_ID} is free — no action needed.[/green]")
    else:
        console.print(f"[yellow]ClientId {IBKR_CLIENT_ID} is occupied. Attempting to free...[/yellow]")
        if kick_client_id(host=IBKR_HOST, port=port):
            console.print(f"[green]Done. ClientId {IBKR_CLIENT_ID} should now be free.[/green]")
        else:
            console.print(f"[red]Could not free the slot automatically.[/red]")
            console.print(f"[dim]Try: File -> Restart in the IB Gateway UI, then retry.[/dim]")
    console.print()


def run():
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h", "help"):
        console.print(HELP)
        return

    cmd = args[0].lower()
    rest = args[1:]

    if   cmd == "status":      cmd_status(rest)
    elif cmd == "connect":     cmd_connect(rest)
    elif cmd == "disconnect":  cmd_disconnect(rest)
    elif cmd == "free":        cmd_free(rest)
    else:
        console.print(f"[red]Unknown ibkr command:[/red] {cmd}")
        console.print(HELP)


if __name__ == "__main__":
    run()
