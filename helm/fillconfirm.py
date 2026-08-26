"""W151 - correct a REAL position's stored entry prices from the broker's fills.

The defect this repairs: the iron-condor booker asks the trader for ONE
number, the net credit, then scales the quoted bids so the legs sum to it
(open_cmd.py:1975). The four per-leg prices are manufactured to match the
one true number. On LRCX every leg was wrong and the net was exactly
right - which is why nobody noticed for two months: the only figure
anyone checks is the one that is correct by construction.

Design decisions, all of them deliberate:

  * REAL book only. Paper has no broker, so a paper fill cannot exist.
    Anything else is refused, not skipped quietly.
  * Legs are written. net_premium is RECOMPUTED and COMPARED, never
    overwritten. If the recomputed net disagrees with what is stored, the
    position is refused and reported - stored P&L is not moved silently
    to make an import fit.
  * A position is marked confirmed only when EVERY leg was matched. A
    partial confirm leaves the flag NULL, because a flag that says
    "confirmed" over three legs of four is worse than no flag.
  * commission and fees are CAPTURED and never computed with. Russ
    decided 2026-08-26 to keep P&L gross. A column that exists can be
    ignored; one that does not cannot be recovered.

This module does not choose a file, does not open a database, and does
not decide when to run. It plans, and it applies a plan it was given.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from helm import fillmatch


@dataclass
class LegCorrection:
    leg_id: str
    position_id: str
    symbol: str
    direction: str
    contracts: int
    stored: float
    broker: float
    commission: float
    fees: float

    @property
    def delta(self):
        if self.stored is None or self.broker is None:
            return None
        return round(self.broker - self.stored, 4)

    @property
    def changed(self):
        return self.delta is not None and abs(self.delta) >= 0.005


@dataclass
class PositionPlan:
    position_id: str
    book: str
    status: str
    corrections: list = field(default_factory=list)
    legs_total: int = 0
    stored_net: float = None
    recomputed_net: float = None
    refusals: list = field(default_factory=list)

    @property
    def complete(self):
        return self.legs_total > 0 and len(self.corrections) == self.legs_total

    @property
    def net_ok(self):
        if self.stored_net is None or self.recomputed_net is None:
            return False
        return abs(self.recomputed_net - self.stored_net) < 0.01

    @property
    def writable(self):
        return not self.refusals and self.complete and self.net_ok

    @property
    def net_delta(self):
        if self.stored_net is None or self.recomputed_net is None:
            return None
        return round(self.recomputed_net - self.stored_net, 2)


def _leg_cash(direction, price, contracts):
    """A short leg is a credit, a long leg a debit. Gross, no costs."""
    if price is None or contracts is None:
        return None
    sign = 1.0 if (direction or "").upper() == "SHORT" else -1.0
    return sign * float(price) * 100.0 * int(contracts)


def build_plan(conn, fills, statuses=("OPEN",), book="REAL"):
    """Plan the corrections. Reads only - no writes happen here."""
    entries = [f for f in fills if f.event == "OPEN" and f.is_option]
    plans = {}

    for fill in entries:
        match = fillmatch.resolve(conn, fill, statuses=statuses, book=book)
        if not match.ok:
            # A near miss is louder than a no-match, and both are recorded
            # against the ticker rather than dropped.
            plans.setdefault(
                "UNRESOLVED:" + fill.ticker,
                PositionPlan(position_id="UNRESOLVED:" + fill.ticker, book=book, status="-"),
            ).refusals.append(match.outcome + " " + fill.symbol + " - " + match.note)
            continue

        leg = match.leg
        pid = leg["position_id"]
        if pid not in plans:
            row = conn.execute(
                "SELECT id, book, status, net_premium FROM positions WHERE id = ?", (pid,)
            ).fetchone()
            total = conn.execute(
                "SELECT COUNT(*) FROM legs WHERE position_id = ?", (pid,)
            ).fetchone()[0]
            plan = PositionPlan(
                position_id=pid,
                book=(row["book"] if row else "?"),
                status=(row["status"] if row else "?"),
                legs_total=total,
                stored_net=(row["net_premium"] if row else None),
            )
            if plan.book != "REAL":
                plan.refusals.append("not the REAL book - paper has no broker fills")
            plans[pid] = plan

        plan = plans[pid]
        if any(c.leg_id == leg["id"] for c in plan.corrections):
            plan.refusals.append("two opening fills resolved to leg " + str(leg["id"]))
            continue
        plan.corrections.append(
            LegCorrection(
                leg_id=leg["id"],
                position_id=pid,
                symbol=fill.symbol,
                direction=leg["direction"],
                contracts=leg["contracts"],
                stored=leg["open_price"],
                broker=fill.price,
                commission=fill.commission or 0.0,
                fees=fill.fees or 0.0,
            )
        )

    for plan in plans.values():
        if plan.position_id.startswith("UNRESOLVED:"):
            continue
        rows = conn.execute(
            "SELECT id, direction, open_price, contracts FROM legs WHERE position_id = ?",
            (plan.position_id,),
        ).fetchall()
        corrected = {c.leg_id: c.broker for c in plan.corrections}
        total = 0.0
        for row in rows:
            price = corrected.get(row["id"], row["open_price"])
            cash = _leg_cash(row["direction"], price, row["contracts"])
            if cash is None:
                total = None
                break
            total += cash
        plan.recomputed_net = None if total is None else round(total, 2)

    return sorted(plans.values(), key=lambda p: p.position_id)


def apply_plan(conn, plans, now=None):
    """Write the corrections for every WRITABLE plan. Caller owns the commit."""
    stamp = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    written = []
    for plan in plans:
        if not plan.writable:
            continue
        for c in plan.corrections:
            conn.execute(
                "UPDATE legs SET open_price = ?, commission = ?, fees = ? WHERE id = ?",
                (c.broker, c.commission, c.fees, c.leg_id),
            )
        conn.execute(
            "UPDATE positions SET fills_confirmed_at = ? WHERE id = ?",
            (stamp, plan.position_id),
        )
        written.append(plan.position_id)
    return written, stamp
