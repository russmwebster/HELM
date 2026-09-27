"""_s123/verify_w195.py -- W195 (lc-screen-v2, Russ 2026-09-27) on a FRESH
database copy. Never opens the live database for writing; no network.

    python3 _s123/verify_w195.py

  1. the screen   -- G1 recorded not gated; 50/50 calm/cheap, no RSI; the
                     0.72 bar; S&P gate fails closed; G3/G4/G5 unchanged
  2. the replay   -- v2 over all 73 stored scans passes exactly the names the
                     re-score/replay scripts passed
  3. S&P reading  -- spx_vs_200_now on a fake fetcher: above, below, and every
                     failure returns None with a reason
  4. real flags   -- sleeve >= 5% and one-per-name decline a real long call
                     through the W201 path; typed count overrides; recorded
  5. scan footer  -- the Real column: suggest / held / 2/day / sleeve
  6. paper        -- one per name, 2 a day, W195-only sleeve before the trade,
                     refusals logged (bookers faked: no chain fetch)
  7. both ways    -- each rule removed in memory flips its check
Harness preamble (copy, network isolation) is verify_w201's, unchanged.
"""
import contextlib, hashlib, io, os, sqlite3, sys, tempfile

os.environ["COLUMNS"] = "300"          # rich wraps at 80 off a tty; keep lines whole
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PG = os.path.join(os.path.dirname(ROOT), "helm-pg")
sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, "data", "helm.db")
TMP = tempfile.mkdtemp(prefix="w195_")
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
        raise ConnectionRefusedError("verify_w195: IB gateway blocked in the harness")
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


def db(sql, *a):
    c = sqlite3.connect(COPY)
    try:
        c.execute(sql, a)
        c.commit()
    finally:
        c.close()


import csv, json, uuid                                     # noqa: E402
from datetime import date, datetime, timedelta             # noqa: E402
from rich.console import Console                           # noqa: E402
from helm import lc_screen as L                            # noqa: E402
from helm import market_context as MC                      # noqa: E402
import helm.cli.open_cmd as oc                             # noqa: E402
import helm.cli.scan_cmd as sc                             # noqa: E402
import helm.cli._paper_generate as pgen                   # noqa: E402
A = get_active_account()
print("         account  %s\n" % A)
ABOVE = {"above": True, "spx": 6600.0, "sma200": 6000.0, "asof": "2026-09-25", "source": "test"}


def row(t, hv252=18.0, ratio=0.72, d2e=40, bias=0, rsi=50.0, **kw):
    r = {"ticker": t, "hv_252": hv252, "iv_hv90_ratio": ratio, "days_to_earnings": d2e,
         "bias_score": bias, "rsi_14": rsi, "spot_price": 100.0, "sma_50": 110.0,
         "sma_200": 120.0, "hv_90": 20.0, "adx": 10.0}
    r.update(kw)
    return r


# ------------------------------------------------------------ 1. the screen
print("-- 1. lc-screen-v2 (pure)")
b = [row("CALM"), row("RSIHOT", rsi=95.0), row("MID", hv252=30.0, ratio=0.80),
     row("EDGE", hv252=20.0, ratio=0.81204), row("G3", ratio=0.95), row("G4", d2e=3),
     row("G5", hv252=45.0), row("NOHV", hv252=None)]
surv = L.screen(b, market=ABOVE)
by = {r["ticker"]: r for r in b}
g = json.loads(by["CALM"]["lc_gates_json"])
ok("version is lc-screen-v2 (W195)", g["version"] == "lc-screen-v2 (W195)", g["version"])
ok("G1 is recorded, not gated: bias 0 and a broken MA stack still pass",
   by["CALM"]["lc_screen_pass"] == 1 and g["g1"]["gate"] is False and g["g1"]["ok"] is False
   and g["g1"]["bias"] == 0)
ok("score = 50% calm + 50% cheap (HV252 18 -> 1.0, ratio 0.72 -> 0.9) = 0.95",
   by["CALM"]["lc_rank_score"] == 0.95, by["CALM"]["lc_rank_score"])
ok("no RSI penalty: RSI 95 scores the same", by["RSIHOT"]["lc_rank_score"] == 0.95)
ok("score 0.50 is rejected 'below bar'", by["MID"]["lc_screen_reject"] == "below bar"
   and by["MID"]["lc_rank_score"] == 0.5)
ok("0.7199 is under the 0.72 bar", by["EDGE"]["lc_screen_pass"] == 0
   and by["EDGE"]["lc_rank_score"] < 0.72, by["EDGE"]["lc_rank_score"])
ok("G3 / G4 / G5 still gate, and the bar is not listed on top of them",
   (by["G3"]["lc_screen_reject"], by["G4"]["lc_screen_reject"], by["G5"]["lc_screen_reject"])
   == ("G3 vol", "G4 earnings ramp", "G5 vol ceiling"),
   (by["G3"]["lc_screen_reject"], by["G4"]["lc_screen_reject"], by["G5"]["lc_screen_reject"]))
ok("unmeasured HV252 cannot pass", by["NOHV"]["lc_screen_pass"] == 0
   and by["NOHV"]["lc_rank_score"] is None, by["NOHV"]["lc_screen_reject"])
ok("survivors ranked 1..n by score", [r["ticker"] for r in surv] == ["CALM", "RSIHOT"]
   and [r["lc_screen_rank"] for r in surv] == [1, 2])
ok("bar and components recorded on a rejected row too",
   json.loads(by["G3"]["lc_gates_json"])["bar"]["bar"] == 0.72
   and "calmness" in json.loads(by["G3"]["lc_gates_json"])["components"])
b2 = [row("A1")]
L.screen(b2, market={"above": False, "spx": 5000, "sma200": 5500})
ok("S&P below its 200-day rejects", b2[0]["lc_screen_reject"] == "S&P below 200d")
b3 = [row("A1")]
L.screen(b3)
ok("S&P not passed in -> fails closed", b3[0]["lc_screen_reject"] == "S&P unknown")
b4 = [row("A1")]
L.screen(b4, market={"above": None, "error": "empty frame"})
ok("S&P unread -> fails closed, reason kept in the record",
   b4[0]["lc_screen_reject"] == "S&P unknown"
   and json.loads(b4[0]["lc_gates_json"])["mkt"]["error"] == "empty frame")
ok("bar 0.72, max 2/day, marked provisional", (L.RANK_BAR, L.MAX_NEW_PER_DAY) == (0.72, 2)
   and "provisional" in json.loads(by["CALM"]["lc_gates_json"])["bar"]["note"])

# ------------------------------------------- 2. same answer as the replay
print("\n-- 2. v2 on the 73 stored scans == the replay's rule (_s123/w195_rescore.py)")
px = [(r["Date"], float(r["Close"])) for r in csv.DictReader(open(os.path.join(ROOT, "_s122/px/SPY.csv")))]
spy = {px[i][0]: px[i][1] > sum(x for _, x in px[i-199:i+1]) / 200 for i in range(199, len(px))}
def sp_above(d):
    ks = [k for k in spy if k <= d]
    return spy[max(ks)] if ks else None
cheap = lambda r: 1.0 if r <= 0.70 else 0.0 if r >= 0.90 else (0.90 - r) / 0.20
calm = lambda h: 1.0 if h <= 20 else 0.0 if h >= 40 else (40 - h) / 20
sc_ = sqlite3.connect(COPY)
sc_.row_factory = sqlite3.Row
scans = {}
for s in sc_.execute("SELECT * FROM signals WHERE lc_screen_pass IS NOT NULL ORDER BY generated_at"):
    scans.setdefault(s["generated_at"], []).append(dict(s))
sc_.close()
diff, n_pass, n_rows = [], 0, 0
for gen, rows in scans.items():
    mk = {"above": sp_above(gen[:10])}
    want = set()
    for r in rows:
        gj = json.loads(r["lc_gates_json"])
        ratio, hv = gj["g3"].get("iv_hv90_ratio"), (r["hv_252"] if r["hv_252"] is not None else gj["g5"].get("hv_252"))
        if (mk["above"] and ratio is not None and ratio <= 0.90 and gj["g4"].get("state") == "clear"
                and hv is not None and hv < 40 and 0.5 * calm(hv) + 0.5 * cheap(ratio) >= 0.72):
            want.add(r["ticker"])
        r["iv_hv90_ratio"], r["hv_252"] = ratio, hv
        r["days_to_earnings"] = gj["g4"].get("days_to_earnings")   # the scan-time input
    got = {r["ticker"] for r in L.screen(rows, market=mk)}
    n_pass += len(got); n_rows += len(rows)
    if got != want:
        diff.append((gen, sorted(got ^ want)))
ok("%d scans, %d rows: identical pass sets" % (len(scans), n_rows), not diff and len(scans) == 73,
   diff[:3] or "%d passes" % n_pass)

# ---------------------------------------------------- 3. S&P at scan time
print("\n-- 3. spx_vs_200_now (fake fetcher, no network)")
T = date(2026, 9, 25)
def series(n, last_px, base=100.0, end=T):
    d, out = end, {}
    while len(out) < n:
        if d.weekday() < 5:
            out[d] = base
        d -= timedelta(days=1)
    out[end] = last_px
    return out
r = MC.spx_vs_200_now(fetcher=lambda *a: series(260, 120.0), today=T)
ok("above", r["above"] is True and r["spx"] == 120.0 and r["asof"] == str(T), r)
r = MC.spx_vs_200_now(fetcher=lambda *a: series(260, 80.0), today=T)
ok("below", r["above"] is False, r)
r = MC.spx_vs_200_now(fetcher=lambda *a: {}, today=T)
ok("empty frame -> None + reason", r["above"] is None and "empty" in r["error"], r["error"])
def boom(*a):
    raise TimeoutError()
r = MC.spx_vs_200_now(fetcher=boom, today=T)
ok("fetch raised -> None + reason", r["above"] is None and "TimeoutError" in r["error"], r["error"])
r = MC.spx_vs_200_now(fetcher=lambda *a: series(150, 120.0), today=T)
ok("fewer than 200 bars -> None", r["above"] is None and "fewer than 200" in r["error"], r["error"])
r = MC.spx_vs_200_now(fetcher=lambda *a: series(260, 120.0, end=T - timedelta(days=9)), today=T)
ok("a 9-day-old last bar -> None (stale)", r["above"] is None and "stale" in r["error"], r["error"])
_scan_src = open(sc.__file__).read()
ok("scan passes the reading into the screen", "_lcs.screen([r for r in results if not r.get(\"error\")], market=_mkt)" in _scan_src
   and "spx_vs_200_now()" in _scan_src)

# ----------------------------------------------------------- 4. real flags
print("\n-- 4. real book: W195 flags (suggest_contracts -> CLI decline / override)")
sv = risk_cap.sleeve_view(A, "REAL")
ok("copy's real sleeve is over 5%% (%.2f%%)" % sv["pct"], sv["over_cap"] is True)
n, bnd, note = oc.suggest_contracts("LONG_CALL", 70.0, 3.00, A, ticker="ZZZ")
ok("sleeve over 5% -> a real long call is declined (W194 flag)",
   (n, bnd) == (0, "w195_sleeve") and "%.1f%% of the account" % sv["pct"] in note, (n, bnd, note))
n2, b2_, _ = oc.suggest_contracts("DIAGONAL", None, 30.0, A, ticker="ZZZ")
ok("  ... a diagonal is not flagged by W195", n2 == 1 and b2_ == "risk_cap", (n2, b2_))
n3, b3_, note3 = oc.suggest_contracts("LONG_CALL", 700.0, 160.95, A, ticker="LLY")
ok("  ... a W201 decline keeps W201 and gains the flag",
   (n3, b3_) == (0, "risk_cap") and note3.startswith("one contract is $16,095, over the $5,000 cap (W201); also the real long-premium sleeve"), note3)
n4, _, _ = oc.suggest_contracts("LONG_CALL", 70.0, 3.00, A, ticker="")
ok("  ... no ticker -> no flag lookup (sizing unchanged)", n4 == 16, n4)

LC = {"ticker": "ZZZ", "opt_type": "CALL", "strike": 70.0, "expiration": "2027-01-15", "dte": 110,
      "bid": 2.95, "ask": 3.05, "mid": 3.00, "delta": 0.75, "theta": -0.02, "gamma": 0.01,
      "vega": 0.1, "iv": 18.0, "oi": 1500, "spread_pct": 3.0, "source": "ibkr-synthetic",
      "direction": "LONG"}
CFG = oc.STRATEGY_CONFIG["LONG_CALL"]
Q = "select count(*) from positions where ticker=? and strategy='LONG_CALL' and book='REAL'"
z0 = count(Q, "ZZZ")
with feed("1\n3.00\n\n") as out:
    oc.confirm_and_log("ZZZ", "LONG_CALL", [dict(LC)], CFG, 71.0)
t = out.getvalue()
ok("CLI: 'Declined:' with the sleeve figure; Enter records nothing (W194)",
   "Declined: the real long-premium sleeve is" in t and count(Q, "ZZZ") == z0
   and "Nothing was recorded -- declined (W194)." in t, t[-200:])
with feed("1\n3.00\n2\ny\n") as out:
    oc.confirm_and_log("ZZZ", "LONG_CALL", [dict(LC)], CFG, 71.0)
t = out.getvalue()
note_z = sqlite3.connect(COPY).execute("select notes from positions where ticker='ZZZ' and book='REAL' "
                                       "order by created_at desc limit 1").fetchone()
ok("typed count overrides, said out loud, books 2",
   "Override: 2 contract(s) with the real long-premium sleeve at or over 5% (W194)" in t
   and count(Q, "ZZZ") == z0 + 1)
ok("  ... recorded: OVER-CAP OVERRIDE (W194)", note_z and "OVER-CAP OVERRIDE (W194): the real long-premium sleeve is"
   in (note_z[0] or ""), note_z)

pv = sqlite3.connect(COPY).execute("select portfolio_value from accounts where id=?", (A,)).fetchone()[0]
db("update accounts set portfolio_value=? where id=?", pv * 10, A)
ok("(account x10 on the copy -> real sleeve %.2f%%, under 5%%)" % risk_cap.sleeve_view(A, "REAL")["pct"],
   risk_cap.sleeve_view(A, "REAL")["over_cap"] is False)
n, bnd, note = oc.suggest_contracts("LONG_CALL", 70.0, 3.00, A, ticker="QQQX")
ok("sleeve under 5%, name not held -> sized normally, no flag", (n, bnd) == (16, "risk_cap"), (n, bnd, note))
n, bnd, note = oc.suggest_contracts("LONG_CALL", 70.0, 3.00, A, ticker="KO")
ok("KO holds a real long call -> declined, one per name (W195)",
   (n, bnd) == (0, "w195_held") and note == "KO already has an open LONG_CALL -- one long call per name (W195)", note)
held = risk_cap.held_long_names("REAL")
dg_name = next((k for k, v in held.items() if v == "DIAGONAL" and k not in
                {k2 for k2, v2 in held.items() if v2 == "LONG_CALL"}), None)
n, bnd, note = oc.suggest_contracts("LONG_CALL", 70.0, 3.00, A, ticker=dg_name)
ok("a call diagonal's long leg counts as a long call in the name (%s)" % dg_name,
   bnd == "w195_held" and "open DIAGONAL" in note, note)
KO = dict(LC, ticker="KO")
k0 = count(Q, "KO")
with feed("1\n3.00\n\n") as out:
    oc.confirm_and_log("KO", "LONG_CALL", [dict(KO)], CFG, 71.0)
t = out.getvalue()
ok("CLI: held name declined, Enter records nothing (W195)", count(Q, "KO") == k0
   and "Nothing was recorded -- declined (W195)." in t)
with feed("1\n3.00\n1\ny\n") as out:
    oc.confirm_and_log("KO", "LONG_CALL", [dict(KO)], CFG, 71.0)
t = out.getvalue()
nk = sqlite3.connect(COPY).execute("select notes from positions where ticker='KO' and book='REAL' "
                                   "and strategy='LONG_CALL' order by created_at desc limit 1").fetchone()[0] or ""
ok("override books it and records RULE OVERRIDE (W195)",
   count(Q, "KO") == k0 + 1 and "Override: 1 contract(s) in a name that already has an open long call (W195)" in t
   and "RULE OVERRIDE (W195): KO already has an open LONG_CALL" in nk, nk)

# ------------------------------------------------- 5. scan footer (real)
print("\n-- 5. scan footer: real suggestions")
foot_rows = [row("KO", ratio=0.70), row("ABT", ratio=0.71), row("WMT", ratio=0.72), row("V", ratio=0.73)]
L.screen(foot_rows, market=ABOVE)
def footer():
    buf = io.StringIO()
    old = sc.console
    sc.console = Console(file=buf, width=250, force_terminal=False, color_system=None)
    try:
        sc._print_lc_screen(foot_rows)
    finally:
        sc.console = old
    return buf.getvalue()
t = footer()
lines = {ln.split()[1]: ln for ln in t.splitlines() if ln.strip()[:1].isdigit()}
ok("header: bar 0.72 provisional, max 2/day, S&P above",
   "bar 0.72 (provisional, review ~2026-10-27)" in t and "max 2/day" in t and "S&P above" in t, t.splitlines()[0])
ok("section 4 booked 2 real long calls today (ZZZ, KO) -> the rest read 'no: 2/day'",
   "no: held" in lines.get("KO", "") and all("no: 2/day" in lines.get(k, "") for k in ("ABT", "WMT", "V")), lines)
db("update positions set opened_at='2026-01-02T10:00:00' where book='REAL' and strategy='LONG_CALL' "
   "and substr(opened_at,1,10)=?", date.today().isoformat())
t = footer()
lines = {ln.split()[1]: ln for ln in t.splitlines() if ln.strip()[:1].isdigit()}
ok("none today: KO held -> 'no: held'; next two -> 'suggest'; fourth -> 'no: 2/day'",
   "no: held" in lines.get("KO", "") and "suggest" in lines.get("ABT", "")
   and "suggest" in lines.get("WMT", "") and "no: 2/day" in lines.get("V", ""), lines)
db("update accounts set portfolio_value=? where id=?", pv, A)
t = footer()
lines = {ln.split()[1]: ln for ln in t.splitlines() if ln.strip()[:1].isdigit()}
ok("real sleeve over 5% -> every row 'no: sleeve' and the red line",
   all("no: sleeve" in v for v in lines.values()) and len(lines) == 4
   and "no real long-call suggestions until it is under" in t, t[-300:])

# --------------------------------------------------------- 6. paper routing
print("\n-- 6. paper routing (paper_generate buy wing)")
pgen.is_market_open = lambda: True
pgen._latest_run_passed_on = lambda: []                   # sell wing off: not under test
COST = {}
def fake_booker(ticker, strategy, spot, scan_data=None):
    pid = "TEST-" + uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    c = sqlite3.connect(COPY)
    c.execute("insert into positions (id, account_id, strategy, ticker, status, opened_at, total_contracts, book) "
              "values (?,?,?,?,?,?,?,?)", (pid, A, strategy, ticker, "OPEN", now, 1, "PAPER"))
    c.execute("insert into legs (id, position_id, leg_role, direction, contracts, multiplier, open_price, "
              "open_date, status) values (?,?,?,?,?,?,?,?,?)",
              (uuid.uuid4().hex, pid, "LONG_CALL", "LONG", 1, 100, COST.get(ticker, 4000) / 100.0, now[:10], "OPEN"))
    c.commit(); c.close()
    return pid
pgen._PAPER_BOOKERS["LONG_CALL"] = fake_booker
tmpl = dict(sqlite3.connect(COPY).execute("select * from signals where lc_screen_pass is not null "
                                          "order by generated_at desc limit 1").fetchone() and {})
c = sqlite3.connect(COPY); c.row_factory = sqlite3.Row
tmpl = dict(c.execute("select * from signals where lc_screen_pass is not null order by generated_at desc limit 1").fetchone())
c.close()
GEN = "2099-01-01T15:00:00"
for i, tk in enumerate(["AAPL", "KO", "ABT", "WMT", "V"], 1):
    s = dict(tmpl, id="SIG-W195-" + tk, ticker=tk, generated_at=GEN, lc_screen_pass=1, lc_screen_rank=i,
             lc_screen_reject=None, lc_rank_score=0.9 - i / 100, iv_hv90_ratio=0.75, spot_price=100.0,
             russ_action=None, lc_gates_json=json.dumps({"version": L.SCREEN_VERSION}))
    cc = sqlite3.connect(COPY)
    cc.execute("insert into signals (%s) values (%s)" % (",".join(s), ",".join("?" * len(s))), list(s.values()))
    cc.commit(); cc.close()
ps = risk_cap.w195_paper_sleeve(A)
ok("paper W195 sleeve starts at $0 (v1 long calls and %d-odd diagonals not counted; full paper sleeve %.1f%%)"
   % (len(risk_cap.sleeve_view(A, "PAPER")["positions"]), risk_cap.sleeve_view(A, "PAPER")["pct"]),
   ps["value"] == 0 and ps["pct"] == 0, ps.get("error"))
def run():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = pgen.paper_generate()
    return {tk: why for tk, _, why in res["skipped"]}, [tk for tk, _, _ in res["booked"]]
def refusals():
    try:
        return count("select count(*) from paper_refusals where rule='W194'")
    except sqlite3.OperationalError:
        return 0
r0 = refusals()
sk, bk = run()
ok("run 1: AAPL (paper already holds a long call) skipped; KO, ABT booked; WMT, V over 2/day",
   bk == ["KO", "ABT"] and sk.get("AAPL", "").startswith("one long call per name (W195)")
   and sk.get("WMT", "").startswith("daily limit (W195)") and sk.get("V", "").startswith("daily limit"), (bk, sk))
ok("  ... stamped LC_SCREEN + the v2 signal, so they count in the W195 sleeve ($8,000)",
   risk_cap.w195_paper_sleeve(A)["value"] == 8000.0, risk_cap.w195_paper_sleeve(A)["value"])
sk, bk = run()
ok("run 2 (same day): nothing booked -- held names and the daily limit",
   bk == [] and sk.get("KO", "").startswith("one long call per name") and sk.get("WMT", "").startswith("daily limit"), (bk, sk))
db("update positions set opened_at='2026-01-02T10:00:00' where id like 'TEST-%'")
db("update legs set open_price=175.0 where position_id like 'TEST-%'")          # 2 x $17,500 = $35,000
sk, bk = run()
ok("run 3 (new day, W195 sleeve $35,000 = %.2f%%): WMT and V refused by W194, nothing booked"
   % (100 * 35000 / pv), bk == [] and sk.get("WMT", "").startswith("refused by W194: W195 paper long calls hold $35,000")
   and sk.get("V", "").startswith("refused by W194"), (bk, sk))
ok("  ... each refusal logged to paper_refusals (rule W194)", refusals() == r0 + 2, refusals() - r0)
db("update legs set open_price=160.0 where position_id like 'TEST-%'")          # $32,000 = 4.65%
sk, bk = run()
ok("run 4 ($32,000 before): WMT books (under 5% before the trade), V refused once it is over",
   bk == ["WMT"] and sk.get("V", "").startswith("refused by W194: W195 paper long calls hold $36,000"), (bk, sk))
ok("  ... paper diagonals never entered the paper cap (option (a))",
   all(p["strategy"] == "LONG_CALL" for p in risk_cap.w195_paper_sleeve(A)["positions"]))

# ------------------------------------------------------- 7. both ways
print("\n-- 7. both ways")
_saved = risk_cap.apply_w195_real_flags
risk_cap.apply_w195_real_flags = lambda s, t, a, d, db=None: d
n, bnd, _ = oc.suggest_contracts("LONG_CALL", 70.0, 3.00, A, ticker="ZZZ")
risk_cap.apply_w195_real_flags = _saved
ok("control: with the W195 fold removed in memory, the over-sleeve call IS suggested (16)", n == 16, n)
n, bnd, _ = oc.suggest_contracts("LONG_CALL", 70.0, 3.00, A, ticker="ZZZ")
ok("  ... restored, it is declined again", (n, bnd) == (0, "w195_sleeve"))
_saved_bar = L.RANK_BAR
L.RANK_BAR = 0.99
bb = [row("CALM")]; L.screen(bb, market=ABOVE)
L.RANK_BAR = _saved_bar
ok("control: a 0.99 bar in memory rejects the 0.95 name the 0.72 bar passes",
   bb[0]["lc_screen_reject"] == "below bar" and by["CALM"]["lc_screen_pass"] == 1)
ok("IB connect never attempted", not IB_ATTEMPTS, IB_ATTEMPTS)
ok("helm.config DB_PATH is the copy throughout", str(DB_PATH) == COPY)

print("\n%s -- %d checks, %d failed" % ("PASS" if not FAILS else "FAIL", N, len(FAILS)))
for f in FAILS:
    print("  FAIL: " + f)
sys.exit(1 if FAILS else 0)
