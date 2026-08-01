"""W91 / HELM-143 -- exit-improvement push alert. NOTIFY ONLY, NEVER ACTS.

Runs as a post-pass after the scheduled snapshot has journaled its checks
(check_cmd.cmd_snapshot). For each OPEN REAL-book position whose strike
belief is confirmed-broken (helm.thesis states, slice-1 calibrated), it asks
one question: is today's best journaled exit meaningfully better than what
the market offered on the recent check days? If yes, it records the alert
(exit_alerts table) and posts a macOS notification pointing at the thesis
card. PAPER positions are excluded deliberately -- their exits are the
15:55 agent's job, and a noisy alert trains itself to be ignored.

Doctrine: facts gate, judgments display. This module gates nothing and
closes nothing.

Quiet rules:
- at most one alert per (position, day) -- unique index;
- fires only when today's best beats the best of the last LOOKBACK_DAYS
  check days by >= max(MIN_IMPROVE_ABS, MIN_IMPROVE_FRAC * |max_loss|)
  (max_loss from the positions row when defined; the dollar floor alone
  when not);
- fires only when today's best also beats every previously alerted offer
  for the position -- the same offer level never alerts twice.
"""
from datetime import datetime

MIN_IMPROVE_ABS = 250.0
MIN_IMPROVE_FRAC = 0.05
LOOKBACK_DAYS = 5

DDL = """CREATE TABLE IF NOT EXISTS exit_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL,
    alert_date TEXT NOT NULL,
    fired_at TEXT NOT NULL,
    today_best REAL,
    best_prior REAL,
    threshold REAL,
    message TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exit_alerts_pos_day
    ON exit_alerts(position_id, alert_date);"""


def decide(prior_day_best, today_best, max_loss=None):
    """Pure. prior_day_best: [(date, best_mark)] ascending, today excluded.
    Returns dict or None."""
    prior = [b for _d, b in prior_day_best[-LOOKBACK_DAYS:] if b is not None]
    if not prior or today_best is None:
        return None
    best_prior = max(prior)
    threshold = MIN_IMPROVE_ABS
    if max_loss:
        try:
            threshold = max(threshold, abs(float(max_loss)) * MIN_IMPROVE_FRAC)
        except (TypeError, ValueError):
            pass
    improve = today_best - best_prior
    if improve >= threshold:
        return {"today_best": today_best, "best_prior": best_prior,
                "improve": improve, "threshold": threshold}
    return None


def _daily_best(checks):
    byday = {}
    for r in checks:
        d = (r.get("checked_at") or "")[:10]
        p = r.get("pnl_unrealized")
        if not d or p is None:
            continue
        p = float(p)
        if d not in byday or p > byday[d]:
            byday[d] = p
    return [(d, byday[d]) for d in sorted(byday)]


def _notify(title, msg):
    """macOS notification. Best effort; never raises."""
    try:
        import subprocess
        subprocess.run(
            ["osascript", "-e",
             'display notification "%s" with title "%s"'
             % (msg.replace('"', "'"), title.replace('"', "'"))],
            capture_output=True, timeout=10)
    except Exception:
        pass


def run_post_snapshot(conn=None, notify=True, today=None):
    """The post-pass. Reads what the snapshot just journaled; writes only to
    exit_alerts. Returns the list of alerts fired (for tests)."""
    from helm import thesis as T
    own_conn = conn is None
    if own_conn:
        import sqlite3
        from helm.config import DB_PATH
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
    fired = []
    try:
        conn.executescript(DDL)
        today = today or datetime.now().date().isoformat()
        positions = [dict(r) for r in conn.execute(
            "SELECT * FROM positions WHERE status='OPEN' AND book='REAL'")]
        for pos in positions:
            legs = [dict(r) for r in conn.execute(
                "SELECT * FROM legs WHERE position_id=?", (pos["id"],))]
            checks = [dict(r) for r in conn.execute(
                "SELECT checked_at, spot_price, pnl_unrealized FROM checks "
                "WHERE position_id=? AND data_quality='GOOD' "
                "ORDER BY checked_at", (pos["id"],))]
            series = T.day_series(legs, checks)
            state, _streak, _w = T._strike_state(series)
            if state not in (T.BROKEN, T.BROKEN_LOUD):
                continue
            daily = _daily_best(checks)
            if not daily or daily[-1][0] != today:
                continue  # no journaled mark today -- nothing honest to say
            today_best = daily[-1][1]
            verdict = decide(daily[:-1], today_best, pos.get("max_loss"))
            if not verdict:
                continue
            prev = conn.execute(
                "SELECT MAX(today_best) FROM exit_alerts WHERE position_id=?",
                (pos["id"],)).fetchone()[0]
            if prev is not None and today_best <= prev:
                continue  # same or worse offer than one already alerted
            msg = ("{t}: today's exit {a:+,.0f} beats the best of the last "
                   "{n} check days ({b:+,.0f}) -- the thesis card has the "
                   "ledger").format(t=pos.get("ticker"), a=today_best,
                                    n=LOOKBACK_DAYS, b=verdict["best_prior"])
            try:
                conn.execute(
                    "INSERT INTO exit_alerts (position_id, alert_date, "
                    "fired_at, today_best, best_prior, threshold, message) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (pos["id"], today, datetime.now().isoformat(),
                     today_best, verdict["best_prior"], verdict["threshold"],
                     msg))
                conn.commit()
            except Exception:
                continue  # unique-index: already alerted today
            fired.append({"position_id": pos["id"], "message": msg,
                          **verdict})
            if notify:
                _notify("HELM exit alert", msg)
    finally:
        if own_conn:
            conn.close()
    return fired
