"""W156 -- notice the IB gateway is down WHILE it is down.

The gap this closes: on 2026-08-24 and 08-25 the gateway was unreachable for
about 45 straight hours. Every snapshot fired on time and came back EMPTY,
and the only thing that said so was `helm audit eod` at 16:15 -- after the
day's marks were already lost. It reported Monday's loss on Monday evening
and Tuesday's on Tuesday evening. Nine of the 49 days the sampler has
recorded are degraded or blind; seven of those are trading days.

Design, and each choice is deliberate:

  * IT READS THE SAMPLER, IT DOES NOT DIAL THE BROKER. logs/mktdata_samples.csv
    is already the independent witness of whether IBKR was answering, written
    every 10 minutes around the clock. Opening a second IBKR connection to
    ask would risk the client-id collision that makes `helm restart` kill a
    running snapshot (HELM-169).
  * IT WRITES NOTHING TO THE DATABASE. State lives in a JSON file beside the
    logs. After a schema change broke `helm close` on 2026-08-26, a watchdog
    is the last thing that should be adding columns or tables.
  * IT NOTIFIES, IT NEVER ACTS. It will not restart the gateway. Same
    doctrine as HELM-143: on the real book HELM informs.
  * IT ALSO WATCHES THE WATCHMAN. `helm audit eod` names this in its own
    blind spots -- machine liveness is inferred from the sampler, and
    nothing witnesses the sampler. If the newest sample goes stale during
    RTH, that is its own alert.

Quiet rules: one alert per outage, one on recovery, nothing at all outside
a trading session -- and NOTHING WHEN THIS MACHINE IS OFFLINE.

That last rule is Russ's (2026-08-28) and it is the difference between a
useful alert and one he learns to swipe away. On a road trip there is often
no connection; IB Gateway cannot hold an IBKR login without one, so it stops
listening and every sample reads "connection refused". The gateway is
behaving correctly. Telling him to restart it would be advice he cannot act
on, delivered every travel morning. So the watchdog tests its own
connectivity first, says nothing when there is none, and remembers that it
was silent -- so the eventual reconnection is not announced as a recovery
from an outage nobody was ever told about.
"""
from __future__ import annotations

import csv
import json
import os
import socket
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CSV_PATH = os.path.join(REPO, "logs", "mktdata_samples.csv")
STATE_PATH = os.path.join(REPO, "logs", "gateway_watch_state.json")

# Three consecutive sampler runs at a 10-minute cadence: about 30 minutes
# down before Russ is told. Long enough to ride out a single failed sample,
# short enough that a lost day becomes a lost half-hour.
CONSECUTIVE = 3
STALE_MINUTES = 30
RTH_OPEN = (9, 30)

OK, OUTAGE, STALE, OFFLINE = "ok", "outage", "stale", "offline"

# Two raw IPs (no DNS needed) and one name (proves resolution works). Any one
# answering counts as "this machine has the internet".
CHECK_HOSTS = [("1.1.1.1", 53), ("8.8.8.8", 53), ("api.ibkr.com", 443)]
CHECK_TIMEOUT = 2.0


def read_runs(path=CSV_PATH, tail_bytes=400_000):
    """Sampler rows collapsed into RUNS: (timestamp, connected?).

    One run writes a row per ticker sharing a timestamp; the run counts as
    connected if ANY of its rows connected, so one thin ticker failing to
    quote is not read as the gateway being down.
    """
    if not os.path.exists(path):
        return []
    size = os.path.getsize(path)
    with open(path, "r", newline="") as fh:
        header = fh.readline().strip().split(",")
        if size > tail_bytes:
            fh.seek(size - tail_bytes)
            fh.readline()  # discard the partial line
        runs = {}
        for row in csv.reader(fh):
            if len(row) < 3:
                continue
            rec = dict(zip(header, row))
            ts = rec.get("ts") or ""
            if len(ts) < 19:
                continue
            runs[ts] = runs.get(ts, False) or (rec.get("connected") == "1")
    out = []
    for ts in sorted(runs):
        try:
            out.append((datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S"), runs[ts]))
        except ValueError:
            continue
    return out


def has_internet(hosts=None, timeout=CHECK_TIMEOUT):
    """Does this machine have ANY internet right now? Never raises.

    Why the watchdog needs to know: IB Gateway cannot hold an IBKR login
    without a connection, so on a road trip with no signal it stops
    listening and every sample reads "connection refused". That is the
    gateway behaving correctly, not failing. Telling Russ to restart it
    would be advice he cannot act on, delivered every travel morning --
    and an alert that is routinely wrong is one you learn to ignore.
    (helm/exit_alert.py makes the same argument about paper positions.)
    """
    for host, port in (hosts or CHECK_HOSTS):
        try:
            sock = socket.create_connection((host, port), timeout)
            sock.close()
            return True
        except Exception:
            continue
    return False


def in_session(now, calendar=True):
    """True only during RTH on a trading day. Holidays included, via HELM-159."""
    if now.hour < RTH_OPEN[0] or (now.hour == RTH_OPEN[0] and now.minute < RTH_OPEN[1]):
        return False, "before the open"
    if not calendar:
        return (now.weekday() < 5), "weekday check only"
    try:
        from helm.market_calendar import session_state
        st = session_state(now)
        return bool(st["run"]), st["reason"]
    except Exception as exc:  # never let the watchdog die on a calendar lookup
        return (now.weekday() < 5), "calendar unavailable (%r) - weekday only" % (exc,)


def decide(runs, now, state, sampler_age_min=None, online=True):
    """Pure. Returns {action, message, state} - action in NONE/OUTAGE/RECOVERED/SAMPLER_STALE.

    sampler_age_min: how long since the sampler last WROTE, measured from the
    file's mtime by the caller. Prefer it. Comparing the clock against a
    timestamp the sampler wrote assumes both are in the same timezone, which
    is true on Russ's Mac and false anywhere else -- a UTC caller reads a
    healthy ET sampler as four hours stale. The mtime is an epoch, so it
    carries no timezone at all.
    """
    was = (state or {}).get("state", OK)
    if not runs:
        return {"action": "NONE", "message": "no samples to read", "state": was}

    newest_ts, newest_ok = runs[-1]
    age = sampler_age_min
    if age is None:
        age = (now - newest_ts).total_seconds() / 60.0

    if age > STALE_MINUTES:
        if was == STALE:
            return {"action": "NONE", "message": "sampler still stale", "state": STALE}
        return {"action": "SAMPLER_STALE", "state": STALE,
                "message": ("the market-data sampler has written nothing for %d minutes "
                            "(newest %s). Nothing is witnessing the broker right now."
                            % (age, newest_ts.strftime("%H:%M")))}

    recent = runs[-CONSECUTIVE:]
    all_down = len(recent) == CONSECUTIVE and not any(ok for _ts, ok in recent)

    # No internet: the gateway CANNOT be up, so its being down is not news.
    # Stay silent (Russ, 2026-08-28) and remember that we did, so the
    # eventual reconnection is not announced as a recovery from an outage
    # nobody was ever told about.
    if not online:
        return {"action": "NONE", "state": OFFLINE,
                "message": "this machine is offline - not collecting, nothing to report"}

    if all_down:
        if was == OUTAGE:
            return {"action": "NONE", "message": "outage already reported", "state": OUTAGE}
        first = recent[0][0].strftime("%H:%M")
        return {"action": "OUTAGE", "state": OUTAGE,
                "message": ("IB Gateway has not answered since %s (%d consecutive samples). "
                            "Marks are not being journaled. Restart the gateway."
                            % (first, CONSECUTIVE))}

    if newest_ok and was == OFFLINE:
        return {"action": "NONE", "state": OK,
                "message": "back online; no outage was reported, so none is cleared"}

    if newest_ok and was in (OUTAGE, STALE):
        return {"action": "RECOVERED", "state": OK,
                "message": "IB Gateway is answering again as of %s."
                           % newest_ts.strftime("%H:%M")}

    return {"action": "NONE", "message": "gateway answering", "state": OK if newest_ok else was}


def sampler_age(path=CSV_PATH):
    """Minutes since the sampler last wrote, from the file mtime. None if absent."""
    try:
        return (time.time() - os.path.getmtime(path)) / 60.0
    except Exception:
        return None


def load_state(path=STATE_PATH):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {"state": OK}


def save_state(state, action, now, path=STATE_PATH):
    try:
        with open(path, "w") as fh:
            json.dump({"state": state, "last_action": action,
                       "updated_at": now.isoformat()}, fh, indent=2)
    except Exception:
        pass


def notify(title, message):
    """macOS notification. Best effort; never raises."""
    try:
        from helm.exit_alert import _notify
        _notify(title, message)
    except Exception:
        try:
            import subprocess
            subprocess.run(["osascript", "-e",
                            'display notification "%s" with title "%s"'
                            % (message.replace('"', "'"), title.replace('"', "'"))],
                           capture_output=True, timeout=10)
        except Exception:
            pass


def run(now=None, csv_path=CSV_PATH, state_path=STATE_PATH, do_notify=True, quiet=False):
    now = now or datetime.now()
    open_now, why = in_session(now)
    if not open_now:
        if not quiet:
            print("[gwwatch] standing down: %s" % why)
        return {"action": "NONE", "message": "outside RTH: %s" % why, "state": None}

    runs = read_runs(csv_path)
    state = load_state(state_path)
    verdict = decide(runs, now, state, sampler_age_min=sampler_age(csv_path),
                     online=has_internet())
    save_state(verdict["state"], verdict["action"], now, state_path)

    if verdict["action"] != "NONE":
        title = {"OUTAGE": "HELM: broker is down",
                 "RECOVERED": "HELM: broker is back",
                 "SAMPLER_STALE": "HELM: nothing is watching the broker"}[verdict["action"]]
        if do_notify:
            notify(title, verdict["message"])
        print("[gwwatch] %s -- %s" % (verdict["action"], verdict["message"]))
    elif not quiet:
        print("[gwwatch] ok -- %s" % verdict["message"])
    return verdict


if __name__ == "__main__":
    import sys
    run(do_notify="--no-notify" not in sys.argv)
