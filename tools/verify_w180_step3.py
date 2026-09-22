"""W180 step 3 -- close one leg, keep the position; a CLOSED leg marks at close_price, never quoted.

Usage (fresh copy every run -- the harness makes its own):
    HELM_ROOT=~/Projects/helm python3 tools/verify_w180_step3.py            # patched: expect PASS
    HELM_ROOT=~/Projects/helm python3 tools/verify_w180_step3.py <check_cmd .bak>   # control: expect FAIL on the quote cases

Fixture: the ABT PAPER diagonal (long 105C 11-20 @ 9.30, short 112C 10-02 @ 2.20).
On a VACUUM INTO copy: close the short at 0.45 via close_leg, then run the REAL
check_one with IBKR stubbed and count which contracts it quotes. Then close the
whole position through _finalize_close with the short already closed, and check
it is counted once. Prints the check_cmd file it loaded.
"""
import os, sys, sqlite3, importlib, types
from importlib.machinery import SourceFileLoader

ROOT = os.environ.get("HELM_ROOT") or os.getcwd()
sys.path.insert(0, ROOT)
COPY = os.path.join(os.path.expanduser("~"), "w180s3_verify.db")
LIVE = os.path.join(ROOT, "data", "helm.db")
POS = "ABT-DIAGONAL-20260831-EFF093"

# fresh copy, always
if os.path.exists(COPY):
    os.remove(COPY)
src = sqlite3.connect("file:" + LIVE + "?mode=ro", uri=True)
src.execute("VACUUM INTO ?", (COPY,)); src.close()
os.environ["HELM_DB"] = COPY
live_before = os.path.getsize(LIVE)

# import AFTER HELM_DB is set so config.DB_PATH points at the copy
import helm.config as _cfg
assert str(_cfg.DB_PATH) == COPY, f"DB_PATH is {_cfg.DB_PATH}, not the copy"
from helm.models.position import Position
from helm.models.leg import Leg
from helm.models.lifecycle import LifecycleEvent
import helm.cli.close_cmd as close_cmd

target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "helm", "cli", "check_cmd.py")
spec = importlib.util.spec_from_loader("cc", SourceFileLoader("cc", target))
cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
print("check_cmd loaded:", os.path.abspath(target))
print("close_cmd loaded:", os.path.abspath(close_cmd.__file__))

ok = True
def chk(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}{('  -- ' + str(detail)) if detail else ''}")

pos = Position.get(POS)
legs = Leg.for_position(POS)
short = [l for l in legs if l.leg_role == "SHORT_CALL"][0]
long_ = [l for l in legs if l.leg_role == "LONG_CALL"][0]
chk("fixture: both legs OPEN before", short.status == "OPEN" and long_.status == "OPEN")

# --- A. close the short at 0.45, position stays OPEN ---
r = close_cmd.close_leg(pos, legs, short, 0.45, reason="WORTHLESS")
chk("A1 close_leg ok", r.get("ok"), r)
chk("A2 realized on the short = (2.20-0.45)*100 = 175", abs(r.get("realized_pnl", 0) - 175.0) < 1e-6, r.get("realized_pnl"))
legs2 = Leg.for_position(POS)
s2 = [l for l in legs2 if l.id == short.id][0]; l2 = [l for l in legs2 if l.id == long_.id][0]
chk("A3 short CLOSED at 0.45", s2.status == "CLOSED" and abs(s2.close_price - 0.45) < 1e-9, (s2.status, s2.close_price))
chk("A4 long untouched", l2.status == "OPEN" and l2.close_price is None)
chk("A5 position still OPEN", Position.get(POS).status == "OPEN", Position.get(POS).status)
ev = [e for e in LifecycleEvent.for_position(POS)
      if e.event_type == "ADJUSTED" and (e.narrative or "").startswith("LEG_CLOSED")]
chk("A6 one ADJUSTED/LEG_CLOSED event, leg_id + pnl carried", len(ev) == 1 and ev[0].leg_id == short.id and abs(ev[0].pnl_at_event - 175) < 1e-6,
    (len(ev), ev[0].narrative[:80] if ev else None))
# refusals
chk("A7 refuses to re-close a CLOSED leg", not close_cmd.close_leg(pos, legs2, s2, 0.10)["ok"])
chk("A8 refuses to close the LAST open leg", not close_cmd.close_leg(pos, legs2, l2, 4.30)["ok"])

# --- B. the REAL check_one, IBKR stubbed: the closed short must not be quoted ---
quoted = []
def fake_opt(ticker, expiration, strike, option_type):
    quoted.append((expiration, strike))
    return {"underlying": None, "bid": 4.20, "ask": 4.40, "last": 4.30, "mid": 4.30,
            "delta": 0.45, "gamma": 0.03, "theta": -0.05, "vega": 0.10, "iv": 28.0,
            "source": "ibkr", "live": True, "error": None}
cc.fetch_ibkr_option = fake_opt
cc.fetch_ibkr_underlying = lambda t: {"price": 103.0, "live": True}
cc.fetch_yf_data = lambda *a, **k: {"mid": None, "bid": None, "ask": None, "error": "stubbed"}
cc.is_market_open = lambda: True
conn = sqlite3.connect(COPY); conn.row_factory = sqlite3.Row
posd = dict(conn.execute("select * from positions where id=?", (POS,)).fetchone())
legsd = [dict(r) for r in conn.execute("select * from legs where position_id=?", (POS,))]
conn.close()
a = cc.check_one(posd, legsd, persist=False)
chk("B1 only the LONG was quoted (1 IBKR call, 11-20 exp)", len(quoted) == 1 and quoted[0][0] == "2026-11-20", quoted)
chk("B2 primary is the long", (a.get("primary_leg") or {}).get("leg_role") == "LONG_CALL", (a.get("primary_leg") or {}).get("leg_role"))
# pnl_mtm = long (4.30-9.30)*100 = -500  +  short realized 175  = -325
chk("B3 pnl_mtm nets the closed short at 0.45 -> -325", a.get("pnl_mtm") is not None and abs(a["pnl_mtm"] - (-325.0)) < 1e-6, a.get("pnl_mtm"))

# --- C. whole-position close counts the already-closed short ONCE ---
pos3 = Position.get(POS); legs3 = Leg.for_position(POS)
r3 = close_cmd._finalize_close(pos3, legs3, {l2.id: 4.30}, reason="DISCRETIONARY")
chk("C1 realized = 175 + (4.30-9.30)*100 = -325", abs(r3["realized_pnl"] - (-325.0)) < 1e-6, r3["realized_pnl"])
s3 = [l for l in Leg.for_position(POS) if l.id == short.id][0]
chk("C2 the short's close_price untouched (0.45, not re-closed)", abs(s3.close_price - 0.45) < 1e-9, s3.close_price)
chk("C3 position CLOSED", Position.get(POS).status == "CLOSED")

chk("live DB byte-identical", os.path.getsize(LIVE) == live_before)
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
