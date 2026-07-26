"""helm restart -- restart a launchd-managed HELM service.

  helm restart              the engine server (unchanged default)
  helm restart pg           the PG web app -- the web/desktop dashboard
  helm restart trial        the free-data trial web app
  helm restart all          every HELM agent above
  helm restart list         show the targets, and which are up, touching nothing

Three HELM services run as launchd agents with KeepAlive, and until W59 exactly
one of them had a command: `helm restart` hardcoded com.helm.server, so the
other two needed a `launchctl kickstart` line remembered from nowhere. Reaching
instead for the dev launcher (`helm-pg/run.sh`) starts a SECOND process that
fights the agent for the port -- the same trap this module was written to close
for helm-servers.sh, left open for PG.

Each target wraps `launchctl kickstart -k gui/<uid>/<label>`, which kills and
relaunches in one shot, re-reading code on the way up. Use after editing server
or web-app code.

FIREWALL: labels come only from the TARGETS table below. A label is never taken
from the command line, and anything outside the com.helm.* namespace is refused
before launchctl is called -- COTS (com.cots.*, cots.local:8765) is a separate
live system and this command must never be able to reach it, however it is
invoked.

Exit 0 only when every requested restart was CONFIRMED back up. A kickstart that
returns 0 while the port stays silent is reported as a problem, not a success --
"the command exited 0" and "the service is answering" are different claims.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

# name -> (launchd label, description, probe hosts, port, url, log hint)
# Two probe hosts because the bind address is the agent's business, not ours:
# failing to reach 127.0.0.1 is not evidence a service is down.
TARGETS = {
    "server": ("com.helm.server", "HELM engine server",
               ("helm.local", "127.0.0.1"), 8766, "http://helm.local:8766",
               "launchctl print gui/$(id -u)/com.helm.server | grep -i path"),
    "pg": ("com.helm.pg", "PG web app (the web dashboard)",
           ("127.0.0.1",), 8770, "http://127.0.0.1:8770",
           "tail -20 ~/Projects/helm-pg/pg.log"),
    "trial": ("com.helm.trial.ui", "free-data trial web app",
              ("127.0.0.1",), 8771, "http://127.0.0.1:8771",
              "tail -20 ~/Projects/helm-pg/pg-trial.log"),
}

# Spellings that are obviously the same thing. Deliberately a fixed map rather
# than fuzzy matching: a typo must be refused, not guessed at.
ALIASES = {
    "engine": "server", "helm": "server",
    "web": "pg", "ui": "pg", "dashboard": "pg", "desktop": "pg",
    "helm-pg": "pg", "helm_pg": "pg",
    "helm-trial": "trial", "trial-ui": "trial",
}

DEFAULT = "server"
PROBE_TIMEOUT = 12.0     # seconds to wait for a restarted service to answer


def _port_open(hosts, port: int, timeout: float = 0.6) -> bool:
    for host in hosts:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _await_port(hosts, port: int, deadline: float) -> bool:
    while True:
        if _port_open(hosts, port):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.4)


def _print_targets(console) -> None:
    from rich.table import Table
    from rich import box
    t = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
    t.add_column("target", style="cyan bold")
    t.add_column("service")
    t.add_column("now")
    t.add_column("launchd label", style="dim")
    t.add_column("url", style="dim")
    for name, (label, what, hosts, port, url, _log) in TARGETS.items():
        state = ("[green]up[/green]" if _port_open(hosts, port)
                 else "[dim]not answering[/dim]")
        t.add_row(name + ("  [dim]· default[/dim]" if name == DEFAULT else ""),
                  what, state, label, url)
    console.print()
    console.print(t)
    console.print("[dim]  helm restart          "
                  f"{DEFAULT} (unchanged default)[/dim]")
    console.print("[dim]  helm restart pg       the web dashboard[/dim]")
    console.print("[dim]  helm restart all      every HELM agent above[/dim]")
    console.print()


def _kickstart(console, name: str) -> bool:
    label, what, hosts, port, url, log_hint = TARGETS[name]

    # Firewall, checked at the point of use rather than trusted from the table:
    # this command must never be able to act on anything outside com.helm.*.
    if not label.startswith("com.helm."):
        console.print(f"[red]refusing[/red] {label} — outside the com.helm.* "
                      f"namespace. COTS is not this command's business.")
        return False

    was_up = _port_open(hosts, port)
    target = f"gui/{os.getuid()}/{label}"
    console.print(f"[cyan]Restarting[/cyan] {label} [dim]— {what}[/dim]")

    res = subprocess.run(["launchctl", "kickstart", "-k", target],
                         capture_output=True, text=True)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip()
        console.print(f"  [red]kickstart failed[/red] (rc={res.returncode})")
        if msg:
            console.print(f"  [dim]{msg}[/dim]")
        console.print("  [dim]Is the agent loaded?  launchctl list | grep helm"
                      "[/dim]")
        return False

    if _await_port(hosts, port, time.time() + PROBE_TIMEOUT):
        tail = "" if was_up else "  [dim](it was not answering before)[/dim]"
        console.print(f"  [green]up[/green]  {url}{tail}")
        return True

    console.print(f"  [yellow]kickstart accepted, but {url} is not answering "
                  f"after {PROBE_TIMEOUT:.0f}s[/yellow]")
    console.print(f"  [dim]{log_hint}[/dim]")
    # Deliberately a failure. An unconfirmed restart reported as success is the
    # thing that makes a health signal worthless (see W35).
    return False


def run() -> None:
    from rich.console import Console
    console = Console()

    args = [a for a in sys.argv[1:] if a.strip()]

    if any(a in ("-h", "--help", "help") for a in args):
        console.print()
        console.print("[bold cyan]helm restart[/bold cyan] "
                      "[dim]— restart a launchd-managed HELM service[/dim]")
        _print_targets(console)
        return

    if args and args[0].lstrip("-").lower() in ("list", "ls", "targets"):
        _print_targets(console)
        return

    if sys.platform != "darwin":
        console.print("[red]helm restart is macOS/launchd-only.[/red]")
        sys.exit(1)

    if not args:
        names = [DEFAULT]
    elif args[0].lower() == "all":
        names = list(TARGETS)
    else:
        names = []
        for raw in args:
            key = ALIASES.get(raw.lower(), raw.lower())
            if key not in TARGETS:
                console.print(f"[red]Unknown restart target:[/red] {raw}")
                _print_targets(console)
                sys.exit(2)
            if key not in names:
                names.append(key)

    ok = True
    for i, name in enumerate(names):
        if i:
            console.print()
        ok = _kickstart(console, name) and ok

    if len(names) > 1:
        console.print()
        console.print("[green]all restarted and answering[/green]" if ok else
                      "[yellow]finished with problems above[/yellow]")
    sys.exit(0 if ok else 1)
