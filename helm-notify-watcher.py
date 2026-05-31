#!/usr/bin/env python3
"""
helm-notify-watcher.py
Watched by launchd WatchPaths. Creates a Reminder in the HELM list, syncs to iPhone.
"""
import json, subprocess, os

TRIGGER = "/tmp/helm_notify_trigger.txt"
REMINDERS_LIST = "HELM"

try:
    with open(TRIGGER, "r") as f:
        payload = json.load(f)
    title   = payload.get("title", "HELM").replace('"', '').replace("'", "")
    message = payload.get("message", "").replace('"', '').replace("'", "")
    script = (
        f'tell application "Reminders"\n'
        f'  tell list "{REMINDERS_LIST}"\n'
        f'    make new reminder with properties {{name:"{title}", body:"{message}"}}\n'
        f'  end tell\n'
        f'end tell'
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        print(f"Reminder created in {REMINDERS_LIST}: {r.stdout.strip()}")
    else:
        print(f"Error: {r.stderr}")
        # Fallback to default list
        fallback = f'tell application "Reminders" to make new reminder with properties {{name:"{title}", body:"{message}"}}'
        subprocess.run(["osascript", "-e", fallback], timeout=10)
    os.remove(TRIGGER)
except Exception as e:
    print(f"HELM notify watcher error: {e}")
