"""helm fills -- book the real book's trades from a Fidelity activity download (s136).

    helm fills                      dry run on the newest activity download in ~/Downloads
    helm fills --file PATH          name the download yourself
    helm fills --apply              write it: backup first, then one y/N
    helm fills --apply --yes        no prompt (the board, later)
    helm fills --reason ID=NAME     the exit reason for one position close (repeatable)
    helm fills --history            also list every pre-10/24 difference (report only)

Reads both download formats -- History_for_Account_<n>.csv and Accounts_History.csv --
keeps the Rollover IRA's rows only, and never stores or prints the account number
(HELM keeps a fingerprint of it). Fills from 2026-10-24 are booked; earlier ones are
compared and reported. OPEN positions get their entry prices corrected to the fill;
closed positions are never changed. Assigned short puts are booked (s138: the put
closes at 0.00, the shares go in at the strike). New opens, called-away shares and
dividends are reported and booked in stage 2. The planner and writer are helm/fillimport.py.

(`helm import` is HELM's original positions-onboarding import -- a different job.)
"""
from __future__ import annotations

import glob
import os
import sqlite3
import sys
from datetime import datetime

from rich.console import Console

console = Console()


def _downloads():
    return os.path.expanduser(os.environ.get("HELM_DOWNLOADS", "~/Downloads"))


def _pick(path):
    if path:
        return os.path.expanduser(path), []
    d = _downloads()
    cands = glob.glob(os.path.join(d, "History_for_Account_*.csv")) + glob.glob(os.path.join(d, "Accounts_History*.csv"))
    cands.sort(key=os.path.getmtime, reverse=True)
    return (cands[0] if cands else None), cands[1:]


def _learn_hash(B):
    d = _downloads()
    cands = glob.glob(os.path.join(d, "Portfolio_Positions*.csv")) + glob.glob(os.path.join(d, "Accounts_History*.csv"))
    cands.sort(key=os.path.getmtime, reverse=True)
    for c in cands:
        h = B.learn_ira_hash(c)
        if h:
            return h, os.path.basename(c)
    return None, None


def run():
    args = sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1] == "fills" else sys.argv[1:]
    if "-h" in args or "--help" in args:
        console.print(__doc__)
        return
    from helm import brokerfills as B
    from helm import fillimport as FI
    from helm.config import DB_PATH

    path, apply_, yes, history, reasons = None, False, False, False, {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--file" and i + 1 < len(args):
            path = args[i + 1]; i += 1
        elif a == "--apply":
            apply_ = True
        elif a == "--yes":
            yes = True
        elif a == "--history":
            history = True
        elif a == "--reason" and i + 1 < len(args):
            k, _, v = args[i + 1].partition("=")
            from helm.cli.close_cmd import EXIT_REASONS
            if v.strip().upper() not in EXIT_REASONS:
                console.print("[red]--reason %s: one of %s[/red]" % (args[i + 1], ", ".join(EXIT_REASONS)))
                return 1
            reasons[k.strip()] = v.strip().upper(); i += 1
        else:
            console.print("[red]unknown argument %s[/red] (helm fills --help)" % a)
            return 1
        i += 1

    path, others = _pick(path)  # s136b: every printed file name goes through mask_name
    if not path or not os.path.exists(path):
        console.print("[red]No Fidelity activity download found in %s.[/red] Download Activity & Orders "
                      "(History) as CSV, or give --file." % _downloads())
        return 1

    conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        have = FI.stored_ira_hash(conn) if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='helm_meta'").fetchone() else None
        learned, learned_from = (None, None)
        ira = have
        if ira is None:
            learned, learned_from = _learn_hash(B)
            ira = learned
        exp = B.read_export(path, ira)
        console.print("\n[bold]helm fills[/bold] -- %s  [dim](%s%s)[/dim]" % (
            B.mask_name(os.path.basename(path)), exp.fmt.lower().replace("_", " "),
            ("; also in Downloads: " + ", ".join(B.mask_name(os.path.basename(o)) for o in others[:3]))
            if others else ""))
        if exp.hash_unknown:
            console.print("[red]HELM doesn't know which account is the Rollover IRA yet.[/red] Put a "
                          "Portfolio_Positions or Accounts_History download in Downloads (it names the account), "
                          "then run again.")
            return 1
        if exp.other_account:
            console.print("[red]This download is for an account that isn't the Rollover IRA -- nothing read.[/red]")
            return 1
        if learned and have is None:
            console.print("[dim]Rollover IRA identified from %s; HELM will keep a fingerprint of its number, "
                          "never the number.[/dim]" % B.mask_name(learned_from))
        p = FI.plan(conn, [exp], reasons=reasons)
    finally:
        conn.close()

    opts = sum(1 for f in exp.fills if f.is_option)
    console.print("[dim]Rollover IRA: %d rows (%d option fills), %s to %s; booking from %s. "
                  "Share rows are read only to book an assignment; cash and dividend rows aren't read yet.[/dim]" % (
                      len(exp.fills), opts, min((f.as_of for f in exp.fills), default="—"),
                      max((f.as_of for f in exp.fills), default="—"), FI.CUTOFF))
    for n in p.notes:
        console.print("[dim]%s[/dim]" % n)

    sections = (("POSITION_CLOSE", "To book -- positions closed"),
                ("PARTIAL_CLOSE", "To book -- part of a position closed (the closed part becomes its own position)"),
                ("LEG_CLOSE", "To book -- legs closed"),
                ("ASSIGN", "To book -- puts assigned (shares into HELM)"),
                ("OPEN", "To book -- opened at Fidelity (new positions, PMCC shorts, rolls)"),
                ("PRICE_FIX", "To correct -- entry prices on open positions"),
                ("MATCHED", "Already in HELM (recorded so they aren't checked again)"),
                ("REPORT", "Not booked -- for you to look at"))
    for kind, title in sections:
        rows = [it for it in p.items if it.kind == kind]
        if rows:
            console.print("\n[bold]%s[/bold]" % title)
            for it in rows:
                console.print("  " + it.text + (("   [dim]%s[/dim]" % it.position_id) if kind in ("POSITION_CLOSE", "PARTIAL_CLOSE") else ""))
    if p.history:
        console.print("\n[bold]Before %s[/bold] -- %d difference(s) between HELM and Fidelity; report only, "
                      "closed positions are not corrected%s" % (FI.CUTOFF, len(p.history),
                                                                 "" if history else " (--history lists them)"))
        if history:
            for h in p.history:
                console.print("  " + h)

    w = p.writes
    if not w:
        console.print("\n[green]Nothing to write.[/green]\n")
        return 0
    if not apply_:
        console.print("\n[dim]Dry run -- nothing written. %d change(s). Run with --apply to write them.[/dim]\n" % len(w))
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = str(DB_PATH) + ".bak-fills-" + stamp
    console.print()
    if not yes:
        from rich.prompt import Confirm
        if not Confirm.ask("  Write these %d change(s)? (backup first)" % len(w), default=False):
            console.print("[dim]  Cancelled -- nothing written.[/dim]\n")
            return 0
    c = sqlite3.connect(str(DB_PATH))
    try:
        c.execute("VACUUM INTO ?", (bak,))
    finally:
        c.close()
    console.print("  [dim]backup: %s[/dim]" % bak)
    try:
        done = FI.apply(p, ira_hash=ira)
    except Exception as e:
        console.print("\n[red]Stopped: %s[/red]\n  Anything written before this point stays; the backup above "
                      "is the database as it was before this run.\n" % e)
        return 1
    console.print("\n[green]Written:[/green] %d" % len(done))
    console.print("[dim]Run helm fills again: it should propose nothing.[/dim]\n")
    return 0
