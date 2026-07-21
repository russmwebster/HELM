#!/usr/bin/env python3
"""Replay paper-book exits at journaled first-fire marks — one-time backlog fix.

HELM-095 backlog: the paper book's exit-acting was accidentally retired
2026-07-03 (HELM-037 s62 refactor). 108 PAPER positions accumulated unmanaged.
This reconstructs what disciplined HELM would have booked: it closes every open
PAPER position whose decision-core verdict (PROFIT_TARGET / DTE_MANAGE / EXPIRY)
first fired during the gap, at the mark recorded in the checks journal on that
first-fire day -- the same honest journal-replay method as the HELM-093
counterfactual.

Doctrine guards:
  * REAL book is NEVER touched (asserted per position).
  * No stops (HELM-094): PROFIT_TARGET / DTE_MANAGE / EXPIRY only -- mirrors
    helm.decision.evaluate exactly.
  * Closes are stamped at the historical fire-day date+time, not today.

Dry-run by default (prints what it would do, writes nothing).
--apply  : back up the DB, then execute the closes in one transaction.
"""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("HELM_ROOT", str(ROOT))
sys.path.insert(0, str(ROOT))

APPLY = "--apply" in sys.argv

from helm.db import get_conn                                  # noqa: E402
from helm.models.position import Position                     # noqa: E402
from helm.models.leg import Leg                               # noqa: E402
from helm.decision import (_family, _settings,                # noqa: E402
                           DEFAULT_PROFIT_TARGET, DEFAULT_DTE_EXIT)


def verdict(fam, pnl, credit, maxp, dte, pt, dtex):
    """Mirror helm.decision.evaluate's reason logic on a journaled row."""
    reason = None
    if fam in ('CREDIT', 'LONG_DEBIT', 'COVERED') and credit and (pnl / abs(credit)) >= pt:
        reason = 'PROFIT_TARGET'
    elif fam == 'DEBIT_SPREAD' and maxp and (pnl / maxp) >= pt:
        reason = 'PROFIT_TARGET'
    if reason is None and dte is not None:
        if dte <= 0:
            reason = 'EXPIRY'
        elif dte <= dtex:
            reason = 'DTE_MANAGE'
    return reason


def main():
    conn = get_conn()
    conn.row_factory = __import__('sqlite3').Row
    positions = conn.execute(
        "SELECT * FROM positions WHERE book='PAPER' AND status='OPEN' ORDER BY ticker"
    ).fetchall()
    print(f"{'DRY RUN' if not APPLY else 'APPLY'} — {len(positions)} open PAPER positions\n")

    plan = []
    for p in positions:
        assert p['book'] == 'PAPER', f"SAFETY: non-paper {p['id']}"   # never touch REAL
        fam = _family(p['strategy'])
        s = _settings(p['account_id'], p['strategy'])
        pt = s.get('profit_target_pct') or DEFAULT_PROFIT_TARGET
        pt = pt if pt <= 1 else pt / 100.0
        dtex = s.get('dte_exit_threshold') or DEFAULT_DTE_EXIT
        credit = p['net_premium'] or 0
        maxp = p['max_profit']
        checks = conn.execute(
            "SELECT id, checked_at, pnl_unrealized, dte_now FROM checks "
            "WHERE position_id=? AND data_quality='GOOD' AND pnl_unrealized IS NOT NULL "
            "ORDER BY checked_at", (p['id'],)).fetchall()
        fired = None
        for chk in checks:
            v = verdict(fam, chk['pnl_unrealized'], credit, maxp, chk['dte_now'], pt, dtex)
            if v:
                fired = (v, chk)
                break
        if not fired:
            continue
        reason, chk = fired
        legmarks = {r['leg_id']: r['current_price'] for r in conn.execute(
            "SELECT leg_id, current_price FROM leg_checks WHERE check_id=?", (chk['id'],))}
        plan.append((p, reason, chk, legmarks))

    # Report
    pt_tot = dte_tot = 0.0
    print(f"{'TICKER':7}{'STRATEGY':18}{'REASON':14}{'FIRE-DAY':12}{'REALIZED':>10}")
    for p, reason, chk, _ in sorted(plan, key=lambda x: (x[1], x[0]['ticker'])):
        pnl = chk['pnl_unrealized']
        if reason == 'PROFIT_TARGET':
            pt_tot += pnl
        else:
            dte_tot += pnl
        print(f"{p['ticker']:7}{p['strategy']:18}{reason:14}{chk['checked_at'][:10]:12}{pnl:>10.0f}")
    print(f"\n{len(plan)} closes | PROFIT_TARGET +{pt_tot:.0f} | "
          f"DTE/EXPIRY {dte_tot:.0f} | NET {pt_tot + dte_tot:.0f}")

    if not APPLY:
        print("\n(dry run — nothing written. Re-run with --apply to execute.)")
        return

    # ---- APPLY: backup, then write in one transaction ----
    db_path = Path(os.path.expanduser("~/Projects/helm/data/helm.db"))
    bak = db_path.with_name(f"helm.db.bak-h095replay-{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy2(db_path, bak)
    print(f"\nbackup: {bak.name}")

    closed = 0
    for p, reason, chk, legmarks in plan:
        fire_at = chk['checked_at']
        legs = Leg.for_position(p['id'])
        # per-leg close via journaled marks (mirrors _finalize_close math + dates)
        total = 0.0
        complete = all(legmarks.get(lg.id) is not None for lg in legs)
        for lg in legs:
            m = legmarks.get(lg.id)
            if m is not None:
                lg.close(m, close_date=fire_at)
                if lg.direction == 'SHORT':
                    total += (lg.open_price - m) * lg.contracts * lg.multiplier
                else:
                    total += (m - lg.open_price) * lg.contracts * lg.multiplier
            else:
                # rare: no journaled mark for this leg -> close dateless-priced,
                # position realized_pnl falls back to the journal total below
                lg.close_price = None
                lg.close_date = fire_at
                lg.status = 'CLOSED'
                lg.save()
        realized = round(total, 2) if complete else round(chk['pnl_unrealized'], 2)
        pos = Position.get(p['id'])
        assert pos.book == 'PAPER'
        pos.close(realized, closed_at=fire_at, exit_reason=reason)
        closed += 1
    print(f"applied: {closed} paper positions closed")

    # readback verification
    still_open = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE book='PAPER' AND status='OPEN'").fetchone()[0]
    real_open = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE book='REAL' AND status='OPEN'").fetchone()[0]
    print(f"verify: open PAPER now {still_open} (was {len(positions)}); "
          f"open REAL {real_open} (untouched)")


if __name__ == "__main__":
    main()
