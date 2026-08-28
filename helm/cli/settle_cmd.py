"""
helm settle -- settle positions whose legs have reached expiry.

HELM cannot see Fidelity. On the REAL book, inferring assignment from
spot-versus-strike would be a guess presented as a record, so settle ASKS and
writes only what the trader confirms.

On the PAPER book there is no Fidelity -- HELM is the book of record -- so
asking would be theatre. PAPER expiries settle deterministically from the last
GOOD recorded spot on the leg's expiry day (decided by Russ, 2026-08-28, s110):

  * clearly OTM (>= 1% from strike)  -> the leg closes at 0.00
  * clearly ITM (>= 1% from strike)  -> the leg closes at intrinsic value
  * within 1% of the strike, or no GOOD expiry-day mark -> NOT settled; the
    leg is flagged like the real book, because a close call inferred from a
    mark that may predate the close is a guess presented as a record.

Proposals are printed first and applied only on an explicit yes -- nothing is
written without a keypress.

Three cases, not two (W131 / HELM-194 -- the old MAX(expiration) selection
could not see a position with one dead leg and one live one):

  * ALL open legs past expiry, single-leg short, REAL -> the closed question:
    assigned, or expired worthless.
  * ALL open legs past expiry, multi-leg, REAL -> flagged only. A four-legged
    condor does not reduce to assigned/not.
  * SOME legs past expiry, some live (a diagonal's normal lifecycle) -> the
    position is NEVER auto-closed. Dead legs are NAMED. On PAPER they settle
    per leg under the rule above and the position stays OPEN.
  * ASSIGNED is never written here. It prints the `helm assign` command, so
    the cost-basis arithmetic lives in exactly one place and is seen before
    it is committed.
"""

import sys
from datetime import date, datetime

from rich.console import Console

from helm.db import get_conn

console = Console()

# A paper leg is only inferred when the expiry-day spot is at least this far
# from the strike, either side. Inside the band, the recorded mark (possibly
# hours before the close) cannot say which side the option finished on.
NEAR_STRIKE_PCT = 0.01


def _pending(conn, today):
    """OPEN positions with at least one OPEN leg whose expiry is already past.

    Returns (position_row, total_leg_count, dead_legs, live_legs) tuples.
    Stock legs carry no expiration and are always live. The old query kept
    only positions whose MAX(expiration) was past -- the blindness W131
    records.
    """
    out = []
    for p in conn.execute(
            "SELECT id, ticker, strategy, book, net_premium, total_contracts "
            "FROM positions WHERE status = 'OPEN'").fetchall():
        nlegs_total = conn.execute(
            "SELECT COUNT(*) FROM legs WHERE position_id = ?",
            (p["id"],)).fetchone()[0]
        legs = conn.execute(
            "SELECT id, leg_role, option_type, direction, strike, expiration, "
            "       contracts, multiplier, open_price "
            "FROM legs WHERE position_id = ? AND status = 'OPEN'",
            (p["id"],)).fetchall()
        dead = [l for l in legs
                if l["expiration"] and str(l["expiration"])[:10] < today]
        if not dead:
            continue
        dead_ids = {l["id"] for l in dead}
        live = [l for l in legs if l["id"] not in dead_ids]
        out.append((p, nlegs_total, dead, live))
    out.sort(key=lambda t: (min(str(l["expiration"]) for l in t[2]),
                            t[0]["ticker"]))
    return out


def _leg_name(leg):
    return "%s $%s exp %s" % (leg["leg_role"],
                              format(float(leg["strike"] or 0), "g"),
                              str(leg["expiration"])[:10])


def _expiry_day_spot(conn, position_id, expiry):
    row = conn.execute(
        "SELECT spot_price, checked_at FROM checks "
        "WHERE position_id = ? AND date(checked_at) = date(?) "
        "  AND data_quality = 'GOOD' AND spot_price IS NOT NULL "
        "ORDER BY checked_at DESC LIMIT 1",
        (position_id, str(expiry)[:10])).fetchone()
    if row is None:
        return None, None
    return float(row["spot_price"]), str(row["checked_at"])


def _paper_proposal(conn, pos, leg):
    """What the recorded spot says this dead PAPER leg settled at, or why it
    cannot say ('settle' is None when the leg is flagged instead)."""
    expiry = str(leg["expiration"])[:10]
    spot, at = _expiry_day_spot(conn, pos["id"], expiry)
    strike = float(leg["strike"])
    if spot is None:
        return {"leg": leg, "settle": None,
                "why": "no GOOD mark on the expiry day %s" % expiry}
    dist = (spot - strike) / strike
    if abs(dist) < NEAR_STRIKE_PCT:
        return {"leg": leg, "settle": None, "spot": spot,
                "why": "spot %.2f finished within %.0f%% of the %.2f strike "
                       "-- too close to infer from a mark that may predate "
                       "the close" % (spot, NEAR_STRIKE_PCT * 100, strike)}
    is_call = (leg["option_type"] or "").upper() == "CALL"
    itm = spot > strike if is_call else spot < strike
    intrinsic = (spot - strike) if is_call else (strike - spot)
    price = round(intrinsic, 2) if itm else 0.0
    return {"leg": leg, "settle": price, "spot": spot, "at": at, "itm": itm}


def _apply_paper(conn, todo):
    """Write the accepted paper settlements. A leg closes on its expiry date
    at 16:00, not today -- the option ceased to exist then, and a backdated
    close keeps the record true."""
    now = datetime.now().isoformat()
    for pos, props in todo:
        for pr in props:
            if pr["settle"] is None:
                continue
            leg = pr["leg"]
            expiry = str(leg["expiration"])[:10]
            note = ("settled by helm settle, PAPER inference (W131): spot "
                    "%.2f on %s -> %s at %.2f"
                    % (pr["spot"], expiry,
                       "ITM intrinsic" if pr["itm"] else "expired OTM",
                       pr["settle"]))
            conn.execute(
                "UPDATE legs SET status = 'CLOSED', close_price = ?, "
                "close_date = ?, notes = COALESCE(notes || ' | ', '') || ? "
                "WHERE id = ?",
                (pr["settle"], expiry + "T16:00:00", note, leg["id"]))
        left = conn.execute(
            "SELECT COUNT(*) FROM legs WHERE position_id = ? "
            "AND status = 'OPEN'", (pos["id"],)).fetchone()[0]
        if left == 0:
            # Realized is DERIVED from the legs -- including any closed at a
            # real price before expiry -- never asserted from net_premium.
            realized = conn.execute(
                "SELECT COALESCE(SUM((CASE WHEN direction = 'SHORT' "
                "  THEN open_price - COALESCE(close_price, 0) "
                "  ELSE COALESCE(close_price, 0) - open_price END) "
                "  * contracts * multiplier), 0) "
                "FROM legs WHERE position_id = ?", (pos["id"],)).fetchone()[0]
            conn.execute(
                "UPDATE positions SET status = 'EXPIRED', closed_at = ?, "
                "exit_reason = 'EXPIRED', realized_pnl = ?, updated_at = ? "
                "WHERE id = ?", (now, realized, now, pos["id"]))
        else:
            conn.execute("UPDATE positions SET updated_at = ? WHERE id = ?",
                         (now, pos["id"]))
    conn.commit()


def run():
    args = sys.argv[1:]
    today = date.today().isoformat()
    for a in args:
        if a.startswith("--date"):
            today = a.split("=", 1)[1] if "=" in a else today
    conn = get_conn()
    pend = _pending(conn, today)
    console.print()
    if not pend:
        console.print("[green]Nothing to settle.[/green] No open position has "
                      "a leg past expiry.")
        console.print()
        return 0

    console.print("[bold]%d position(s) have a leg past expiry and are still "
                  "open[/bold]" % len(pend))
    console.print("[dim]REAL is asked, never inferred. PAPER is inferred from "
                  "recorded spot, never asked -- and written only on a yes "
                  "below.[/dim]")
    console.print()

    paper_todo = []
    n_proposed = 0

    for p, nlegs_total, dead, live in pend:
        partial = bool(live)
        tag = ("partially expired" if partial
               else "expired %s" % max(str(l["expiration"])[:10] for l in dead))
        console.print("  [bold]%s[/bold]  %s  %s  %s" % (
            p["ticker"], p["strategy"], p["book"], tag))

        if p["book"] == "PAPER":
            props = [_paper_proposal(conn, p, l) for l in dead]
            for pr in props:
                leg = pr["leg"]
                if pr["settle"] is None:
                    console.print("    [yellow]%s -- left alone:[/yellow] %s"
                                  % (_leg_name(leg), pr["why"]))
                else:
                    side = ("ITM -> close at intrinsic" if pr["itm"]
                            else "OTM -> expired worthless")
                    console.print("    %s -- spot %.2f on expiry day: %s "
                                  "[bold]%.2f[/bold]"
                                  % (_leg_name(leg), pr["spot"], side,
                                     pr["settle"]))
                    n_proposed += 1
            for l in live:
                console.print("    [dim]%s is live -- the position stays "
                              "OPEN.[/dim]" % _leg_name(l))
            if any(pr["settle"] is not None for pr in props):
                paper_todo.append((p, props))
            console.print()
            continue

        # REAL book: never inferred, exactly as before.
        if partial:
            for l in dead:
                console.print("    [yellow]%s is past expiry[/yellow] -- "
                              "never auto-closed. Check the broker record."
                              % _leg_name(l))
            console.print("    assigned -> [bold]helm assign %s "
                          "--position-id %s --apply[/bold]"
                          % (p["ticker"], p["id"]))
            console.print("    [dim]expired worthless -> no per-leg close "
                          "command exists yet; note it, and close the whole[/dim]")
            console.print("    [dim]position when the live leg goes.[/dim]")
            for l in live:
                console.print("    [dim]%s is live.[/dim]" % _leg_name(l))
            console.print()
            continue

        single = (nlegs_total == 1 and len(dead) == 1
                  and dead[0]["direction"] == "SHORT")
        if not single:
            console.print("    [yellow]multi-leg -- settle by hand.[/yellow] A %d-leg structure does not"
                          % nlegs_total)
            console.print("    reduce to assigned/not. Fidelity logs a closing price per")
            console.print("    leg, and `helm close` prompts for each leg -- you type what")
            console.print("    the broker shows, so no matching heuristic can get it wrong:")
            console.print("      [bold]helm close %s --position-id %s --reason EXPIRED[/bold]"
                          % (p["ticker"], p["id"]))
            console.print("    [dim](`helm activity` imports the whole export instead, but it[/dim]")
            console.print("    [dim]matches legs without checking quantity or direction.)[/dim]")
            console.print()
            continue
        try:
            ans = input("    assigned or expired worthless?  [a/e, Enter skips] ").strip().lower()
        except EOFError:
            ans = ""
        if ans == "a":
            console.print("    [cyan]Assignment is written by `helm assign`[/cyan], which shows the")
            console.print("    cost basis before committing. Run:")
            console.print("      [bold]helm assign %s --position-id %s --apply[/bold]"
                          % (p["ticker"], p["id"]))
        elif ans == "e":
            prem = float(p["net_premium"] or 0.0)
            now = datetime.now().isoformat()
            conn.execute(
                "UPDATE positions SET status=?, closed_at=?, exit_reason=?, "
                "realized_pnl=?, updated_at=? WHERE id=?",
                ("EXPIRED", now, "EXPIRED", prem, now, p["id"]))
            # An expiry HAS a closing price and it is zero: the option ceased
            # to exist worthless. Closing every leg at 0.00 with a date is what
            # makes the record checkable against the broker leg by leg, and it
            # keeps the realized figure DERIVABLE from the legs rather than
            # only asserted on the position. (Contrast `helm assign`, where
            # nothing trades and the close price is left NULL on purpose.)
            conn.execute(
                "UPDATE legs SET status = ?, close_price = ?, close_date = ? "
                "WHERE position_id = ? AND status = 'OPEN'",
                ("CLOSED", 0.0, now, p["id"]))
            conn.commit()
            back = conn.execute("SELECT status, exit_reason, realized_pnl FROM positions "
                                "WHERE id = ?", (p["id"],)).fetchone()
            console.print("    [green]recorded EXPIRED[/green] -- %s / %s / realized $%s"
                          % (back["status"], back["exit_reason"],
                             format(back["realized_pnl"] or 0, ",.2f")))
            shut = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(open_price * contracts * multiplier), 0) "
                "FROM legs WHERE position_id = ? AND status = 'CLOSED'",
                (p["id"],)).fetchone()
            console.print("    %d leg(s) closed at $0.00 -- premium kept $%s, which is "
                          "the realized figure derived from the legs"
                          % (shut[0], format(shut[1] or 0, ",.2f")))
        else:
            console.print("    [dim]skipped[/dim]")
        console.print()

    if paper_todo:
        try:
            ans = input("  apply %d PAPER leg settlement(s) above?  [y/N] "
                        % n_proposed).strip().lower()
        except EOFError:
            ans = ""
        if ans == "y":
            _apply_paper(conn, paper_todo)
            console.print()
            for pos, props in paper_todo:
                for pr in props:
                    if pr["settle"] is None:
                        continue
                    back = conn.execute(
                        "SELECT status, close_price, close_date FROM legs "
                        "WHERE id = ?", (pr["leg"]["id"],)).fetchone()
                    console.print("  [green]recorded[/green] %s %s -> %s at "
                                  "%.2f, close date %s"
                                  % (pos["ticker"], _leg_name(pr["leg"]),
                                     back["status"],
                                     float(back["close_price"]),
                                     str(back["close_date"])[:10]))
                pstat = conn.execute(
                    "SELECT status, realized_pnl FROM positions WHERE id = ?",
                    (pos["id"],)).fetchone()
                if pstat["status"] != "OPEN":
                    console.print("  [green]position %s -> %s[/green], "
                                  "realized $%s (derived from the legs)"
                                  % (pos["id"], pstat["status"],
                                     format(pstat["realized_pnl"] or 0, ",.2f")))
        else:
            console.print("  [dim]nothing written[/dim]")
    console.print()
    return 0
