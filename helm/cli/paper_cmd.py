"""helm paper - HELM paper book commands.

Subcommands:
  generate   Paper-open HELM's top-ranked pick for each passed-on
             candidate in the latest scan run (book='PAPER'). Gated on RTH; the
             live book is unaffected (HELM only advises there).

Dispatched from helm.py: sys.argv is ['helm paper', <subcommand>, ...].
"""
from __future__ import annotations

import sys

from rich.console import Console


def run() -> None:
    console = Console()
    args = sys.argv[1:]
    sub = args[0] if args else None

    if sub == "generate":
        from helm.cli._paper_generate import paper_generate
        paper_generate()
        return

    console.print("[bold cyan]helm paper[/bold cyan] [dim]- HELM paper book[/dim]")
    console.print()
    console.print("[bold]Subcommands:[/bold]")
    console.print("  [cyan]generate[/cyan]   Paper-open HELM's picks for the latest run's "
                  "passed-on field")
    console.print()
    if sub is not None:
        console.print(f"[yellow]Unknown subcommand:[/yellow] {sub}")
        console.print("[dim]Run [bold]helm paper[/bold] for usage.[/dim]")
