#!/usr/bin/env python3
"""
helm-notify-watcher.py
Watched by launchd WatchPaths.
Sends iMessage via iMessage service to +12022558209. Falls back to HELM Reminders.
"""
import json, subprocess, os

TRIGGER = "/tmp/helm_notify_trigger.txt"
PHONE = "+12022558209"
REMINDERS_LIST = "HELM"
TIMEOUT = 30

try:
    with open(TRIGGER, "r") as f:
        payload = json.load(f)
    title   = payload.get("title", "HELM").replace('"', '').replace("'", "")
    message = payload.get("message", "").replace('"', '').replace("'", "")
    full_msg = f"{title}\n{message}" if message else title

    # Send via iMessage service explicitly (proven working)
    msg_script = (
        f'tell application "Messages"\n'
        f'  set targetService to 1st service whose service type = iMessage\n'
        f'  set targetBuddy to buddy "{PHONE}" of targetService\n'
        f'  send "{full_msg}" to targetBuddy\n'
        f'end tell'
    )
    r1 = subprocess.run(["osascript", "-e", msg_script], capture_output=True, text=True, timeout=TIMEOUT)
    if r1.returncode == 0:
        print(f"iMessage sent to {PHONE}")
    else:
        print(f"iMessage failed: {r1.stderr.strip()}")
        # Fallback: Reminders
        rem_script = (
            f'tell application "Reminders"\n'
            f'  tell list "{REMINDERS_LIST}"\n'
            f'    make new reminder with properties {{name:"{title}", body:"{message}"}}\n'
            f'  end tell\n'
            f'end tell'
        )
        subprocess.run(["osascript", "-e", rem_script], timeout=TIMEOUT)
        print("Fallback: Reminder created in HELM list")

    os.remove(TRIGGER)
except Exception as e:
    print(f"HELM notify watcher error: {e}")
