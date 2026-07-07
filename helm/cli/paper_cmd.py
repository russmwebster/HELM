"""Paper-book command dispatch (HELM-061).

Wires the manual `helm paper generate` verb to the paper-generation unit.
Deployed by the user after the day's REAL trades are placed: it takes the
latest scan run's passed-on field, runs HELM's own open unit on those
tickers, and books the results as new PAPER positions.

Manual only by design. Booking positions is a write; HELM-037 keeps
`snapshot --all` as the sole *scheduled* writer, so paper generation is
never scheduled -- the user runs it. `manage` was retired (s62/HELM-037);
any subcommand other than `generate` is refused, fail-closed.
"""
from __future__ import annotations

import sys

# HELM-061 wiring sentinel: paper command dispatch present.
_HELM_061_PAPER_CMD = True


def run(args=None):
    """Dispatch `helm paper <subcommand>`.

    Robust to either dispatcher calling convention: if the caller passes the
    remaining argv it is used; otherwise argv is read directly from sys.argv.
    """
    if args is None:
        args = sys.argv[2:]
    sub = args[0].lower() if args else None

    if sub == "generate":
        from helm.cli._paper_generate import paper_generate
        return paper_generate()

    from rich.console import Console
    console = Console()
    if sub is None:
        console.print("[bold]Usage:[/bold] helm paper generate")
        console.print("[dim]Books HELM's paper picks from the latest scan's "
                       "passed-on field. Run after placing the day's real trades.[/dim]")
        return {"status": "usage"}
    console.print(f"[red]Unknown paper subcommand:[/red] {sub}")
    console.print("[dim]Only 'generate' is supported "
                  "(paper manage was retired in HELM-037).[/dim]")
    return {"status": "unknown_subcommand", "subcommand": sub}
