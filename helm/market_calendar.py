"""helm/market_calendar.py -- trading-calendar gate for the scheduled agents.

W110 / s102 (2026-08-09).

WHY THIS EXISTS
---------------
launchd understands weekdays; it knows nothing about market holidays, so every
scheduled agent fired on any Monday-Friday whether or not the exchange was
open. Measured before this shipped:

  * 2026-06-19 (Juneteenth)  -- the snapshot journalled 84 position marks.
  * 2026-07-03 (Jul 4 observed) -- the paper exit agent CLOSED SEVEN POSITIONS,
    all DTE_MANAGE, at 10:01-10:06 and 15:46, against a shut exchange.

Half-days matter for the same reason: on a 13:00 close the 15:15 snapshot and
the 15:35 exit run both fire hours after the bell.

SOURCE OF TRUTH
---------------
exchange_calendars, calendar XNYS. Verified 2026-08-09 against Juneteenth,
3 July, Labor Day, Thanksgiving, Christmas and both 1pm half-days (27 Nov and
24 Dec 2026).

FAIL-OPEN, DELIBERATELY
-----------------------
If the calendar cannot answer, the agents RUN. A whole trading day missing from
the corpus costs more than one spurious holiday row -- 2026-08-06 proved that,
when a gateway outage cost 227 readings. A fail-closed bug here would stop the
entire system while every status read green, which is the exact shape HELM-154
exists to prevent.

Every fallback is printed and carried into the run note. None is silent: a
silent except inside a guard against silent failure is the joke writing itself
(s100).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

STATUS_SKIPPED = "SKIPPED_CLOSED"

AGENT_SNAPSHOT = "com.helm.snapshot.daily"
AGENT_EXITS = "com.helm.paper.exits"
AGENT_IVR = "com.helm.ivr.refresh"

_CAL = None
_CAL_TRIED = False


def _calendar():
    """The XNYS calendar, built once per process. None if unavailable."""
    global _CAL, _CAL_TRIED
    if _CAL_TRIED:
        return _CAL
    _CAL_TRIED = True
    try:
        import exchange_calendars as xc
        _CAL = xc.get_calendar("XNYS")
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        print("[market_calendar] WARNING: calendar unavailable (%r); "
              "failing OPEN -- agents will run." % (e,))
        _CAL = None
    return _CAL


def _as_et(now=None):
    if now is None:
        return datetime.now(ET)
    if now.tzinfo is None:
        return now.replace(tzinfo=ET)
    return now.astimezone(ET)


def session_state(now=None) -> dict:
    """Describe the session for `now` (naive datetimes are read as ET).

    Returns {run, reason, degraded, close}. `degraded` marks a fail-open."""
    now = _as_et(now)
    cal = _calendar()
    if cal is None:
        return {"run": True, "degraded": True, "close": None,
                "reason": "trading calendar unavailable - failing open"}
    try:
        import pandas as pd
        ts = pd.Timestamp(now.date())
        if not cal.is_session(ts):
            return {"run": False, "degraded": False, "close": None,
                    "reason": "%s (%s) is not a trading session"
                              % (now.date().isoformat(), now.strftime("%a"))}
        close = cal.session_close(ts).tz_convert(ET)
        if now > close:
            return {"run": False, "degraded": False, "close": close,
                    "reason": "after the %s close on %s"
                              % (close.strftime("%H:%M"), now.date().isoformat())}
        return {"run": True, "degraded": False, "close": close,
                "reason": "session open until %s" % close.strftime("%H:%M")}
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        print("[market_calendar] WARNING: calendar lookup failed (%r); "
              "failing OPEN." % (e,))
        return {"run": True, "degraded": True, "close": None,
                "reason": "calendar lookup failed (%r) - failing open" % (e,)}


def agent_should_run(now=None):
    """(bool, reason). False means the exchange is shut -- stand down."""
    st = session_state(now)
    return st["run"], st["reason"]


def stand_down(agent, started_at, reason, slot=None):
    """Record a deliberate stand-down in the run ledger.

    An agent that simply returns leaves a hole indistinguishable from a crash.
    This writes SKIPPED_CLOSED with the reason named, so W96's end-of-day audit
    can tell 'correctly did nothing' from 'failed to run'."""
    finished = datetime.now().isoformat()
    try:
        from helm.db import get_conn
        conn = get_conn()
        conn.execute(
            "INSERT INTO agent_runs (agent, started_at, finished_at, slot, "
            "attempted, journaled, failed, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (agent, started_at, finished, slot, 0, 0, 0, STATUS_SKIPPED, reason))
        conn.commit()
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        print("[market_calendar] WARNING: could not record stand-down: %r" % (e,))
