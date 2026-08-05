"""W21 (s90): make the scheduled agents' runs and their gaps legible.

Two separate facts live here, and keeping them separate matters:

  * THE JOURNAL is what the exit doctrine actually depends on. THESIS_BREAK
    counts consecutive GOOD journal days, so a slot that never ran does not
    merely lose a data point -- it stalls a verdict. journal_health() therefore
    measures the CHECKS TABLE, not the agent, because the table is the thing
    the rule reads. It works on history that already exists.

  * THE RUN LEDGER records what the agent did each time it woke: how many
    positions it attempted, how many rows actually reached the journal, and how
    many verdicts failed. That last number was previously logged and discarded.
    It starts empty and accumulates from here.

Deliberately NOT here: any backfill. A catch-up run for a missed 15:45 would
write marks at stale or closed prices, and the streak only counts GOOD marks,
so a backfilled bad row is worse than an absent one. Settled by Russ in s90:
make the gap visible and let him decide.

Holidays are not modelled. Trading days are assumed Mon-Fri, so a market
holiday reads as three missed slots. Said out loud rather than silently
smoothed, because a rule that hides real gaps to avoid false ones is the wrong
trade for a counter that gates an exit.
"""

from datetime import datetime, timedelta, date

AGENT_SNAPSHOT = 'com.helm.snapshot.daily'
AGENT_EXITS    = 'com.helm.paper.exits'
AGENT_IVR      = 'com.helm.ivr.refresh'

# The schedule com.helm.snapshot.daily actually carries (15 explicit entries:
# weekdays 1-5 x three slots). Mirrored here rather than parsed from the plist,
# because the plist is not tracked in git (W42) and this module must work on a
# machine where it is absent.
SLOTS = ((10, 0), (12, 30), (15, 45))

# How late a run may be and still count as its slot. 14 July's 15:45 ran at
# 15:58 and would otherwise read as a miss plus an unexplained ad-hoc run.
SLOT_GRACE_MIN = 45

DDL = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    slot        TEXT,
    attempted   INTEGER,
    journaled   INTEGER,
    failed      INTEGER,
    status      TEXT NOT NULL DEFAULT 'OK',
    notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_started
    ON agent_runs(agent, started_at);
"""


def ensure_table(conn):
    """Create the ledger if absent. Never raises -- a missing ledger must not
    stop a snapshot from doing its actual job."""
    try:
        conn.executescript(DDL)
        return True
    except Exception:
        return False


def slot_for(dt):
    """The scheduled slot this moment belongs to, or None for an ad-hoc run.

    A run is attributed to a slot when it starts at or after the slot time and
    within SLOT_GRACE_MIN of it. Runs before the first slot, between slots, or
    at a weekend are ad-hoc -- a manual helm snapshot should never be counted
    as the scheduled one, or a missed slot would be silently papered over by
    somebody running it by hand.
    """
    if dt.weekday() >= 5:
        return None
    for h, m in SLOTS:
        base = dt.replace(hour=h, minute=m, second=0, microsecond=0)
        if base <= dt < base + timedelta(minutes=SLOT_GRACE_MIN):
            return '%02d:%02d' % (h, m)
    return None


def record_run(conn, agent, started_at, finished_at, attempted, journaled,
               failed, notes=None):
    """Append one run to the ledger. Returns True on success.

    status is derived, not passed: a run that journaled nothing, or that lost
    positions to failures, is not OK however cleanly the process exited. This
    is the W35 distinction -- "the command exited 0" and "the work happened"
    are different claims -- applied to the writer rather than the restarter.
    """
    if journaled == 0 and (attempted or 0) > 0:
        status = 'EMPTY'
    elif failed:
        status = 'PARTIAL'
    elif (attempted or 0) > (journaled or 0):
        # s100: a slot can journal fewer rows than it attempted with no
        # verdict failing at all -- save_check drops a reading whose quote
        # was not live (HELM-037). That drop is deliberate; reporting it as
        # OK is not. 2026-07-29 lost 59 readings this way and recorded a
        # clean day. attempted vs journaled was already in scope, unused.
        status = 'SHORTFALL'
    else:
        status = 'OK'
    try:
        conn.execute(
            'INSERT INTO agent_runs (agent, started_at, finished_at, slot, '
            'attempted, journaled, failed, status, notes) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (agent, started_at, finished_at,
             slot_for(datetime.fromisoformat(started_at)),
             attempted, journaled, failed, status, notes))
        conn.commit()
        return True
    except Exception:
        return False


def last_run(conn, agent=AGENT_SNAPSHOT):
    """The most recent ledger row for an agent, or None."""
    try:
        r = conn.execute(
            'SELECT started_at, finished_at, slot, attempted, journaled, '
            'failed, status FROM agent_runs WHERE agent = ? '
            'ORDER BY started_at DESC LIMIT 1', (agent,)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    return {'started_at': r[0], 'finished_at': r[1], 'slot': r[2],
            'attempted': r[3], 'journaled': r[4], 'failed': r[5],
            'status': r[6]}


def journal_runs(conn, since):
    """Reconstruct journal write-events from the checks table.

    A "run" is a cluster of checked_at values with no gap longer than ten
    minutes. The checks table is written by the scheduled snapshot AND by
    ad-hoc helm check, so this returns every write event; slot attribution
    below is what separates the scheduled ones.

    Returns [(datetime, row_count)] oldest first.
    """
    try:
        rows = [r[0] for r in conn.execute(
            'SELECT checked_at FROM checks WHERE checked_at >= ? '
            'ORDER BY checked_at', (since,))]
    except Exception:
        return []
    out = []
    last = None
    for ts in rows:
        try:
            dt = datetime.fromisoformat(str(ts))
        except Exception:
            continue
        if last is None or (dt - last).total_seconds() > 600:
            out.append([dt, 0])
        out[-1][1] += 1
        last = dt
    return [(d, n) for d, n in out]


def expected_slots(start_day, now=None):
    """Every scheduled slot from start_day up to now, as (date, 'HH:MM').

    Only slots whose time has actually passed count -- today's 15:45 is not a
    miss at noon. Weekends excluded; holidays are not (see module docstring).
    """
    now = now or datetime.now()
    out = []
    d = start_day
    while d <= now.date():
        if d.weekday() < 5:
            for h, m in SLOTS:
                when = datetime.combine(d, datetime.min.time()).replace(hour=h, minute=m)
                if when + timedelta(minutes=SLOT_GRACE_MIN) <= now:
                    out.append((d, '%02d:%02d' % (h, m)))
        d += timedelta(days=1)
    return out


def journal_health(conn, days=7, now=None):
    """What the exit doctrine's input actually looks like right now.

    Returns a dict:
      last_write      ISO timestamp of the newest journal row, or None
      age_hours       hours since that write, or None
      slots_today     how many of today's due slots produced a write
      slots_due_today how many were due by now
      missed          [(date, 'HH:MM')] scheduled slots with no write, newest first
      window_days     how far back missed was computed

    Reads the CHECKS table, not the ledger, so it is true for history that
    predates the ledger existing.
    """
    now = now or datetime.now()
    start = (now - timedelta(days=days)).date()
    runs = journal_runs(conn, start.isoformat())

    hit = set()
    for dt, _n in runs:
        s = slot_for(dt)
        if s:
            hit.add((dt.date(), s))

    due = expected_slots(start, now)
    missed = [x for x in due if x not in hit]
    missed.sort(reverse=True)

    today_due = [x for x in due if x[0] == now.date()]
    today_hit = [x for x in today_due if x in hit]

    last_write = None
    age_hours = None
    try:
        r = conn.execute('SELECT MAX(checked_at) FROM checks').fetchone()
        if r and r[0]:
            last_write = str(r[0])
            age_hours = round(
                (now - datetime.fromisoformat(last_write)).total_seconds() / 3600.0, 1)
    except Exception:
        pass

    return {'last_write': last_write, 'age_hours': age_hours,
            'slots_today': len(today_hit), 'slots_due_today': len(today_due),
            'missed': missed, 'window_days': days,
            'runs': len(runs)}


def health_line(h):
    """One rich-markup line for helm check, or '' when there is nothing to say.

    Follows the W56 convention exactly: silent when healthy, dim when notable,
    yellow when it should change a decision, and it says what it does not know
    out loud rather than leaving a blank -- a blank reads as fine.

    The threshold is deliberately tied to the doctrine rather than to taste.
    CONFIRM_DAYS is 2, so two consecutive missed journal days is the point at
    which a thesis break cannot confirm; anything at or beyond that is yellow.
    """
    if h.get('last_write') is None:
        return '[yellow]journal: no marks ever recorded[/yellow]'
    age = h.get('age_hours')
    miss = h.get('missed') or []
    today = '%d of %d slots today' % (h.get('slots_today', 0),
                                      h.get('slots_due_today', 0))
    recent = [m for m in miss[:6]]
    tail = ''
    if recent:
        tail = ' · missed ' + ', '.join('%s %s' % (d.strftime('%m-%d'), s)
                                        for d, s in recent[:3])
        if len(miss) > 3:
            tail += ' (+%d)' % (len(miss) - 3)

    if age is not None and age >= 30:
        return ('[yellow]journal: last mark %.0fh ago — the thesis-break streak '
                'cannot advance[/yellow][dim]%s[/dim]' % (age, tail))
    if h.get('slots_due_today', 0) and h.get('slots_today', 0) == 0:
        return ('[yellow]journal: no marks today (%s)[/yellow][dim]%s[/dim]'
                % (today, tail))
    if miss or (age is not None and age >= 20):
        return ('[dim]journal: last mark %.0fh ago · %s%s[/dim]'
                % (age or 0, today, tail))
    return ''
