"""_s123/verify_w189.py -- W189, the CSP spread gate (Russ, 2026-09-26), on a
FRESH database copy. Never opens the live database for writing; no network.

    python3 _s123/verify_w189.py [--no-pg]

  1. the measure  -- (ask - bid) / mid, 0.1 precision, the entry snapshot's own
  2. real sizing  -- >= 10% declined, < 10% sized, not-a-CSP untouched
  3. real CLI     -- decline, Enter records nothing, typed override recorded
  4. paper        -- refused and logged (W189), at-the-line refused, no quote
                     refused, under the line books; W160 still checked first
  5. PG board     -- the open page shows the same decline
  6. both ways    -- the fold removed in memory lets the wide CSP through
Harness preamble (copy, network isolation) is verify_w201's, unchanged.
"""
import contextlib, hashlib, io, os, sqlite3, sys, tempfile

os.environ["COLUMNS"] = "300"          # rich wraps at 80 off a tty; keep lines whole
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PG = os.path.join(os.path.dirname(ROOT), "helm-pg")
sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, "data", "helm.db")
TMP = tempfile.mkdtemp(prefix="w189_")
COPY = os.path.join(TMP, "helm_copy.db")
_s = sqlite3.connect("file:" + SRC + "?mode=ro", uri=True)
_s.execute("VACUUM INTO ?", (COPY,))
_s.close()
os.environ["HELM_DB"] = COPY
os.environ["HELM_ROOT"] = ROOT

# ---- network isolation, before any helm import --------------------------
import types                                               # noqa: E402
_yf = types.ModuleType("yfinance")
_yf.Ticker = lambda t: types.SimpleNamespace(
    fast_info=types.SimpleNamespace(last_price=772.55, display_name=str(t)),
    options=(), history=lambda *a, **k: None, info={})
sys.modules["yfinance"] = _yf                              # stand-in, whole run
IB_ATTEMPTS = []
try:
    import ib_insync as _ibi                               # real module, connect disabled

    def _no_connect(self, *a, **k):
        IB_ATTEMPTS.append((a, k))
        raise ConnectionRefusedError("verify_w189: IB gateway blocked in the harness")
    _ibi.IB.connect = _no_connect
    if hasattr(_ibi.IB, "connectAsync"):
        _ibi.IB.connectAsync = _no_connect
except ImportError:
    pass
import helm.vol_context as _vc                             # noqa: E402
_vc.backfill_entry_vol = lambda *a, **k: None              # the post-booking IBKR call

from helm.config import DB_PATH, get_active_account      # noqa: E402
from helm import risk_cap                                  # noqa: E402
assert str(DB_PATH) == COPY, DB_PATH
RC_PATH = risk_cap.__file__
RC_SHA = hashlib.sha256(open(RC_PATH, "rb").read()).hexdigest()
print("harness: code from %s\n         db copy  %s (fresh VACUUM INTO of %s)" % (ROOT, COPY, SRC))
print("         network  yfinance stubbed; IB connect blocked; vol backfill off")

N, FAILS = 0, []


def ok(label, cond, detail=""):
    global N
    N += 1
    print(("PASS  " if cond else "FAIL  ") + label + (("   [" + str(detail) + "]") if detail else ""))
    if not cond:
        FAILS.append(label)


def count(sql, *a):
    c = sqlite3.connect(COPY)
    try:
        return c.execute(sql, a).fetchone()[0]
    finally:
        c.close()


@contextlib.contextmanager
def feed(text):
    old = sys.stdin
    sys.stdin = io.StringIO(text)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            yield buf
    finally:
        sys.stdin = old


import helm.cli.open_cmd as oc                             # noqa: E402
import helm.cli._paper_open as po                          # noqa: E402
import helm.cli._paper_generate as pgen                   # noqa: E402
# W195's real flags are not under test here (and the live real sleeve is over
# 5%, which would decline long calls -- not CSPs, but keep the harness narrow).
risk_cap.real_long_premium_flags = lambda *a, **k: []
A = get_active_account()
print("         account  %s\n" % A)

def put(bid, ask, **kw):
    mid = round((bid + ask) / 2, 2)
    d = {"ticker": "KO", "opt_type": "PUT", "strike": 65.0, "expiration": "2026-11-06",
         "dte": 36, "bid": bid, "ask": ask, "mid": mid, "delta": 0.25, "theta": -0.02,
         "gamma": 0.02, "vega": 0.05, "iv": 18.0, "oi": 2400,
         "spread_pct": round((ask - bid) / mid * 100, 1), "source": "ibkr-synthetic",
         "direction": "SHORT"}
    d.update(kw)
    return d
WIDE = put(1.76, 2.00)          # 12.8%
EDGE = put(0.95, 1.05)          # exactly 10.0%: at the line counts
TIGHT = put(1.82, 2.00)         # 9.4%
CFG = oc.STRATEGY_CONFIG["CSP"]
Q = "select count(*) from positions where ticker='KO' and strategy='CSP' and book=?"

# ------------------------------------------------------------ 1. the measure
print("-- 1. the measure and the line")
ok("12.8% / 10.0% / 9.4%",
   [risk_cap.spread_pct_of(x) for x in (WIDE, EDGE, TIGHT)] == [12.8, 10.0, 9.4],
   [risk_cap.spread_pct_of(x) for x in (WIDE, EDGE, TIGHT)])
ok("same figure the entry snapshot stores (the deep dive's measure)",
   all(risk_cap.spread_pct_of(x) == x["spread_pct"] for x in (WIDE, EDGE, TIGHT)))

# ------------------------------------------------------------ 2. real sizing
print("\n-- 2. real book: suggest_contracts")
def sug(c):
    return oc.suggest_contracts("CSP", c["strike"], c["mid"], A, ticker="KO", iv=c["iv"],
                                dte=c["dte"], spot=70.0, spread_pct=risk_cap.spread_pct_of(c))
n, b, note = sug(TIGHT)
ok("9.4%: sized normally", n > 0 and b != "w189_spread", (n, b, note))
N_TIGHT = n
n, b, note = sug(EDGE)
ok("10.0%: declined (at the line counts)", (n, b) == (0, "w189_spread"), (n, b, note))
n, b, note = sug(WIDE)
ok("12.8%: declined, the note names the figure and the rule",
   (n, b) == (0, "w189_spread") and note == "the bid-ask spread is 12.8% of mid, at or over 10% "
   "-- not suggested as a CSP (W189)", note)
n2, b2, note2 = oc.suggest_contracts("CSP", 65.0, 1.88, A, ticker="KO", iv=18.0, dte=36, spot=70.0)
ok("no spread passed -> the gate does not run (every caller that suggests now passes it)",
   n2 > 0, (n2, b2))
n3, b3, note3 = oc.suggest_contracts("BULL_PUT_SPREAD", 65.0, 1.88, A, ticker="KO",
                                     spread_pct=30.0)
ok("not a CSP -> untouched", b3 != "w189_spread", (n3, b3))
_src = open(oc.__file__).read()
ok("all three open_cmd callers that size a CSP pass spread_pct",
   _src.count("spread_pct=_rc_sp.spread_pct_of(selected)") == 1
   and _src.count("spread_pct=_risk_cap_sp.spread_pct_of(c)") == 1
   and _src.count("spread_pct=_risk_cap_sp.spread_pct_of(best)") == 1)

# ------------------------------------------------------------ 3. real CLI
print("\n-- 3. real book: confirm_and_log")
r0 = count(Q, "REAL")
with feed("1\n1.88\n\n") as out:
    oc.confirm_and_log("KO", "CSP", [dict(WIDE)], CFG, 70.0)
t = out.getvalue()
ok("'Declined:' with the spread figure; offers a tighter contract",
   "Declined: the bid-ask spread is 12.8% of mid, at or over 10%" in t
   and "Another strike or expiry with a tighter market" in t and "put spread" not in t, t[-300:])
ok("Enter records nothing -- 'declined (W189)'", count(Q, "REAL") == r0
   and "Nothing was recorded -- declined (W189)." in t)
with feed("1\n1.88\n2\ny\n") as out:
    oc.confirm_and_log("KO", "CSP", [dict(WIDE)], CFG, 70.0)
t = out.getvalue()
row = sqlite3.connect(COPY).execute(
    "select total_contracts, notes from positions where ticker='KO' and strategy='CSP' and book='REAL' "
    "order by created_at desc limit 1").fetchone()
ok("a typed count overrides, said out loud, books 2",
   "Override: 2 contract(s) with the bid-ask spread at or over 10% of mid (W189)" in t
   and count(Q, "REAL") == r0 + 1 and row[0] == 2, t[-200:])
ok("  ... recorded: RULE OVERRIDE (W189)", "RULE OVERRIDE (W189): the bid-ask spread is 12.8% of mid"
   in (row[1] or ""), row[1])
with feed("1\n1.91\n\ny\n") as out:
    oc.confirm_and_log("KO", "CSP", [dict(TIGHT)], CFG, 70.0)
t = out.getvalue()
ok("control: a 9.4% CSP books at its suggestion, no decline, no override",
   "Declined" not in t and "Override" not in t and count(Q, "REAL") == r0 + 2)

# ------------------------------------------------------------ 4. paper
print("\n-- 4. paper book (_book_and_stamp -> paper_open_one)")
def refusals(rule):
    try:
        return count("select count(*) from paper_refusals where rule=?", rule)
    except sqlite3.OperationalError:
        return 0
p0, f0 = count(Q, "PAPER"), refusals("W189")
po.evaluate_contracts = lambda *a, **k: [dict(WIDE, source="ibkr")]
pid, why = pgen._book_and_stamp({"id": "SIG-W189"}, "KO", "CSP", 70.0, "SELL_SCREEN")
last = sqlite3.connect(COPY).execute(
    "select rule, one_contract_risk, reason, signal_id, origin_screen from paper_refusals "
    "order by refused_at desc, rowid desc limit 1").fetchone()
ok("12.8%: refused, nothing booked, the reason names it",
   pid is None and why == "refused by W189: the bid-ask spread is 12.8% of mid, at or over 10%"
   and count(Q, "PAPER") == p0, why)
ok("  ... logged: one paper_refusals row, rule W189, NULL risk, signal and origin kept",
   refusals("W189") == f0 + 1 and last == ("W189", None, why, "SIG-W189", "SELL_SCREEN"), last)
po.evaluate_contracts = lambda *a, **k: [dict(EDGE, source="ibkr")]
pid, why = pgen._book_and_stamp({}, "KO", "CSP", 70.0, "SELL_SCREEN")
ok("10.0%: refused (at the line)", pid is None and "10.0% of mid" in (why or ""), why)
po.evaluate_contracts = lambda *a, **k: [dict(WIDE, source="ibkr", ask=None, spread_pct=None)]
pid, why = pgen._book_and_stamp({}, "KO", "CSP", 70.0, "SELL_SCREEN")
ok("no ask -> refused as 'refused: no bid/ask, spread gate couldn't run', logged",
   pid is None and why == "refused: no bid/ask, spread gate couldn't run"
   and refusals("W189") == f0 + 3, why)
po.evaluate_contracts = lambda *a, **k: [dict(TIGHT, source="ibkr")]
pid, why = pgen._book_and_stamp({}, "KO", "CSP", 70.0, "SELL_SCREEN")
ok("control: 9.4% books, nothing logged", pid is not None and count(Q, "PAPER") == p0 + 1
   and refusals("W189") == f0 + 3, why)
es = sqlite3.connect(COPY).execute("select bid_ask_spread_pct from entry_snapshots where position_id=?",
                                   (pid,)).fetchone()
ok("  ... its entry snapshot stores the same 9.4 the gate read", es and es[0] == 9.4, es)
po.evaluate_contracts = lambda *a, **k: [dict(WIDE, source="ibkr", ticker="META", strike=710.0,
                                             iv=49.6, bid=11.0, ask=13.0, mid=12.0, spread_pct=16.7)]
pid, why = pgen._book_and_stamp({}, "META", "CSP", 772.55, "SELL_SCREEN")
ok("over W160 AND wide: W160 is the logged reason (checked first)",
   pid is None and (why or "").startswith("refused by W160"), why)

# ------------------------------------------------------------ 5. PG board
if "--no-pg" not in sys.argv and os.path.isdir(PG):
    print("\n-- 5. PG board open page (%s)" % PG)
    sys.path.insert(0, PG)
    import helm_engine as eng                              # noqa: E402
    oc.evaluate_contracts = lambda *a, **k: [dict(WIDE)]
    r = eng.evaluate("KO", "CSP")
    ok("board: a 12.8% CSP suggests 0 with the W189 wording",
       r.get("suggested_contracts") == 0 and "(W189)" in (r.get("sizing_note") or ""), r.get("sizing_note"))
    import app as pgapp                                    # noqa: E402
    html = pgapp.app.test_client().get("/open/KO?strategy=CSP").get_data(as_text=True)
    ok("board page shows the decline", "Declined:</b> the bid-ask spread is 12.8% of mid" in html)
    oc.evaluate_contracts = lambda *a, **k: [dict(TIGHT)]
    r = eng.evaluate("KO", "CSP")
    ok("board control: a 9.4% CSP is sized", (r.get("suggested_contracts") or 0) > 0,
       (r.get("suggested_contracts"), r.get("sizing_note")))

# ------------------------------------------------------------ 6. both ways
print("\n-- 6. both ways")
_saved = risk_cap.apply_w189_flag
risk_cap.apply_w189_flag = lambda s, sp, d: d
n, b, _ = sug(WIDE)
risk_cap.apply_w189_flag = _saved
ok("control: with the W189 fold removed in memory, the 12.8% CSP IS suggested", n > 0, (n, b))
n, b, _ = sug(WIDE)
ok("  ... restored, it is declined again", (n, b) == (0, "w189_spread"))
ok("IB connect never attempted", not IB_ATTEMPTS, IB_ATTEMPTS)
ok("helm.config DB_PATH is the copy throughout", str(DB_PATH) == COPY)

print("\n%s -- %d checks, %d failed" % ("PASS" if not FAILS else "FAIL", N, len(FAILS)))
for f in FAILS:
    print("  FAIL: " + f)
sys.exit(1 if FAILS else 0)
