#!/usr/bin/env python3
"""
helm-notify-watcher.py
Watched by launchd WatchPaths. Creates a Reminder that syncs to iPhone.
"""
import json, subprocess, os

TRIGGER = "/tmp/helm_notify_trigger.txt"

try:
    with open(TRIGGER, "r") as f:
        payload = json.load(f)
    title   = payload.get("title", "HELM").replace('"', '').replace("'", "")
    message = payload.get("message", "").replace('"', '').replace("'", "")
    # Create reminder via AppleScript - syncs to iPhone via iCloud
    script = f'tell application "Reminders" to make new reminder with properties {{name:"{title}", body:"{message}"}}'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        print(f"Reminder created: {r.stdout.strip()}")
    else:
        print(f"Error: {r.stderr}")
    os.remove(TRIGGER)
except Exception as e:
    print(f"HELM notify watcher error: {e}")
