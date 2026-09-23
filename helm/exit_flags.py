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

DIAGONALS TAKE NONE OF THE THREE (W180 step 5, 2026-09-23). They have their
own kinds, read from the legs -- see the W180 block at the foot of this file.
M1 and M6 are unaffected: M6 counts `thesis` only, which diagonals never wrote.

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
    settle_legs(conn, now=stamp)     # W180 step 5: before, so a harvested
                                     # short's flag cannot block its successor
    for p in conn.execute("SELECT id, ticker, strategy FROM positions "
                          "WHERE status='OPEN' AND book='REAL'"):
        pid, ticker, strategy = p[0], p[1], p[2]
        if is_diagonal(strategy):
            written += _scan_diagonal(conn, pid, ticker, stamp, seed)
            continue
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
    # W180 step 5, found live 2026-09-23 15:22: a scan emits from the WHOLE
    # latest journal day, so a short harvested at 12:51 still fired harvest50
    # off the 10:00 and 12:30 rows -- AFTER the settle above had run. Settle
    # again so a flag born already answered never reaches a surface open.
    settle_legs(conn, now=stamp)
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
    return len(rows) + settle_legs(conn, now=stamp)   # W180 step 5


def keep(conn, position_id, kind, reason, note=None, now=None):
    """Russ looked and chose to hold. The one disposition nobody infers."""
    conn.executescript(DDL)
    stamp = now or datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "UPDATE exit_flags SET disposition='KEEP', decided_at=?, "
        "decided_date=?, decided_by='russ', reason=?, "
        "note=CASE WHEN ? IS NULL THEN note "
        "ELSE COALESCE(note || ' | ', '') || ? END "
        "WHERE position_id=? AND kind=? AND disposition IS NULL",
        (stamp, stamp[:10], reason, note, note, position_id, kind))
    conn.commit()
    return cur.rowcount


# -------------------------------------------------------------------- reads
def pending(conn, as_of=None, ensure=True):
    """Every open decision point, with what has happened since it fired.

    ensure=False skips the DDL so a READ-ONLY connection can call this.
    PG needs that: W34 says the board reads live and writes nothing, and a
    CREATE TABLE IF NOT EXISTS is still a write. The table not existing then
    surfaces as an error to the caller rather than being papered over, which
    is the honest failure for a reader. (s117)
    """
    if ensure:
        conn.executescript(DDL)
    out = []
    for r in conn.execute(
            "SELECT f.id, f.position_id, f.kind, f.flag_date, f.mark_at_flag, "
            "f.pct_at_flag, f.dte_at_flag, f.seeded, p.ticker, p.strategy, f.note "
            "FROM exit_flags f JOIN positions p ON p.id=f.position_id "
            "WHERE f.disposition IS NULL AND p.status='OPEN' AND p.book='REAL' "
            "ORDER BY f.flag_date"):
        d = dict(zip(("id", "position_id", "kind", "flag_date", "mark_at_flag",
                      "pct_at_flag", "dte_at_flag", "seeded", "ticker",
                      "strategy", "note"), r))
        d["label"] = KIND_LABEL.get(d["kind"], d["kind"])
        chk = _checks(conn, d["position_id"])
        last = chk[-1] if chk else None
        d["mark_now"] = last.get("pnl_unrealized") if last else None
        d["pct_now"] = last.get("pnl_pct") if last else None
        d["as_of"] = last["checked_at"][:10] if last else None
        if is_diagonal(d["strategy"]):
            d["still_firing"] = d["kind"] in diag_firing_now(conn, d["position_id"])
        else:
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


# ================================================================ W180 step 5
# THE DIAGONAL'S FLAGS. A diagonal is a long call with a sequence of shorts
# sold against it (Russ, 2026-09-21), and neither of its states is served by
# the three generic kinds above:
#
#   * SHORT ON -- the generic `target` read pnl_pct, which is POSITION P&L over
#     the structure's debit. The harvest rule reads the SHORT's own capture
#     (W180 4.3), and the two diverge: a short at 69% captured sits on a
#     position that is down (09-23, B). The generic `dte` fired on the short's
#     21 DTE unconditionally, which W180 4.4 refutes on this book (MSFT −159%
#     at 21 DTE, then +100%).
#   * LONG ONLY -- nothing fired at all. W184: a bare long sat unprompted, and
#     three real ones (AA, EQT, UNH) had been harvested into that state on
#     2026-09-23.
#
# So a DIAGONAL-family position never takes the generic kinds. It takes these,
# computed from the legs and the journal's per-leg marks (leg_checks):
#
#   SHORT ON, on the live short:
#     pos_stop     position pnl_pct <= −50 (W180 §3: the v3 backstop on the
#                  position is a fact whatever the short is doing)
#     breach       spot through the short strike on 2+ consecutive check days,
#                  worst-of-day -- thesis.py's confirmed-breach construction,
#                  the best-measured signal HELM has (W180 4.1)
#     worthless    short mark <= $0.05 (W180 4.2)
#     harvest50    >= 50% of the short's premium captured  } BOTH are flagged on
#     harvest25    >= 25% captured                         } the real book, as
#                  separate decisions; HELM records which Russ took (W180 §8.1)
#
#   LONG ONLY, on the long (v3 governs it -- W180 4.5):
#     long_stop     the long's OWN move <= −50% of its ORIGINAL debit
#     long_giveback the long's own move <= its peak − 20 points (never deeper
#                   than the stop -- v3's cap)
#     long_dte7     long DTE <= 7, any sign
#     long_dte21    long DTE <= 21 and the long's own move <= 0
#     bare          the long has no short sold against it. Always fires in LONG
#                   ONLY: a decision -- sell the next short, or hold it bare.
#
# THE DENOMINATOR (Russ, 2026-09-23): the long-only percentages are the long's
# OWN price move over its ORIGINAL debit. Rent collected never enters them,
# because every harvest would otherwise widen the −50% line -- a cap in
# reverse. Effective basis (posview) is the number Russ reads; this is the one
# the rules use. Measured at the decision, 12:30 marks: AA −63% own vs −39%
# with rent; EQT −46% vs −22%; UNH −39% vs −20%.
#
# REAL book only, like everything in this module. Reports, never acts. v3's
# CONFIRM_DAYS is NOT applied: a flag is a review trigger, and v3's own
# measurement found the confirmation buffer saved nothing (0 saves, 5 delays).
#
# RE-FIRE IS EPISODIC FOR THESE KINDS. The generic kinds re-open a decision on
# the next day they fire after a disposition, which for a standing condition
# (a bare long, a short sitting above 50%) is a daily nag for a decision
# already taken. A diagonal kind re-opens only after it has STOPPED firing on
# some check since the decision and then fired again -- a new episode is a new
# decision; the same episode is not.
#
# A LEG CLOSE SETTLES THE SHORT'S FLAGS. `helm close --leg` (step 3) leaves the
# position OPEN, so settle_closed() never saw it: UNH's 09-18 `dte` flag stood
# open on a short harvested 09-23. settle_legs() marks every open short-side
# flag ACTED, 'inferred-leg-close', when the short it was about has closed --
# and a `bare` flag ACTED, 'inferred-resell', once a new short is on (step 6).
from helm import long_exit as _LE
from helm import posview as _PV

DIAG_STRATEGIES = ("DIAGONAL", "DIAGONAL_PUT", "PMCC")
HARVEST_LEVELS = (50.0, 25.0)
WORTHLESS_MARK = 0.05
BREACH_DAYS = 2
STOP_PCT = _LE.STOP_LOSS_PCT * 100.0          # −50.0, v3's own constant
GIVE_BACK_PTS = _LE.GIVE_BACK_BAND * 100.0     # 20.0
LONG_DTE_SOFT = _LE.DTE_SOFT                   # 21
LONG_DTE_HARD = _LE.DTE_HARD                   # 7

SHORT_KINDS = ("breach", "worthless", "harvest50", "harvest25")
LONG_KINDS = ("long_stop", "long_giveback", "long_dte7", "long_dte21", "bare")
DIAG_KINDS = ("pos_stop",) + SHORT_KINDS + LONG_KINDS
# Kinds a leg close answers. The generic `target`/`dte` are here because
# diagonals took them before this step -- UNH's 09-18 `dte` is one.
_LEG_ANSWERED = SHORT_KINDS + ("target", "dte")

KIND_LABEL = {
    "thesis": "thesis broken", "target": "profit target", "dte": "21 DTE",
    "pos_stop": "position −50%",
    "breach": "short breached (confirmed)", "worthless": "short ≤ $0.05",
    "harvest50": "short 50% captured", "harvest25": "short 25% captured",
    "long_stop": "long −50% of its debit", "long_giveback": "long gave back 20 pts",
    "long_dte7": "long ≤ 7 DTE", "long_dte21": "long ≤ 21 DTE, not positive",
    "bare": "long is bare — no short on it",
}


def is_diagonal(strategy):
    return (strategy or "").upper() in DIAG_STRATEGIES


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_opt(l):
    return (l.get("option_type") or "") not in ("", "STOCK")


def _live_at(leg, t):
    """Was this leg on the book at check time t? Opened on or before t's day,
    and either still OPEN or closed after t. A mark alone is not enough: a
    leg must not be read on a check that predates it."""
    od = str(leg.get("open_date") or leg.get("created_at") or "")[:10]
    if od and od > t[:10]:
        return False
    if str(leg.get("status") or "").upper() == "OPEN":
        return True
    cd = str(leg.get("close_date") or "")
    return bool(cd) and cd > t


def _dte_on(exp, day):
    try:
        return (date.fromisoformat(str(exp)[:10]) - date.fromisoformat(day)).days
    except (TypeError, ValueError):
        return None


def diag_series(legs, rows):
    """Pure. For each check row (ascending), which diagonal kinds fire, and the
    readings they fired on. rows: dicts with checked_at, spot_price, pnl_pct,
    marks {leg_id: price}. Returns [(row, [kinds], info)] -- kinds in
    precedence order (loss causes before calendar causes before harvest)."""
    from helm.thesis import buffer_pct
    opt = [l for l in legs or [] if _is_opt(l)]
    shorts = [l for l in opt if str(l.get("direction") or "").upper() == "SHORT"]
    longs = [l for l in opt if str(l.get("direction") or "").upper() == "LONG"]
    peak = {}          # long leg id -> best own-move % seen on any check
    worst = {}         # short leg id -> {day: worst buffer % that day}
    out = []
    for r in rows:
        t = r.get("checked_at") or ""
        day = t[:10]
        marks = r.get("marks") or {}
        ls = [s for s in shorts if s["id"] in marks and _live_at(s, t)]
        ll = [l for l in longs if l["id"] in marks and _live_at(l, t)]
        kinds, info = [], {}
        lg = sorted(ll, key=lambda l: str(l.get("expiration") or ""))[-1] if ll else None
        lpct = None
        if lg is not None:
            op, m = _f(lg.get("open_price")), _f(marks.get(lg["id"]))
            if op and m is not None:
                lpct = (m - op) / op * 100.0
                peak[lg["id"]] = max(peak.get(lg["id"], lpct), lpct)
        if ls:
            s = sorted(ls, key=lambda l: str(l.get("expiration") or ""))[0]
            sm = _f(marks.get(s["id"]))
            cap = _PV.captured_pct(s, sm)
            b = buffer_pct([s], r.get("spot_price"))
            if b is not None:
                w = worst.setdefault(s["id"], {})
                w[day] = min(w.get(day, b[0]), b[0])
            streak = 0
            for d in sorted(worst.get(s["id"], {}), reverse=True):
                if worst[s["id"]][d] < 0:
                    streak += 1
                else:
                    break
            pp = _f(r.get("pnl_pct"))
            if pp is not None and pp <= STOP_PCT:
                kinds.append("pos_stop")
            if streak >= BREACH_DAYS:
                kinds.append("breach")
            if sm is not None and sm <= WORTHLESS_MARK:
                kinds.append("worthless")
            for lvl in HARVEST_LEVELS:
                if cap is not None and cap >= lvl:
                    kinds.append("harvest%d" % lvl)
            info = {"state": "SHORT ON", "leg": s["id"], "captured": cap,
                    "mark": sm, "streak": streak,
                    "dte": _dte_on(s.get("expiration"), day)}
        elif lg is not None:
            ldte = _dte_on(lg.get("expiration"), day)
            if lpct is not None:
                trail = max(peak[lg["id"]] - GIVE_BACK_PTS, STOP_PCT)
                if lpct <= STOP_PCT:
                    kinds.append("long_stop")
                elif lpct <= trail:
                    kinds.append("long_giveback")
            if ldte is not None and ldte <= LONG_DTE_HARD:
                kinds.append("long_dte7")
            elif ldte is not None and ldte <= LONG_DTE_SOFT and lpct is not None and lpct <= 0:
                kinds.append("long_dte21")
            kinds.append("bare")
            info = {"state": "LONG ONLY", "leg": lg["id"], "long_pct": lpct,
                    "peak": peak.get(lg["id"]), "mark": _f(marks.get(lg["id"])),
                    "debit": _f(lg.get("open_price")), "dte": ldte}
        out.append((r, kinds, info))
    return out


def diag_new_flags(legs, rows, already_open, dispositioned_after, only_day=None):
    """Pure. Which (kind, row, info) open a NEW decision point. Episodic re-fire
    (module note above): after a disposition, a kind counts again only once a
    later check has NOT fired it. only_day limits emission to one journal day
    while the whole history still feeds the peak and the breach streak."""
    out, taken, reset = [], set(), {}
    for r, kinds, info in diag_series(legs, rows):
        day = (r.get("checked_at") or "")[:10]
        for k in DIAG_KINDS:
            cut = dispositioned_after.get(k)
            if cut and day > cut and k not in kinds:
                reset[k] = True
        if only_day and day != only_day:
            continue
        for k in kinds:
            if k in taken or k in already_open:
                continue
            cut = dispositioned_after.get(k)
            if cut and (day <= cut or not reset.get(k)):
                continue
            taken.add(k)
            out.append((k, r, info))
    return out


_LEG_COLS = ("id", "leg_role", "option_type", "direction", "strike",
             "expiration", "contracts", "multiplier", "open_price",
             "close_price", "open_date", "close_date", "status", "created_at")


def _legs(conn, position_id):
    return [dict(zip(_LEG_COLS, r)) for r in conn.execute(
        "SELECT " + ", ".join(_LEG_COLS) + " FROM legs WHERE position_id=? "
        "ORDER BY expiration, id", (position_id,))]


def _diag_rows(conn, position_id, legs=None):
    """GOOD check rows with each leg's journaled mark attached. Named columns,
    never row_factory -- same reason as _checks().

    THE PRIMARY LEG'S MARK IS NOT IN leg_checks ON EVERY POSITION. Measured
    2026-09-23: AA, EQT and UNH have leg_checks rows for the LONG only, 30-43 of
    them, and none ever for the short -- the short was the primary (s117 orders
    front-first) and check_one journals the primary's mid on checks.current_price.
    B, IBM, MSFT and APLD have both legs in leg_checks. So when the leg that
    was primary at a check has no leg_checks mark, it takes checks.current_price.
    "Primary at t" is check_one's own rule: the front-most option leg still on
    the book and not expired that day."""
    legs = legs if legs is not None else _legs(conn, position_id)
    opt = sorted((l for l in legs if _is_opt(l)),
                 key=lambda l: (str(l.get("expiration") or ""), l["id"]))
    marks = {}
    for cid, lid, px in conn.execute(
            "SELECT check_id, leg_id, current_price FROM leg_checks "
            "WHERE position_id=? AND current_price IS NOT NULL", (position_id,)):
        marks.setdefault(cid, {})[lid] = px
    rows = []
    for cid, t, spot, pct, pnl, cur in conn.execute(
            "SELECT id, checked_at, spot_price, pnl_pct, pnl_unrealized, current_price "
            "FROM checks WHERE position_id=? AND data_quality='GOOD' "
            "AND pnl_unrealized IS NOT NULL ORDER BY checked_at", (position_id,)):
        m = dict(marks.get(cid, {}))
        prim = next((l for l in opt if _live_at(l, t)
                     and (_dte_on(l.get("expiration"), t[:10]) or 0) >= 0), None)
        if prim is not None and prim["id"] not in m and cur is not None:
            m[prim["id"]] = cur
        rows.append({"checked_at": t, "spot_price": spot, "pnl_pct": pct,
                     "pnl_unrealized": pnl, "marks": m})
    return rows


def _diag_note(kind, info):
    """The reading the flag fired on, in words. pct_at_flag stays position
    pnl_pct for every kind, so the column means one thing; this carries the
    number the rule actually read."""
    if info.get("state") == "SHORT ON":
        c = info.get("captured")
        return "short %s captured, mark %s, %s DTE%s" % (
            "—" if c is None else "%.0f%%" % c,
            "—" if info.get("mark") is None else "%.2f" % info["mark"],
            info.get("dte"),
            ", breached %dd" % info["streak"] if info.get("streak") else "")
    lp, pk = info.get("long_pct"), info.get("peak")
    return "long %s of its %.2f debit (peak %s), %s DTE" % (
        "—" if lp is None else "%+.0f%%" % lp, info.get("debit") or 0,
        "—" if pk is None else "%+.0f%%" % pk, info.get("dte"))


def settle_legs(conn, now=None):
    """Open diagonal flags that a LEG event has answered. Returns rows settled.

    * a short-side flag, once no short that was live on its flag date is
      still open -> ACTED, 'inferred-leg-close', reason from the leg close's
      lifecycle narrative (HARVEST / WORTHLESS / ...) or SETTLED at expiry.
    * a `bare` flag, once a short opened on or after it is live -> ACTED,
      'inferred-resell'.
    Nobody typed either; both say so in decided_by, like settle_closed()."""
    stamp = now or datetime.now().isoformat(timespec="seconds")
    n = 0
    rows = list(conn.execute(
        "SELECT f.id, f.position_id, f.kind, f.flag_date, p.strategy "
        "FROM exit_flags f JOIN positions p ON p.id=f.position_id "
        "WHERE f.disposition IS NULL AND p.status='OPEN'"))
    for fid, pid, kind, fdate, strat in rows:
        if not is_diagonal(strat):
            continue
        legs = [l for l in _legs(conn, pid) if _is_opt(l)
                and str(l.get("direction") or "").upper() == "SHORT"]
        if kind in _LEG_ANSWERED:
            if any(str(l["status"]).upper() == "OPEN"
                   and str(l.get("open_date") or l.get("created_at") or "")[:10] <= fdate
                   for l in legs):
                continue
            done = sorted((l for l in legs if str(l["status"]).upper() != "OPEN"
                           and str(l.get("close_date") or "")[:10] >= fdate),
                          key=lambda l: str(l.get("close_date") or ""))
            if not done:
                continue
            leg = done[0]
            why = "SETTLED"
            ev = conn.execute(
                "SELECT narrative FROM lifecycle_events WHERE leg_id=? "
                "AND narrative LIKE 'LEG_CLOSED%' ORDER BY occurred_at DESC LIMIT 1",
                (leg["id"],)).fetchone()
            if ev and "reason=" in (ev[0] or ""):
                why = ev[0].split("reason=", 1)[1].split("|", 1)[0].strip()
            conn.execute(
                "UPDATE exit_flags SET disposition='ACTED', decided_at=?, "
                "decided_date=?, decided_by='inferred-leg-close', reason=? "
                "WHERE id=? AND disposition IS NULL",
                (stamp, str(leg.get("close_date") or stamp)[:10], why, fid))
            n += 1
        elif kind == "bare":
            new = [l for l in legs if str(l["status"]).upper() == "OPEN"
                   and str(l.get("open_date") or l.get("created_at") or "")[:10] >= fdate]
            if new:
                conn.execute(
                    "UPDATE exit_flags SET disposition='ACTED', decided_at=?, "
                    "decided_date=?, decided_by='inferred-resell', reason='RESOLD' "
                    "WHERE id=? AND disposition IS NULL",
                    (stamp, str(new[0].get("open_date") or new[0].get("created_at") or stamp)[:10], fid))
                n += 1
    conn.commit()
    return n


def diag_firing_now(conn, position_id):
    """The kinds firing on the latest GOOD check, for pending()'s still?"""
    legs = _legs(conn, position_id)
    s = diag_series(legs, _diag_rows(conn, position_id, legs))
    return set(s[-1][1]) if s else set()


def _scan_diagonal(conn, pid, ticker, stamp, seed):
    """scan()'s diagonal branch. The WHOLE journal feeds the series (the long's
    peak and the breach streak need history); only the latest journal day
    emits, unless seeding."""
    legs = _legs(conn, pid)
    rows = _diag_rows(conn, pid, legs)
    if not rows:
        return []
    only = None if seed else rows[-1]["checked_at"][:10]
    open_kinds, after = _state(conn, pid)
    out = []
    for kind, r, info in diag_new_flags(legs, rows, open_kinds,
                                        after, only_day=only):
        try:
            conn.execute(
                "INSERT INTO exit_flags (position_id, kind, flag_date, "
                "fired_at, mark_at_flag, pct_at_flag, dte_at_flag, seeded, note) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, kind, r["checked_at"][:10], stamp, r.get("pnl_unrealized"),
                 r.get("pnl_pct"), info.get("dte"), 1 if seed else 0,
                 _diag_note(kind, info)))
        except Exception:
            continue  # unique index: this exact flag day is already logged
        out.append({"position_id": pid, "ticker": ticker, "kind": kind,
                    "flag_date": r["checked_at"][:10],
                    "mark_at_flag": r.get("pnl_unrealized"), "seeded": bool(seed)})
    return out
