"""W173 (s120) -- what the paper exit agent CONSIDERED, one row per position per run.

Until s120 the agent's only record was its ledger row ("held 54") and a log
line per close. Nothing said which rule each held position was nearest to, or
how near -- so "the rule cut it a day early" and "the rule nearly fired and
didn't" were both unmeasurable, and W19's learning question has been waiting
on exactly that corpus.

This module is PURE arithmetic over the assessment `check_one` already
computed (`consider`) plus one writer (`record`). It decides nothing and
closes nothing. The rule lines it reports are decision.evaluate's and
long_exit's OWN constants and settings lookup -- the same doctrine the agent
acts on, read from the same arms record (HELM-101 s4), never a second copy.

Units, so the columns are readable without this file:
  pnl_pct, hwm_pct, trail_floor, stop_pct, target_pct  -- FRACTIONS of the
      credit (credit families), of max profit (debit spreads), of the debit
      (long families). 0.137 is +13.7%.
  gap_target, gap_trail, gap_stop  -- POINTS (percentage points) still to go
      before that rule fires. Positive = has not fired. gap_target = target -
      pnl; gap_trail = pnl - floor; gap_stop = pnl - stop.
  gap_calendar, gap_hard  -- DAYS to go before the calendar rule binds.
  near_miss -- 1 when the position was HELD and any applicable gap is inside
      NEAR_POINTS / NEAR_DAYS. A fired rule is a fire, not a near-miss.

ONE NEW TABLE, no column on any existing one (W155): ten models map
SELECT * into a constructor, and a column on `checks` or `legs` breaks them.
"""
from datetime import datetime

from helm import decision as D
from helm import long_exit as L

NEAR_POINTS = 5.0   # within 5 points of a P&L line
NEAR_DAYS = 3       # within 3 days of a calendar line

# Which rules apply to which family, matching decision.evaluate's branches.
# DIAGONAL has NO profit-target branch (W159 measured it in s116): the family
# falls through to the calendar only, so its target_pct is reported as None
# rather than pretending a rule exists.
_TARGET_FAMILIES = (D.CREDIT_FAMILY, D.COVERED_FAMILY, D.DEBIT_SPREAD_FAMILY)

DDL = """CREATE TABLE IF NOT EXISTS exit_considered (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_started_at TEXT NOT NULL,
    position_id TEXT NOT NULL,
    ticker TEXT,
    strategy TEXT,
    book TEXT,
    family TEXT,
    verdict TEXT,
    acted INTEGER NOT NULL DEFAULT 0,
    outcome TEXT,
    pnl_pct REAL,
    dte_min INTEGER,
    dte_cal INTEGER,
    target_pct REAL,
    dte_exit INTEGER,
    hwm_pct REAL,
    trail_floor REAL,
    stop_pct REAL,
    dte_soft INTEGER,
    dte_hard INTEGER,
    gap_target REAL,
    gap_calendar INTEGER,
    gap_trail REAL,
    gap_stop REAL,
    gap_hard INTEGER,
    nearest_rule TEXT,
    nearest_gap REAL,
    near_miss INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exit_considered_run ON exit_considered(run_started_at);
CREATE INDEX IF NOT EXISTS idx_exit_considered_pos ON exit_considered(position_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exit_considered_run_pos
    ON exit_considered(run_started_at, position_id);"""

COLUMNS = ("run_started_at", "position_id", "ticker", "strategy", "book", "family",
           "verdict", "acted", "outcome", "pnl_pct", "dte_min", "dte_cal",
           "target_pct", "dte_exit", "hwm_pct", "trail_floor", "stop_pct",
           "dte_soft", "dte_hard", "gap_target", "gap_calendar", "gap_trail",
           "gap_stop", "gap_hard", "nearest_rule", "nearest_gap", "near_miss",
           "note", "created_at")


def ensure_table(conn):
    conn.executescript(DDL)


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def _dtes(a, legs=None):
    """(dte_min, dte_max) from the assessment's legs, as decision.dte sees them."""
    out = []
    for l in (a.get("legs") or legs or []):
        exp = l.get("expiration") if isinstance(l, dict) else getattr(l, "expiration", None)
        if not exp:
            continue
        try:
            d = D.dte(exp)
        except Exception:
            d = None
        if d is not None:
            out.append(d)
    return (min(out), max(out)) if out else (None, None)


def consider(a, pos, run_started_at, outcome="held", thresholds=None):
    """One row for one position from the assessment `check_one` returned.

    `outcome` is what the agent DID with the verdict this run: 'held',
    'closed', 'deferred' (rule fired, a leg had no quote), 'dry' (dry run),
    'failed' (check raised). Pure -- the only lookup is decision's settings,
    and `thresholds=(target_pct, dte_exit)` lets a test bypass it.
    """
    a = a or {}
    pos = dict(pos) if not isinstance(pos, dict) else pos
    strategy = pos.get("strategy")
    fam = D._family(strategy)
    if thresholds is not None:
        pt, de = thresholds
    else:
        s = D._settings(pos.get("account_id"), strategy)
        pt = s.get("profit_target_pct") or D.DEFAULT_PROFIT_TARGET
        pt = pt if pt <= 1 else pt / 100.0
        de = s.get("dte_exit_threshold") or D.DEFAULT_DTE_EXIT

    verdict = a.get("core_reason")
    pnl = a.get("pnl_pct")
    pnl = (pnl / 100.0) if pnl is not None else None      # check_one's pnl_pct is a PERCENT
    dte_min, dte_max = _dtes(a)
    if dte_min is None:
        dte_min = a.get("dte_now") if a.get("dte_now") is not None else (a.get("primary_leg") or {}).get("dte_now")
        dte_max = dte_min
    dte_cal = dte_max if fam == D.DIAGONAL_FAMILY else dte_min

    row = {k: None for k in COLUMNS}
    row.update({
        "run_started_at": run_started_at, "position_id": pos.get("id"),
        "ticker": pos.get("ticker"), "strategy": strategy, "book": pos.get("book"),
        "family": fam, "verdict": verdict, "acted": 1 if outcome == "closed" else 0,
        "outcome": outcome, "pnl_pct": _r(pnl), "dte_min": dte_min, "dte_cal": dte_cal,
        "near_miss": 0, "created_at": datetime.now().isoformat(),
    })
    gaps = []   # (rule, gap, unit)

    if fam == D.LONG_DEBIT_FAMILY:
        arms = a.get("arms") or {}
        gb = arms.get("give_back") or {}
        v3 = arms.get("v3") or {}
        hwm = gb.get("hwm")
        floor = gb.get("floor")
        stop = v3.get("stop", L.STOP_LOSS_PCT)
        soft = v3.get("dte_soft", L.DTE_SOFT)
        hard = v3.get("dte_hard", L.DTE_HARD)
        row.update({"hwm_pct": _r(hwm), "trail_floor": _r(floor), "stop_pct": _r(stop),
                    "dte_soft": soft, "dte_hard": hard, "dte_cal": None})
        if pnl is not None and floor is not None:
            row["gap_trail"] = _r((pnl - floor) * 100.0, 2); gaps.append(("GIVE_BACK", row["gap_trail"], "pts"))
        if pnl is not None and stop is not None:
            row["gap_stop"] = _r((pnl - stop) * 100.0, 2); gaps.append(("STOP_LOSS", row["gap_stop"], "pts"))
        if dte_min is not None:
            row["gap_hard"] = dte_min - hard; gaps.append(("DTE_7", row["gap_hard"], "days"))
            # DTE_21 binds only when not positive; report the days regardless,
            # the sign of pnl says whether it would.
            row["gap_calendar"] = dte_min - soft; gaps.append(("DTE_21", row["gap_calendar"], "days"))
        if not arms:
            row["note"] = "no arms record on the assessment"
    else:
        row.update({"target_pct": _r(pt) if fam in _TARGET_FAMILIES else None, "dte_exit": de})
        if fam in _TARGET_FAMILIES and pnl is not None:
            row["gap_target"] = _r((pt - pnl) * 100.0, 2); gaps.append(("PROFIT_TARGET", row["gap_target"], "pts"))
        if fam == D.DIAGONAL_FAMILY:
            row["note"] = "diagonal family has no profit-target branch (W159)"
        if dte_cal is not None:
            row["gap_calendar"] = dte_cal - de; gaps.append(("DTE_MANAGE", row["gap_calendar"], "days"))

    # nearest rule: smallest gap measured against its own near threshold
    if gaps:
        def _scaled(g):
            rule, gap, unit = g
            if gap is None:
                return float("inf")
            return gap / (NEAR_POINTS if unit == "pts" else NEAR_DAYS)
        rule, gap, unit = min(gaps, key=_scaled)
        row["nearest_rule"], row["nearest_gap"] = rule, gap
        # a near-miss is a HELD position sitting inside the band -- a fired
        # rule is a fire, not a near-miss, however small its overshoot
        row["near_miss"] = 1 if (verdict is None and gap is not None
                                 and gap <= (NEAR_POINTS if unit == "pts" else NEAR_DAYS)) else 0
    return row


def record(conn, rows):
    """Write the run's rows in one transaction. Returns the count written.
    Never raises into the agent -- a failed consideration log must not stop a
    close, but it must not be silent either (the caller prints)."""
    if not rows:
        return 0
    ensure_table(conn)
    ph = ",".join("?" * len(COLUMNS))
    conn.executemany(
        "INSERT OR REPLACE INTO exit_considered (%s) VALUES (%s)" % (",".join(COLUMNS), ph),
        [tuple(r.get(c) for c in COLUMNS) for r in rows])
    conn.commit()
    return len(rows)
