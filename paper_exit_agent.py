#!/usr/bin/env python3
"""HELM paper exit agent — restores doctrine-acting on the PAPER book.

Register: candidate HELM-095. The paper-manage retirement (HELM-037 s62,
7/3) accidentally retired the paper book's exit-acting along with its mark-
writing — no paper position has closed since (108 open as of 7/20). This
agent restores the ACTING half only; marks remain the snapshot writer's job
(com.helm.snapshot.daily), per the one-writer doctrine.

What one run does (weekdays 15:55 ET, after the 15:45 snapshot):
  For each OPEN PAPER position: compute the decision-core verdict via
  check_one(persist=False) — READ-ONLY, no journal writes. If core_reason is
  PROFIT_TARGET / DTE_MANAGE / EXPIRY, close at current yfinance mids via
  close_cmd._finalize_close (the same primitive the old paper auto-manager
  used), stamping the verdict as exit_reason.

Doctrine guards:
  - NO STOPS: HELM-094 removed stops from the decision core; nothing here
    re-adds them. PT / DTE / EXPIRY only.
  - REAL book untouched, ever (HELM-093 advisory-only).
  - Paper fills are modeled at mid by definition; closes use yfinance mids
    (same convention as the trial's agent — symmetric for the comparison).
    A leg with no usable quote defers that close to the next run.
  - DRY RUN: `--dry-run` (or HELM_PAPER_DRY=1) reports what would close and
    writes NOTHING. First run after the 7/3 gap must be dry — the backlog
    decision (replay-close vs forward-only) is Russ's.

Usage:  paper_exit_agent.py [--dry-run]
Log:    launchd redirects to ~/Projects/helm/logs/paper_exit_agent.log
"""
import os
import sys
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # lives in the helm repo root
os.environ.setdefault("HELM_ROOT", str(ROOT))
sys.path.insert(0, str(ROOT))

ACT_REASONS = {"PROFIT_TARGET", "DTE_MANAGE", "EXPIRY"}
DRY = ("--dry-run" in sys.argv) or os.environ.get("HELM_PAPER_DRY") == "1"


def leg_mid(tk, leg):
    """Current yfinance mid for one leg (fallback: last). None if no quote."""
    try:
        exp = (leg.expiration or "")[:10]
        chain = tk.option_chain(exp)
        side = chain.puts if (leg.option_type or "").upper() == "PUT" else chain.calls
        row = side[side["strike"] == float(leg.strike)]
        if row.empty:
            return None
        r = row.iloc[0]
        bid = float(r.get("bid") or 0)
        ask = float(r.get("ask") or 0)
        if bid > 0 and ask > 0:
            return round((bid + ask) / 2, 2)
        last = float(r.get("lastPrice") or 0)
        return round(last, 2) if last > 0 else None
    except Exception:
        return None


def main():
    mode = "DRY RUN" if DRY else "ACTING"
    print(f"[{datetime.now().isoformat(timespec='seconds')}] paper_exit_agent "
          f"start ({mode}, root {ROOT})")
    if date.today().weekday() >= 5:
        print("weekend — nothing to do")
        return

    from helm.db import get_conn
    from helm.cli.check_cmd import check_one
    from helm.cli.close_cmd import _finalize_close
    from helm.models.position import Position
    from helm.models.leg import Leg
    import yfinance as yf

    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE status='OPEN' AND book='PAPER' "
        "ORDER BY ticker").fetchall()]
    conn.close()
    print(f"open PAPER positions: {len(rows)}")
    if not rows:
        return

    would = closed = held = skipped = 0
    for pos in rows:
        conn = get_conn()
        legs = [dict(r) for r in conn.execute(
            "SELECT * FROM legs WHERE position_id=?", (pos["id"],)).fetchall()]
        conn.close()
        try:
            a = check_one(pos, legs, persist=False)   # READ-ONLY verdict
        except Exception as e:
            print(f"  {pos['ticker']} {pos['strategy']}: check failed — {e}")
            skipped += 1
            continue
        reason = (a or {}).get("core_reason")
        if reason not in ACT_REASONS:
            held += 1
            continue

        kept = (a or {}).get("kept_pct")
        pnl = (a or {}).get("pnl_mtm")
        if DRY:
            would += 1
            print(f"  WOULD CLOSE {pos['ticker']:6} {pos['strategy']:18} "
                  f"[{reason}]  pnl~{pnl if pnl is not None else '?'}"
                  f"  kept~{kept if kept is not None else '?'}"
                  f"  opened {str(pos.get('opened_at'))[:10]}")
            continue

        pobj = Position.get(pos["id"])
        lobjs = Leg.for_position(pos["id"])
        tk = yf.Ticker(pos["ticker"])
        prices = {}
        ok = True
        for lg in lobjs:
            m = leg_mid(tk, lg)
            if m is None:
                print(f"  {pos['ticker']} {pos['strategy']}: {reason} but no "
                      f"quote for leg ${lg.strike} — deferred")
                ok = False
                break
            prices[lg.id] = m
        if not ok:
            skipped += 1
            continue
        res = _finalize_close(pobj, lobjs, prices, reason)
        if res.get("ok"):
            closed += 1
            print(f"  CLOSED {pos['ticker']} {pos['strategy']} [{reason}] "
                  f"realized {res['realized_pnl']:+.0f}")
        else:
            skipped += 1
            print(f"  {pos['ticker']}: close write failed")

    tail = (f"{would} would-close" if DRY else f"{closed} closed")
    print(f"[{datetime.now().isoformat(timespec='seconds')}] done: {tail}, "
          f"{held} held, {skipped} skipped")


if __name__ == "__main__":
    main()
