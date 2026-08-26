"""W151 - resolve a parsed broker fill to the HELM leg it belongs to.

READ-ONLY. This module reads; the confirm write lives elsewhere.

It replaces the key in activity_cmd.find_matching_position, which matches
on ticker + expiration + strike + option type, returns the FIRST leg it
finds, and reads neither direction nor quantity (HELM-187). That is the
key that printed a confident MATCH on an MU condor whose strikes were
wrong by a whole width (W104).

Three rules, and the second and third are the point:

  1. Only the REAL book can receive a broker fill. Paper has no broker.
  2. Direction is part of the key. A BOUGHT CLOSING row closes a SHORT
     leg - so a fill that agrees on every field except direction is not
     a match, and must not be treated as one.
  3. Quantity is CARRIED, never matched. Match on it and a partial close
     (20 sold, 7 bought back, 13 assigned) can never be represented.

A key that cannot fail is worse than no key, so a near miss is reported
as a NEAR_MISS naming the field that differed - never as a silent
NO_MATCH, and never as a match. Ambiguity is refused, not resolved by
picking the first row.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

RESOLVED = "RESOLVED"
AMBIGUOUS = "AMBIGUOUS"
NEAR_MISS = "NEAR_MISS"
NO_MATCH = "NO_MATCH"
SKIPPED = "SKIPPED"


@dataclass
class Match:
    outcome: str
    fill: object
    leg: dict = None
    position: dict = None
    candidates: list = field(default_factory=list)
    note: str = ""

    @property
    def ok(self):
        return self.outcome == RESOLVED

    @property
    def stored_price(self):
        return None if not self.leg else self.leg.get("open_price")

    def describe(self):
        head = "%-10s %-16s %-15s" % (self.outcome, self.fill.symbol, self.fill.action)
        if self.outcome == RESOLVED:
            return head + " -> " + str(self.leg["id"]) + "  " + self.note
        return head + "  " + self.note


def open_readonly(db_path):
    """Open the live database read-only. A write here must raise, not succeed."""
    conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def candidate_legs(conn, ticker, statuses=("OPEN",), book="REAL"):
    """Every REAL leg for this ticker whose POSITION is in the given statuses."""
    marks = ",".join("?" for _ in statuses)
    rows = conn.execute(
        "SELECT l.*, p.status AS position_status, p.book AS book, p.ticker AS ticker "
        "FROM legs l JOIN positions p ON p.id = l.position_id "
        "WHERE p.ticker = ? AND p.book = ? AND p.status IN (" + marks + ")",
        (ticker, book) + tuple(statuses),
    ).fetchall()
    return [dict(r) for r in rows]


def _same_contract(leg, fill):
    return (
        str(leg.get("expiration") or "")[:10] == (fill.expiration or "")
        and (leg.get("option_type") or "").upper() == (fill.option_type or "")
        and leg.get("strike") is not None
        and abs(float(leg["strike"]) - float(fill.strike)) < 0.01
    )


def resolve(conn, fill, statuses=("OPEN",), book="REAL"):
    """Resolve one Fill to one leg, or refuse and say why."""
    if not fill.is_option:
        return Match(SKIPPED, fill, note="share row, not an option leg")
    if fill.leg_direction is None:
        return Match(
            SKIPPED, fill,
            note="settlement row (" + fill.action + ") - carries no fill price to confirm",
        )

    legs = candidate_legs(conn, fill.ticker, statuses=statuses, book=book)
    contract_hits = [l for l in legs if _same_contract(l, fill)]
    exact = [l for l in contract_hits if (l.get("direction") or "").upper() == fill.leg_direction]

    if len(exact) == 1:
        leg = exact[0]
        note = ""
        if leg.get("contracts") and abs(fill.qty) > int(leg["contracts"]):
            # Not a failure: quantity is carried, not matched. But say it.
            note = "quantity %d exceeds the leg's %s" % (abs(fill.qty), leg["contracts"])
        elif leg.get("contracts") and abs(fill.qty) < int(leg["contracts"]):
            note = "partial: %d of %s" % (abs(fill.qty), leg["contracts"])
        return Match(RESOLVED, fill, leg=leg, position=None, candidates=exact, note=note)

    if len(exact) > 1:
        return Match(
            AMBIGUOUS, fill, candidates=exact,
            note="%d legs share this contract AND direction - refusing to guess" % len(exact),
        )

    if contract_hits:
        # The old key would have returned one of these and called it a match.
        found = sorted({(l.get("direction") or "?") for l in contract_hits})
        return Match(
            NEAR_MISS, fill, candidates=contract_hits,
            note="contract matches but DIRECTION does not: broker says "
                 + str(fill.leg_direction) + ", HELM holds " + "/".join(found),
        )

    return Match(NO_MATCH, fill, note="no REAL leg for this contract in " + "/".join(statuses))


def resolve_all(conn, fills, statuses=("OPEN",), book="REAL"):
    return [resolve(conn, f, statuses=statuses, book=book) for f in fills]
