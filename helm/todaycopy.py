"""s160 (Russ, 2026-11-17): ask HELM's board to save a fixed copy of its Today page.

The board does the saving (helm-pg/todaycopy.py) so the copy is the page Russ sees, not a second layout. This
side only asks: POST <board>/api/today/save. Used by the snapshot after advice.json and by
`helm today-copy`. Never raises; a board that is down or slow costs nothing but a "not written" line.
Files: <HELM>/Claude outputs/today.html, today.txt, and today/YYYY-MM-DD-HHMM.* kept 14 days.
"""
import json
import os
import urllib.error
import urllib.request

BOARD_URL = "http://127.0.0.1:8770"
TIMEOUT_S = 30


def request_save(url=None, timeout=TIMEOUT_S):
    url = (url or os.environ.get("HELM_PG_URL") or BOARD_URL).rstrip("/") + "/api/today/save"
    try:
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "reason": "the board answered %s" % e.code}
    except Exception as e:        # noqa: BLE001
        return {"ok": False, "reason": "board not answering (%s)" % (getattr(e, "reason", None) or type(e).__name__)}


def line(r):
    if r.get("ok"):
        rm = r.get("removed") or 0
        return "today copy: saved %s%s" % (r.get("saved_at"), (" (%d old copies removed)" % rm) if rm else "")
    return "today copy: not written -- %s" % r.get("reason")


def run():
    print(line(request_save()))
