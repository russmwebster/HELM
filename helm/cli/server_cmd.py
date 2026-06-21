"""helm restart -- restart the launchd-managed HELM server.

The server runs as the launchd agent com.helm.server (KeepAlive). This wraps
`launchctl kickstart -k gui/<uid>/com.helm.server`, which kills and relaunches
the agent in one shot, re-reading code on the way up. Use after editing server
code. Supersedes the stale helm-servers.sh dev launcher (which binds the same
port and can never restart this agent).
"""
from __future__ import annotations

import os
import subprocess
import sys

LABEL = "com.helm.server"


def run() -> None:
    from rich.console import Console
    console = Console()

    if sys.platform != "darwin":
        console.print("[red]helm restart is macOS/launchd-only.[/red]")
        sys.exit(1)

    target = f"gui/{os.getuid()}/{LABEL}"
    console.print(f"[cyan]Restarting[/cyan] {LABEL} ...")

    res = subprocess.run(
        ["launchctl", "kickstart", "-k", target],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip()
        console.print(f"[red]kickstart failed[/red] (rc={res.returncode})")
        if msg:
            console.print(f"[dim]{msg}[/dim]")
        console.print("[dim]Check the agent is loaded: launchctl list | grep helm[/dim]")
        sys.exit(res.returncode)

    console.print(f"[green]done[/green]  {LABEL} restarted -- code re-read on relaunch.")
    console.print("[dim]http://helm.local:8766[/dim]")
