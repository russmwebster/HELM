"""helm confirm-fills - replace synthesised entry prices with the broker's.

    helm confirm-fills                     # dry run against the newest export
    helm confirm-fills --file PATH         # name the file yourself
    helm confirm-fills --apply             # write it

Why this exists: a multi-leg entry stores per-leg prices that were never
fills. The booker asks for one number, the net credit, and scales the
quoted bids so the legs sum to it. Every leg is wrong and the net is
right (W151).

What it will not do:
  * touch the PAPER book - paper has no broker
  * touch a CLOSED position with --apply - history is not being redone
  * move net_premium - it recomputes it, compares, and refuses on a
    difference rather than making stored P&L fit the import
  * mark a position confirmed on a partial match

Costs are captured into legs.commission / legs.fees and are used in no
calculation: stored P&L stays GROSS by decision (2026-08-26).
"""
from __future__ import annotations

import glob
import os
import sys
from datetime import datetime

from rich.console import Console
from rich.table import Table

from helm.brokerfills import parse_activity_csv
from helm import fillconfirm
from helm.db import get_conn

console = Console()

REQUIRED = {
    "positions": "fills_confirmed_at",
    "legs": "commission",
}


def _missing_columns(conn):
    missing = []
    for table, column in REQUIRED.items():
        cols = [r[1] for r in conn.execute("PRAGMA table_info(" + table + ")")]
        if column not in cols:
            missing.append(table + "." + column)
    return missing


def _pick_file(explicit):
    """Choose the export - and SAY which, and what else was in the running.

    helm reconcile picks its CSV by mtime and never says so (W104). The
    picking is fine; the silence is the defect.
    """
    if explicit:
        return explicit, []
    pattern = os.path.expanduser("~/Downloads/*.csv")
    found = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not found:
        return None, []
    return found[0], found[1:4]


def run():
    # helm.py calls run() and DISCARDS its return value, so a command that
    # reports failure with "return 1" exits 0 and no script can tell. audit
    # eod gets its exit-1 contract by calling sys.exit directly; this does
    # the same, once, here. (assign_cmd has the same defect - raised as W153.)
    sys.exit(_run())


def _run():
    args = sys.argv[1:]
    apply_it = "--apply" in args
    include_closed = "--include-closed" in args
    explicit = None
    if "--file" in args:
        explicit = args[args.index("--file") + 1]
    only = None
    if "--position-id" in args:
        only = args[args.index("--position-id") + 1]

    if include_closed and apply_it:
        console.print("[red]--include-closed is a dry-run switch.[/red] "
                      "Confirming fills onto a CLOSED position would rewrite history, "
                      "which W151 is explicitly not doing.")
        return 1

    path, runners_up = _pick_file(explicit)
    if not path or not os.path.exists(path):
        console.print("[red]No activity export found.[/red] Download one from Fidelity "
                      "(30 days, all activity) or pass --file.")
        return 1

    stamp = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    parsed = parse_activity_csv(path)
    fills = parsed.fills
    if only:
        pass  # filtering happens on the plan, below - the file is read whole
    dates = sorted({f.as_of for f in fills})
    console.print("Reading [bold]" + os.path.basename(path) + "[/bold]  (saved " + stamp
                  + ", " + str(len(fills)) + " transactions, "
                  + (dates[0] + " to " + dates[-1] if dates else "no dated rows") + ")")
    if runners_up:
        console.print("[dim]  chosen as the newest of " + str(len(runners_up) + 1)
                      + " CSVs in Downloads; next was "
                      + os.path.basename(runners_up[0]) + "[/dim]")

    conn = get_conn()
    try:
        missing = _missing_columns(conn)
        if missing:
            console.print("[red]Schema is missing " + ", ".join(missing) + ".[/red] "
                          "Run: python3 tools/w151_migrate.py --apply")
            return 1

        statuses = ("OPEN", "CLOSED", "PENDING") if include_closed else ("OPEN",)
        plans = fillconfirm.build_plan(conn, fills, statuses=statuses)
        if only:
            plans = [p for p in plans if p.position_id == only]
        if not plans:
            console.print("Nothing in this export matches an open REAL position.")
            return 0

        table = Table(show_header=True, header_style="bold")
        for col in ("position", "leg", "stored", "broker", "delta", "note"):
            table.add_column(col)
        for plan in plans:
            for c in plan.corrections:
                delta = "" if c.delta is None else ("%+.2f" % c.delta)
                table.add_row(
                    plan.position_id[:34], c.symbol,
                    "" if c.stored is None else ("%.2f" % c.stored),
                    "" if c.broker is None else ("%.2f" % c.broker),
                    "[yellow]" + delta + "[/yellow]" if c.changed else delta,
                    "" if c.changed else "already correct",
                )
            for refusal in plan.refusals:
                table.add_row(plan.position_id[:34], "-", "", "", "", "[red]" + refusal + "[/red]")
        console.print(table)

        for plan in plans:
            if plan.refusals:
                continue
            if not plan.complete:
                console.print("[yellow]" + plan.position_id + "[/yellow]: matched "
                              + str(len(plan.corrections)) + " of " + str(plan.legs_total)
                              + " legs - not marking it confirmed on a partial match.")
            elif not plan.net_ok:
                console.print("[red]" + plan.position_id + "[/red]: legs recompute to "
                              + str(plan.recomputed_net) + " against a stored net of "
                              + str(plan.stored_net) + " (" + str(plan.net_delta)
                              + "). Refusing - stored P&L is not moved to fit an import.")

        writable = [p for p in plans if p.writable]
        if not apply_it:
            console.print("\n[dim]Dry run. " + str(len(writable)) + " position(s) would be "
                          "confirmed. Re-run with --apply to write.[/dim]")
            return 0

        written, when = fillconfirm.apply_plan(conn, writable)
        conn.commit()
        console.print("[green]Confirmed " + str(len(written)) + " position(s)[/green] at " + when)
        for pid in written:
            for row in conn.execute(
                "SELECT id, direction, strike, open_price FROM legs WHERE position_id = ? "
                "ORDER BY strike", (pid,)):
                console.print("   readback " + str(row["id"]) + "  " + str(row["direction"])
                              + " " + str(row["strike"]) + " -> " + str(row["open_price"]))
        return 0
    finally:
        conn.close()
