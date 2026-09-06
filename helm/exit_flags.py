"""W158 -- the exit-flag decision log. REPORTS, NEVER ACTS.

An exit-relevant signal on the REAL book is a DECISION POINT, not an
instruction: act on it, or log the override, the same day. HELM records
that the flag fired and what was decided about it. It closes nothing and
gates nothing -- HELM-193, the same doctrine exit_alert.py works under.

WHY AN EVENT LOG AND NOT A STATE READ. Measured 2026-09-06 on the live
book: eleven (position, kind) flags stood open, and only two were still
firing on the latest check. Nine had fired and cleared. Five of the seven
thesis flags had IMPROVED since firing. A surface that renders "what is
broken right now" hides exactly the cases that make a flag a review
trigger rather than a sell signal.

THE THREE SIGNAL KINDS are the same three tools/exit_discipline.py's M1
uses, deliberately, so the scorecard and the surface cannot disagree:

    thesis  checks.thesis_broken = 1        (written on LONG_CALL journals only)
    target  pnl_pct >= the strategy's profit target  (25%, 50% CSP/CC)
    dte     dte_now <= the 21-day management deadline

Precedence within one check row is thesis > target > dte, matching M1.

ONE OPEN DECISION PER (position, kind). A kind that keeps firing while a
decision is already open does not open a second one. A kind that fires
again AFTER being dispositioned opens a new decision point, because it is
a new decision.

DISPOSITIONS, and which of them are Russ's words:

    ACTED     the position closed on or after the flag date. INFERRED by
              settle_closed() from the close itself, marked decided_by
              'inferred-close'. Nobody typed it.
    KEEP      Russ looked and chose to hold, with a reason. STATED --
              only `helm pending keep` writes it.
    (none)    silent. The flag fired and nothing was recorded.

That three-way split is what feeds M6 in the scorecard. Rows seeded from
the existing journal carry seeded=1: the flag genuinely fired on that date
and the journal proves it, but no surface showed it at the time, so a
silent seeded flag is not evidence that anyone ignored anything.

Writes ONE new table. Nothing is added to positions, legs or checks --
W155: ten models map SELECT * straight into a constructor, so a new
column on an existing table breaks every path that builds that model.
"""
from datetime import datetime, date, timedelta

TARGET_PCT = {"CSP": 50.0, "COVERED_CALL": 50.0}
TARGET_DEFAULT = 25.0
DTE_MANAGE = 21

KINDS = ("thesis", "target", "dte")

DDL = """CREATE TABLE IF NOT EXISTS exit_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    flag_date TEXT NOT NULL,
    fired_at TEXT NOT NULL,
    mark_at_flag REAL,
    pct_at_flag REAL,
    dte_at_flag INTEGER,
    seeded INTEGER NOT NULL DEFAULT 0,
    disposition TEXT,
    decided_at TEXT,
    decided_date TEXT,
    decided_by TEXT,
    reason TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_exit_flags_pos ON exit_flags(position_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exit_flags_pos_kind_date
    ON exit_flags(position_id, kind, flag_date);"""


# --------------------------------------------------------------------- pure
def classify(strategy, pct, dte, thesis_broken):
    """Which signal, if any, does ONE check row fire? Pure.

    Precedence thesis > target > dte, identical to exit_discipline.py M1.
    """
    if thesis_broken == 1:
        return "thesis"
    tgt = TARGET_PCT.get(strategy, TARGET_DEFAULT)
    if pct is not None and pct >= tgt:
        return "target"
    if dte is not None and dte <= DTE_MANAGE:
        return "dte"
    return None


def new_flags(strategy, checks, already_open, dispositioned_after):
    """Pure. Which (kind, check) pairs open a NEW decision point?

    checks: ascending dicts with checked_at, pnl_pct, dte_now,
            thesis_broken, pnl_unrealized.
    already_open: set of kinds with an undispositioned row right now.
    dispositioned_after: {kind: 'YYYY-MM-DD'} -- the date the last
            decision on that kind was taken. A re-fire only counts after
            it, so settling a flag does not immediately re-open it.
    Returns a list of (kind, check) in journal order, at most one per kind.
    """
    out, taken = [], set()
    for c in checks:
        k = classify(strategy, c.get("pnl_pct"), c.get("dte_now"),
                     c.get("thesis_broken"))
        if not k or k in taken or k in already_open:
            continue
        day = (c.get("checked_at") or "")[:10]
        cut = dispositioned_after.get(k)
        if cut and day <= cut:
            continue
        taken.add(k)
        out.append((k, c))
    return out


def _tdays(a, b):
    """Trading days a->b, weekday approximation. Deliberately the cheap
    one: this is a surface, not a measure, and it must never depend on an
    importable package to render."""
    n, x = 0, a
    while x < b:
        x += timedelta(1)
        if x.weekday() < 5:
            n += 1
    return n


# ------------------------------------------------------------------- writes
_CHK_COLS = ("checked_at", "pnl_pct", "dte_now", "thesis_broken",
             "pnl_unrealized")


def _checks(conn, position_id):
    """Dicts built from a named column list, not from conn.row_factory --
    this module must read the same whoever opened the connection."""
    return [dict(zip(_CHK_COLS, r)) for r in conn.execute(
        "SELECT checked_at, pnl_pct, dte_now, thesis_broken, pnl_unrealized "
        "FROM checks WHERE position_id=? AND data_quality='GOOD' "
        "AND pnl_unrealized IS NOT NULL ORDER BY checked_at", (position_id,))]


def _state(conn, position_id):
    """(kinds with an open decision, {kind: last decided_date})."""
    open_kinds, after = set(), {}
    for r in conn.execute(
            "SELECT kind, disposition, decided_date FROM exit_flags "
            "WHERE position_id=?", (position_id,)):
        if r[1] is None:
            open_kinds.add(r[0])
        elif r[2] and r[2] > after.get(r[0], ""):
            after[r[0]] = r[2]
    return open_kinds, after


def scan(conn, seed=False, now=None):
    """Open a decision point for every signal that has newly fired on the
    OPEN REAL book. Returns the rows written.

    seed=False (the scheduled path) records only signals that fired on the
    most recent journal day -- what HELM saw today.
    seed=True reconstructs from the whole journal, for the first run.
    """
    conn.executescript(DDL)
    stamp = now or datetime.now().isoformat(timespec="seconds")
    today = stamp[:10]
    written = []
    for p in conn.execute("SELECT id, ticker, strategy FROM positions "
                          "WHERE status='OPEN' AND book='REAL'"):
        pid, ticker, strategy = p[0], p[1], p[2]
        chk = _checks(conn, pid)
        if not chk:
            continue
        if not seed:
            last_day = chk[-1]["checked_at"][:10]
            chk = [c for c in chk if c["checked_at"][:10] == last_day]
        open_kinds, after = _state(conn, pid)
        for kind, c in new_flags(strategy, chk, open_kinds, after):
            try:
                conn.execute(
                    "INSERT INTO exit_flags (position_id, kind, flag_date, "
                    "fired_at, mark_at_flag, pct_at_flag, dte_at_flag, seeded) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (pid, kind, c["checked_at"][:10], stamp,
                     c.get("pnl_unrealized"), c.get("pnl_pct"),
                     c.get("dte_now"), 1 if seed else 0))
            except Exception:
                continue  # unique index: this exact flag day is already logged
            written.append({"position_id": pid, "ticker": ticker, "kind": kind,
                            "flag_date": c["checked_at"][:10],
                            "mark_at_flag": c.get("pnl_unrealized"),
                            "seeded": bool(seed)})
    conn.commit()
    return written


def settle_closed(conn, now=None):
    """Any open decision on a position that has since CLOSED was acted on
    -- by the close itself. INFERRED, and stamped as inferred."""
    conn.executescript(DDL)
    stamp = now or datetime.now().isoformat(timespec="seconds")
    rows = list(conn.execute(
        "SELECT f.id, f.kind, p.closed_at, p.exit_reason FROM exit_flags f "
        "JOIN positions p ON p.id = f.position_id "
        "WHERE f.disposition IS NULL AND p.status='CLOSED'"))
    for fid, _kind, closed_at, reason in rows:
        conn.execute(
            "UPDATE exit_flags SET disposition='ACTED', decided_at=?, "
            "decided_date=?, decided_by='inferred-close', reason=? WHERE id=?",
            (stamp, (closed_at or stamp)[:10], reason or "manual", fid))
    conn.commit()
    return len(rows)


def keep(conn, position_id, kind, reason, note=None, now=None):
    """Russ looked and chose to hold. The one disposition nobody infers."""
    conn.executescript(DDL)
    stamp = now or datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "UPDATE exit_flags SET disposition='KEEP', decided_at=?, "
        "decided_date=?, decided_by='russ', reason=?, note=? "
        "WHERE position_id=? AND kind=? AND disposition IS NULL",
        (stamp, stamp[:10], reason, note, position_id, kind))
    conn.commit()
    return cur.rowcount


# -------------------------------------------------------------------- reads
def pending(conn, as_of=None):
    """Every open decision point, with what has happened since it fired."""
    conn.executescript(DDL)
    out = []
    for r in conn.execute(
            "SELECT f.id, f.position_id, f.kind, f.flag_date, f.mark_at_flag, "
            "f.pct_at_flag, f.dte_at_flag, f.seeded, p.ticker, p.strategy "
            "FROM exit_flags f JOIN positions p ON p.id=f.position_id "
            "WHERE f.disposition IS NULL AND p.status='OPEN' AND p.book='REAL' "
            "ORDER BY f.flag_date"):
        d = dict(zip(("id", "position_id", "kind", "flag_date", "mark_at_flag",
                      "pct_at_flag", "dte_at_flag", "seeded", "ticker",
                      "strategy"), r))
        chk = _checks(conn, d["position_id"])
        last = chk[-1] if chk else None
        d["mark_now"] = last.get("pnl_unrealized") if last else None
        d["pct_now"] = last.get("pnl_pct") if last else None
        d["as_of"] = last["checked_at"][:10] if last else None
        d["still_firing"] = bool(last) and classify(
            d["strategy"], last.get("pnl_pct"), last.get("dte_now"),
            last.get("thesis_broken")) == d["kind"]
        if d["mark_now"] is not None and d["mark_at_flag"] is not None:
            d["drift"] = d["mark_now"] - d["mark_at_flag"]
        else:
            d["drift"] = None
        end = date.fromisoformat(as_of or d["as_of"] or d["flag_date"])
        d["age_td"] = _tdays(date.fromisoformat(d["flag_date"]), end)
        out.append(d)
    return out


def dispositions_for(conn, position_ids):
    """M6's data source: {position_id: {kind: 'ACTED'|'KEEP'|None}}."""
    out = {}
    if not position_ids:
        return out
    qs = ",".join("?" * len(position_ids))
    for pid, kind, disp in conn.execute(
            "SELECT position_id, kind, disposition FROM exit_flags "
            "WHERE position_id IN (%s)" % qs, list(position_ids)):
        out.setdefault(pid, {})[kind] = disp
    return out
