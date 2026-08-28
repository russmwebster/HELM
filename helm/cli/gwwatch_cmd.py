"""helm gwwatch -- is the broker answering right now?

    helm gwwatch              # check, and notify if something is wrong
    helm gwwatch --no-notify  # check quietly (what the tests use)
    helm gwwatch --status     # just print the state, alert nothing

Runs every 10 minutes as com.helm.gwwatch. Reads the market-data sampler,
never the broker itself, and writes nothing to the database.
"""
import sys

from helm import gateway_watch as G


def run():
    # helm.py discards a command's return value (HELM-207), so exit explicitly.
    if "--status" in sys.argv:
        runs = G.read_runs()
        state = G.load_state()
        if not runs:
            print("no sampler data")
            sys.exit(1)
        ts, ok = runs[-1]
        print("last sample %s  gateway %s  watchdog state %s"
              % (ts.strftime("%Y-%m-%d %H:%M"), "UP" if ok else "DOWN",
                 state.get("state", "ok")))
        sys.exit(0)
    verdict = G.run(do_notify="--no-notify" not in sys.argv)
    sys.exit(1 if verdict["action"] in ("OUTAGE", "SAMPLER_STALE") else 0)
