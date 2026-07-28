#!/usr/bin/env python3
"""HELM-138 / W74 verification (s93). Behavioural checks for:
  - helm.expiry settlement math (DB-free, network stubbed via cache seed)
  - check_cmd: expired legs marked from settlement, fetchers never called
  - save_check: no GOOD row without a mark (runs against a SNAPSHOT db)
  - paper_exit_agent.leg_mid: expired leg returns settlement, chain untouched
Run on the Mac:  python3 tools/verify_s93_w74.py
Never touches the live database: DB work uses VACUUM INTO a temp snapshot.
"""
import os, sys, sqlite3, tempfile, types
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok  {name}")
    else:    FAIL += 1; print(f"FAIL  {name}")

# ── snapshot db first, and point HELM_DB at it BEFORE helm imports ───────────
tmp = tempfile.mkdtemp(prefix="helm_s93_")
snap = os.path.join(tmp, "snap.db")
live = str(ROOT / "data" / "helm.db")
src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
src.execute("VACUUM INTO ?", (snap,))
src.close()
os.environ["HELM_DB"] = snap
check("isolation: HELM_DB points at snapshot, not live",
      os.environ["HELM_DB"] != live and os.path.exists(snap))

# ── 1 · helm.expiry math ─────────────────────────────────────────────────────
import helm.expiry as ex
Y = (date.today() - timedelta(days=4)).isoformat()   # safely expired date
ex._close_cache[("TEST", Y)] = 53.03
check("OTM call settles 0.00",      ex.settlement_intrinsic("TEST", "CALL", 55.0, Y) == 0.0)
check("ITM call settles S-K",       ex.settlement_intrinsic("TEST", "CALL", 50.0, Y) == 3.03)
check("ITM put settles K-S",        ex.settlement_intrinsic("TEST", "PUT", 55.0, Y) == 1.97)
check("OTM put settles 0.00",       ex.settlement_intrinsic("TEST", "PUT", 50.0, Y) == 0.0)
TODAY = date.today().isoformat()
check("expiry day itself: None (still quoting)",
      ex.settlement_intrinsic("TEST", "CALL", 55.0, TODAY) is None)
FUT = (date.today() + timedelta(days=30)).isoformat()
check("live contract: None",        ex.settlement_intrinsic("TEST", "CALL", 55.0, FUT) is None)
ex._close_cache[("NOPX", Y)] = None
check("unknown close: None (never invent)",
      ex.settlement_intrinsic("NOPX", "CALL", 55.0, Y) is None)
check("bad expiration string: None", ex.settlement_intrinsic("TEST", "CALL", 55.0, "garbage") is None)

# ── 2 · check_cmd netting loop: expired leg settled, fetchers untouched ──────
import helm.cli.check_cmd as cc

def _boom(*a, **k):
    raise AssertionError("quote fetcher called for expired contract")

# Drive the real check_one against the snapshot for the EQT diagonal, with
# IBKR/yf leg fetchers instrumented: they may serve the LIVE leg, but must
# never be asked for the EXPIRED one.
con = sqlite3.connect(snap); con.row_factory = sqlite3.Row
pos = dict(con.execute("SELECT * FROM positions WHERE id='EQT-DIAGONAL-20260626-E8F6A0'").fetchone())
legs = [dict(r) for r in con.execute("SELECT * FROM legs WHERE position_id=?", (pos["id"],))]
con.close()
exp_leg = [l for l in legs if l["expiration"] < date.today().isoformat()][0]
live_leg = [l for l in legs if l["expiration"] >= date.today().isoformat()][0]
check("fixture: EQT diagonal has exactly one expired leg",
      sum(1 for l in legs if l["expiration"] < date.today().isoformat()) == 1)

calls = []
_orig_ibkr, _orig_yf = cc.fetch_ibkr_option, cc.fetch_yf_data
def spy_ibkr(tkr, expn, strike, ot):
    calls.append(("ibkr", expn, strike))
    if expn == exp_leg["expiration"]:
        raise AssertionError("IBKR asked for expired contract")
    return {"mid": 3.10, "bid": 3.0, "ask": 3.2, "delta": 0.6}
def spy_yf(tkr, expn, strike, ot):
    calls.append(("yf", expn, strike))
    if expn == exp_leg["expiration"]:
        raise AssertionError("yfinance asked for expired contract")
    return {"mid": 3.10, "bid": 3.0, "ask": 3.2}
cc.fetch_ibkr_option, cc.fetch_yf_data = spy_ibkr, spy_yf
ex._close_cache[("EQT", exp_leg["expiration"])] = 53.03   # real 7/24 close

try:
    a = cc.check_one(pos, legs, persist=False)
finally:
    cc.fetch_ibkr_option, cc.fetch_yf_data = _orig_ibkr, _orig_yf

check("check_one returns an assessment", isinstance(a, dict))
check("pnl_mtm computed (was NULL for 6 slots)", a.get("pnl_mtm") is not None)
# short leg settled 0: pnl = (0.92-0)*100 + (3.10-5.55)*100 = 92 - 245 = -153
check("pnl_mtm arithmetic exact (settled short +92, long marked at stub mid)",
      a.get("pnl_mtm") == -153.0)
check("core verdict computable again (was None while unmarked)",
      "core_reason" in a)
check("live leg was quoted through normal fetchers",
      any(c[1] == live_leg["expiration"] for c in calls))

# ── 3 · save_check honesty guard (against the snapshot) ──────────────────────
def _count_rows():
    c = sqlite3.connect(snap)
    n = c.execute("SELECT count(*) FROM checks WHERE position_id=?", (pos["id"],)).fetchone()[0]
    c.close(); return n

base = {"pnl_mtm": None, "flag": "GREEN", "reasons": [],
        "opt_data": {"bid": 1.0, "mid": None}, "opt_source": "ibkr-live",
        "iv_rank": None, "iv_percentile": None, "rth_flag": None,
        "underlying_price": 53.0, "shadow": None, "arms": None}
n0 = _count_rows()
cc.save_check(pos["id"], dict(base), pos, None)
check("GOOD-with-NULL-pnl row is NOT persisted", _count_rows() == n0)

good = dict(base); good["pnl_mtm"] = -153.0
good["opt_data"] = {"bid": 3.0, "ask": 3.2, "mid": 3.1}
cc.save_check(pos["id"], good, pos, None)
n2 = _count_rows()
check("row WITH a mark still persists as GOOD", n2 == n0 + 1)
c = sqlite3.connect(snap)
row = c.execute("SELECT data_quality, pnl_unrealized FROM checks WHERE position_id=? "
                "ORDER BY checked_at DESC LIMIT 1", (pos["id"],)).fetchone()
c.close()
check("persisted row reads GOOD with the mark", row == ("GOOD", -153.0))

# ── 4 · paper_exit_agent.leg_mid ─────────────────────────────────────────────
sys.path.insert(0, str(ROOT))
import importlib.util
spec = importlib.util.spec_from_file_location("pea", ROOT / "paper_exit_agent.py")
pea = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pea)

class FakeLeg:
    def __init__(self, **kw): self.__dict__.update(kw)
class FakeTk:
    ticker = "EQT"
    def option_chain(self, exp):
        raise AssertionError("option_chain called for expired leg")

lm = pea.leg_mid(FakeTk(), FakeLeg(expiration=exp_leg["expiration"], option_type="CALL", strike=55.0))
check("leg_mid: expired leg returns settlement 0.0 (chain never touched)", lm == 0.0)
ex._close_cache[("EQT", "2026-06-19")] = 60.0
lm2 = pea.leg_mid(FakeTk(), FakeLeg(expiration="2026-06-19", option_type="CALL", strike=55.0))
check("leg_mid: expired ITM leg returns intrinsic", lm2 == 5.0)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
