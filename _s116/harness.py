"""s116 verification: run check_one(persist=True) against a FRESH copy of the
live DB, with every quote source faked, on one scenario. Prints JSON.

Usage: python3 harness.py <version_dir> <case>
Each run is its own process: no import state, and no stale-.pyc ambiguity
between the two trees (the VM cannot delete __pycache__ -- s115's lesson).
"""
import os, sys, json, sqlite3, shutil

VDIR, CASE = sys.argv[1], sys.argv[2]
import uuid as _u
DB = f"/tmp/case_{_u.uuid4().hex[:8]}.db"
LIVE = "/tmp/helm_s116.db"          # the read-only VACUUM copy taken at 12:00

# fresh copy per case -- a check on a copy must start from a fresh copy (s109)
src = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
src.execute("VACUUM INTO ?", (DB,)); src.close()

os.environ["HELM_ROOT"] = VDIR
os.environ["HELM_DB"] = DB
sys.path.insert(0, VDIR)

AA  = "AA-DIAGONAL-20260901-219869"
EQT = "EQT-DIAGONAL-20260902-06CD6A"
BX  = "BX-DIAGONAL-20260729-7A1722"

con = sqlite3.connect(DB)
if CASE == "aa_short_expired":
    # AA's short leg as it will be from 2026-10-16: expired and settled.
    # Its legs are stored SHORT-first, so the short leg is the primary.
    con.execute("UPDATE legs SET expiration='2026-09-04', status='CLOSED', "
                "close_price=3.00, close_date='2026-09-04T16:00:00' "
                "WHERE position_id=? AND direction='SHORT'", (AA,))
    con.commit()
PID = {"bx_settled": BX, "aa_short_expired": AA,
       "live_control": EQT, "unquotable_leg": EQT}[CASE]
con.close()

import helm.cli.check_cmd as cc
import helm.expiry as ex

probe = {"check_cmd_file": cc.__file__,
         "has_settled_mark": hasattr(ex, "settled_mark"),
         "persist_writes_dq": "_dq, _now)" in open(
             os.path.join(VDIR, "helm/cli/check_cmd.py")).read()}

# ---- fake every quote source. Deterministic, offline, and the same in both
# versions, so any difference in the result is the code, not the tape.
QUOTES = {   # (expiration, strike) -> mid/greeks
    ("2026-11-20", 120.0): {"mid": 20.27, "delta": 0.812, "gamma": 0.01, "theta": -0.05, "vega": 0.3, "iv": 0.31, "bid": 20.1, "ask": 20.45},
    ("2026-12-18", 47.0):  {"mid": 7.50,  "delta": 0.700, "gamma": 0.02, "theta": -0.03, "vega": 0.2, "iv": 0.34, "bid": 7.4,  "ask": 7.6},
    ("2026-10-16", 57.5):  {"mid": 1.32,  "delta": 0.355, "gamma": 0.03, "theta": -0.04, "vega": 0.1, "iv": 0.33, "bid": 1.28, "ask": 1.36},
    ("2026-12-18", 52.5):  {"mid": 5.55,  "delta": 0.640, "gamma": 0.02, "theta": -0.02, "vega": 0.2, "iv": 0.33, "bid": 5.5,  "ask": 5.6},
}
if CASE == "unquotable_leg":
    QUOTES.pop(("2026-12-18", 52.5))          # the LONG leg stops quoting

SPOT = {"BX": 136.58, "AA": 49.96, "EQT": 54.85}

cc.fetch_ibkr_underlying = lambda tk: {"price": SPOT.get(tk, 50.0), "source": "ibkr", "live": True, "error": None}
cc.fetch_ibkr_option = lambda tk, exp, strike, ot: dict(QUOTES.get((str(exp)[:10], float(strike)), {}))
cc.fetch_yf_data = lambda tk, exp, strike, ot: {}
cc.is_market_open = lambda: True
# BX's short leg settled at 8.28 (helm settle, last GOOD expiry-day mark);
# yfinance's official close for 2026-08-28 was 142.39 -> intrinsic 7.39.
ex.expiry_close = lambda tk, exp: 142.39

from helm.db import get_conn
c = get_conn(); c.row_factory = sqlite3.Row
pos = dict(c.execute("SELECT * FROM positions WHERE id=?", (PID,)).fetchone())
legs = [dict(r) for r in c.execute("SELECT * FROM legs WHERE position_id=?", (PID,))]
c.close()

before = sqlite3.connect(DB)
n_chk0 = before.execute("SELECT COUNT(*) FROM checks WHERE position_id=?", (PID,)).fetchone()[0]
n_leg0 = before.execute("SELECT COUNT(*) FROM leg_checks WHERE position_id=?", (PID,)).fetchone()[0]
before.close()

err = None
try:
    a = cc.check_one(pos, legs, persist=True)
except Exception as e:
    import traceback; err = f"{type(e).__name__}: {e}"; traceback.print_exc(file=sys.stderr)
    a = {}

out = sqlite3.connect(DB); out.row_factory = sqlite3.Row
chk = out.execute("SELECT * FROM checks WHERE position_id=? ORDER BY created_at DESC LIMIT 1", (PID,)).fetchone()
n_chk1 = out.execute("SELECT COUNT(*) FROM checks WHERE position_id=?", (PID,)).fetchone()[0]
newlegs = [dict(r) for r in out.execute(
    "SELECT lc.leg_id, lc.current_price, lc.data_quality, l.direction, l.status "
    "FROM leg_checks lc JOIN legs l ON l.id=lc.leg_id WHERE lc.position_id=? "
    "AND lc.check_id=(SELECT id FROM checks WHERE position_id=? ORDER BY created_at DESC LIMIT 1)",
    (PID, PID))]
n_leg1 = out.execute("SELECT COUNT(*) FROM leg_checks WHERE position_id=?", (PID,)).fetchone()[0]
out.close()
os.remove(DB)

print(json.dumps({
    "case": CASE, "probe": probe, "error": err,
    "primary_direction": (a.get("primary_leg") or {}).get("direction"),
    "primary_expiration": (a.get("primary_leg") or {}).get("expiration"),
    "assessment_pnl": a.get("pnl_mtm"),
    "check_persisted": n_chk1 - n_chk0,
    "stored_dte_now": chk["dte_now"] if (chk and n_chk1 > n_chk0) else None,
    "stored_pnl": chk["pnl_unrealized"] if (chk and n_chk1 > n_chk0) else None,
    "stored_dq": chk["data_quality"] if (chk and n_chk1 > n_chk0) else None,
    "leg_rows_written": n_leg1 - n_leg0,
    "leg_rows": sorted(newlegs, key=lambda r: r["direction"]),
}, indent=1))
