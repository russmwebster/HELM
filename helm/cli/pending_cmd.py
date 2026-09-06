"""helm pending -- exit flags on the REAL book awaiting a decision (W158).

    helm pending                      # the open decision points
    helm pending --seed               # first run: rebuild from the journal
    helm pending --json               # machine-readable
    helm pending keep TICKER --kind thesis --reason "..."   # log an override

WHAT THIS IS. A fired exit flag is a DECISION POINT, not an instruction.
Act on it -- `helm close` -- or say why you are holding, the same day. HELM
records which you did. It closes nothing (HELM-193).

WHAT THIS IS NOT. A sell signal. Measured 2026-09-06: of the seven thesis
flags standing on the open real book, five had IMPROVED since firing. The
drift column shows both directions on purpose.
"""
import json
import sys

from helm import exit_flags as F


def _conn():
    import sqlite3
    from helm.config import DB_PATH
    c = sqlite3.connect(str(DB_PATH))
    return c


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _resolve(conn, who, kind):
    """A ticker or a position id, plus a kind, must name exactly one open
    decision -- or we refuse rather than guess (W7's rule, W104's lesson)."""
    rows = [d for d in F.pending(conn)
            if who in (d["ticker"], d["position_id"])
            and (kind is None or d["kind"] == kind)]
    return rows


def _keep():
    who = None
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            who = a
            break
    kind = _arg("--kind")
    reason = _arg("--reason")
    note = _arg("--note")
    if not who or not reason:
        print("usage: helm pending keep TICKER --reason \"why you are holding\" "
              "[--kind thesis|target|dte] [--note ...]")
        sys.exit(2)
    conn = _conn()
    try:
        F.settle_closed(conn)
        rows = _resolve(conn, who, kind)
        if not rows:
            print("No open decision matches %s%s."
                  % (who, " (kind %s)" % kind if kind else ""))
            sys.exit(1)
        if len(rows) > 1:
            print("%s has %d open decisions -- name one with --kind: %s"
                  % (who, len(rows), ", ".join(r["kind"] for r in rows)))
            sys.exit(1)
        r = rows[0]
        n = F.keep(conn, r["position_id"], r["kind"], reason, note)
        if not n:
            print("Nothing written.")
            sys.exit(1)
        print("Logged: %s %s flag (fired %s) -- HOLDING.\n  reason: %s"
              % (r["ticker"], r["kind"], r["flag_date"], reason))
        print("  The position is untouched. If it fires again after today, "
              "that is a new decision.")
        sys.exit(0)
    finally:
        conn.close()


def run():
    # helm.py discards a command's return value (W154) -- exit explicitly.
    if len(sys.argv) > 1 and sys.argv[1] == "keep":
        sys.argv.pop(1)
        _keep()
        return

    conn = _conn()
    try:
        F.settle_closed(conn)
        if "--seed" in sys.argv:
            w = F.scan(conn, seed=True)
            print("Seeded %d decision point(s) from the existing journal.\n"
                  % len(w))
        rows = F.pending(conn)
        if "--json" in sys.argv:
            print(json.dumps(rows, indent=2, default=str))
            sys.exit(0)
        _render(rows)
        sys.exit(0)
    finally:
        conn.close()


_SHORT = {"LONG_CALL": "LC", "LONG_PUT": "LP", "IRON_CONDOR": "IC",
          "COVERED_CALL": "CC", "BULL_PUT_SPREAD": "BPS",
          "BEAR_CALL_SPREAD": "BCS", "BEAR_PUT_SPREAD": "BePS",
          "JADE_LIZARD": "JL", "DIAGONAL": "DIAG", "DIAGONAL_PUT": "DIAGP"}


def _money(v):
    return "—" if v is None else format(v, "+,.0f")


def _render(rows):
    from rich import box
    from rich.console import Console
    from rich.table import Table
    con = Console()
    con.print()
    if not rows:
        con.print("[green]No exit flag is waiting on a decision.[/green]")
        con.print("[dim]HELM records flags on the REAL book only. "
                  "Paper exits are the 15:35 agent's job.[/dim]\n")
        return
    yr = rows[0]["flag_date"][:4]
    con.print("[bold cyan]Exit flags awaiting a decision[/bold cyan]  "
              "[dim]REAL book — %d open — flagged %s[/dim]" % (len(rows), yr))
    t = Table(box=box.SIMPLE, padding=(0, 1))
    for c, j in (("ticker", "left"), ("", "left"), ("signal", "left"),
                 ("fired", "left"), ("age", "right"), ("mark then", "right"),
                 ("mark now", "right"), ("since", "right"), ("still?", "left")):
        t.add_column(c, justify=j)
    tot = 0.0
    for d in rows:
        dr = d.get("drift")
        if dr is not None:
            tot += dr
        colour = "green" if (dr or 0) >= 0 else "red"
        t.add_row(
            d["ticker"], _SHORT.get(d["strategy"], d["strategy"]),
            d["kind"], d["flag_date"][5:],
            "%dtd" % d["age_td"],
            _money(d["mark_at_flag"]), _money(d["mark_now"]),
            "[%s]%s[/%s]" % (colour, _money(dr), colour),
            "firing" if d["still_firing"] else "cleared")
    con.print(t)
    up = sum(1 for d in rows if (d.get("drift") or 0) > 0)
    con.print("  [dim]Since their flags these positions have moved "
              "%s in total; %d of %d are BETTER than when they fired.[/dim]"
              % (_money(tot), up, len(rows)))
    con.print()
    con.print("[dim]A flag is a review trigger, not a sell signal — HELM "
              "closes nothing on the real book. Act with [/dim]"
              "[cyan]helm close[/cyan][dim], or log the hold with [/dim]"
              "[cyan]helm pending keep TICKER --reason \"...\"[/cyan][dim]. "
              "Either way it stops being a silent drift.[/dim]")
    ns = sum(1 for d in rows if d["seeded"])
    if ns:
        con.print("[dim]%s reconstructed from the journal when this surface "
                  "was installed — the flags fired on the dates shown, but "
                  "nothing surfaced them at the time, so a long age here is "
                  "not a decision anyone declined to make.[/dim]"
                  % ("All %d rows were" % ns if ns == len(rows)
                     else "%d of these rows were" % ns))
    con.print()
