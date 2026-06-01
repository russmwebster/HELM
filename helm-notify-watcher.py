#!/usr/bin/env python3
"""
helm-notify-watcher.py
Watched by launchd WatchPaths.
Sends iMessage to contact via Messages app. Falls back to Reminders.
"""
import json, subprocess, os

TRIGGER = "/tmp/helm_notify_trigger.txt"
CONTACT = "Russ Webster"
REMINDERS_LIST = "HELM"

try:
    with open(TRIGGER, "r") as f:
        payload = json.load(f)
    title   = payload.get("title", "HELM").replace('"', '').replace("'", "")
    message = payload.get("message", "").replace('"', '').replace("'", "")
    full_msg = f"{title} | {message}" if message else title

    # 1. Send iMessage via Messages app to contact name
    msg_script = f'tell application "Messages" to send "{full_msg}" to buddy "{CONTACT}"'
    r1 = subprocess.run(["osascript", "-e", msg_script], capture_output=True, text=True, timeout=10)
    if r1.returncode == 0:
        print(f"iMessage sent to {CONTACT}")
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
        subprocess.run(["osascript", "-e", rem_script], timeout=10)
        print("Fallback: Reminder created in HELM list")

    os.remove(TRIGGER)
except Exception as e:
    print(f"HELM notify watcher error: {e}")
