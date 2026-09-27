"""_s123/verify_w201.py -- W201, the W160 CSP extension (Russ, 2026-09-27: real
CSP override recorded; paper CSP refused and logged), and the s123 sizing they
finish -- on a FRESH database copy. Never opens the live database for writing.

    python3 _s123/verify_w201.py            # helm-pg looked for at ../helm-pg
    python3 _s123/verify_w201.py --no-pg

What it proves, each as a PASS/FAIL line:
  1. sizing matrix  -- suggest_contracts on the edges (LLY-style $16,095 call,
     exactly $5,000, a cent over, long put, the three diagonal kinds, the two
     CSP worked examples from s122)
  2. real long call and real CSP -- confirm_and_log: decline message, Enter
     records nothing, unreadable count refused, a typed count overrides, books,
     and the override is written to positions.notes
  3. real diagonal  -- _confirm_diagonal: same three behaviours on the long leg
  4. paper          -- CSP (W160), long call and diagonal (W201) refused with a
     named reason, nothing booked, one paper_refusals row each; an unmeasurable
     CSP refused and logged with NULL risk; under-cap trades and a long put book
  5. PG board       -- suggested_contracts is an int (s123 had passed a tuple),
     the page shows the decline, /api/risk and /positions still 200
  6. both ways      -- the self-test FAILS with the W201 rule removed in
     memory, PASSES restored; risk_cap.py is byte-identical after the run
Chain data is synthetic; every evaluator that would fetch a chain is replaced
in memory, and the harness says so.

NETWORK ISOLATION (added after the first run on the Mac): booking a position
runs HELM's post-booking side effects -- backfill_entry_vol connects to the IB
gateway as client id 11, the SNAPSHOT AGENT'S id (W103), and the company name
comes from Yahoo. From the VM both fail at once; on the Mac they really ran,
slowly, and on a weekday inside a snapshot window could have knocked a live
snapshot off the gateway. This harness now blocks every IB connect, stubs
yfinance and no-ops the vol backfill BEFORE importing helm, and aborts if a
connect is attempted anyway. It touches nothing outside its own DB copy.
"""
import contextlib, hashlib, io, os, sqlite3, sys, tempfile

os.environ["COLUMNS"] = "300"          # rich wraps at 80 off a tty; keep lines whole
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PG = os.path.join(os.path.dirname(ROOT), "helm-pg")
sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, "data", "helm.db")
TMP = tempfile.mkdtemp(prefix="w201_")
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
        raise ConnectionRefusedError("verify_w201: IB gateway blocked in the harness")
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
A = get_active_account()
print("         account  %s\n" % A)

# ---------------------------------------------------------------- 1. matrix
print("-- 1. sizing matrix (suggest_contracts)")
W = "one contract is $16,095, over the $5,000 cap (W201)"
matrix = [
    ("LLY-style long call, one contract $16,095", ("LONG_CALL", 700, 160.95), {}, 0, "risk_cap", W),
    ("long call $48.00 -> a genuine 1 by the cap", ("LONG_CALL", 100, 48.00), {}, 1, "risk_cap",
     "sized to 1 by the $5,000/trade risk cap (W160)"),
    ("long call exactly $5,000 -> fits", ("LONG_CALL", 100, 50.00), {}, 1, "risk_cap", None),
    ("long call $5,001 -> declined", ("LONG_CALL", 100, 50.01), {}, 0, "risk_cap",
     "one contract is $5,001, over the $5,000 cap (W201)"),
    ("long put $16,095 -> still floors, labelled floored", ("LONG_PUT", 700, 160.95), {}, 1, "floored", None),
    ("diagonal, long leg $7,660 -> declined", ("DIAGONAL", None, 76.60), {}, 0, "risk_cap",
     "one contract (long leg) is $7,660, over the $5,000 cap (W201)"),
    ("PMCC, long leg $7,660 -> declined", ("PMCC", None, 76.60), {}, 0, "risk_cap", None),
    ("put diagonal, long leg $7,660 -> declined", ("DIAGONAL_PUT", None, 76.60), {}, 0, "risk_cap", None),
    ("diagonal, long leg $3,000 -> 1 by the cap", ("DIAGONAL", None, 30.00), {}, 1, "risk_cap", None),
    ("CSP s122 example $100/IV30/35d/$95 -> 3, cash", ("CSP", 95, 2.0), dict(iv=30, dte=35, spot=100), 3, "cash_ceiling", None),
    ("CSP s122 example $50/IV90/35d/$45 -> 3, cap", ("CSP", 45, 2.0), dict(iv=90, dte=35, spot=50), 3, "risk_cap", None),
]
for label, (st, k, m), kw, want_n, want_b, want_note in matrix:
    n, b, note = oc.suggest_contracts(st, k, m, A, ticker="T", **kw)
    good = (n, b) == (want_n, want_b) and (want_note is None or note == want_note)
    ok(label, good, "%s, %s, %r" % (n, b, note))
    if b == "floored":
        ok("  ... and the floored note does not claim the cap sized it",
           note.startswith("floored to 1, NOT sized by the cap"), note)

# ------------------------------------------------------- 2. real long call
print("\n-- 2. real book, long call (confirm_and_log)")
LLY = {"ticker": "LLY", "opt_type": "CALL", "strike": 700.0, "expiration": "2027-01-15",
       "dte": 110, "bid": 160.40, "ask": 161.50, "mid": 160.95, "delta": 0.72,
       "theta": -0.25, "gamma": 0.002, "vega": 1.1, "iv": 32.0, "oi": 1500,
       "spread_pct": 0.7, "source": "ibkr-synthetic", "direction": "LONG"}
CFG = oc.STRATEGY_CONFIG["LONG_CALL"]
REAL_LC = "select count(*) from positions where ticker='LLY' and strategy='LONG_CALL' and book='REAL'"

b0 = count(REAL_LC)
with feed("1\n160.95\n\n") as out:
    oc.confirm_and_log("LLY", "LONG_CALL", [dict(LLY)], CFG, 780.0)
t = out.getvalue()
ok("decline shown in Russ's wording", "Declined: " + W in t)
ok("Enter (default 0) records nothing", count(REAL_LC) == b0
   and "Nothing was recorded -- declined (W201)." in t)
ok("no 'sized to' anywhere on a declined trade", "sized to" not in t.lower())

with feed("1\n160.95\nabc\n") as out:
    oc.confirm_and_log("LLY", "LONG_CALL", [dict(LLY)], CFG, 780.0)
ok("unreadable count refused, nothing written", count(REAL_LC) == b0
   and "Refusing rather than sizing this myself" in out.getvalue())

with feed("1\n160.95\n1\ny\n") as out:
    oc.confirm_and_log("LLY", "LONG_CALL", [dict(LLY)], CFG, 780.0)
t = out.getvalue()
row = sqlite3.connect(COPY).execute(
    "select total_contracts, notes from positions where ticker='LLY' and strategy='LONG_CALL' "
    "and book='REAL' order by created_at desc limit 1").fetchone()
ok("typed count overrides, said out loud, and books 1",
   "Override: 1 contract(s) over the $5,000 cap (W201)" in t
   and count(REAL_LC) == b0 + 1 and row and row[0] == 1)
ok("  ... and the override is recorded in positions.notes",
   row and "OVER-CAP OVERRIDE (W201): one contract is $16,095, over the $5,000 cap (W201); "
           "booked 1 contract(s) as typed" in (row[1] or ""), row and row[1])

KO = dict(LLY, ticker="KO", strike=70.0, bid=2.95, ask=3.05, mid=3.00, iv=18.0)
REAL_KO = "select count(*) from positions where ticker='KO' and strategy='LONG_CALL' and book='REAL'"
k0 = count(REAL_KO)
with feed("1\n3.00\n\ny\n") as out:
    oc.confirm_and_log("KO", "LONG_CALL", [dict(KO)], CFG, 71.0)
t = out.getvalue()
ok("control: $300 call sized 16 by the cap, Enter books it, no override text",
   "Sized to 16 by the $5,000/trade risk cap (W160)." in t and "Override" not in t
   and count(REAL_KO) == k0 + 1)
ok("  ... and its notes carry no override",
   "OVER-CAP" not in (sqlite3.connect(COPY).execute(
       "select notes from positions where ticker='KO' and book='REAL' order by created_at desc limit 1"
   ).fetchone()[0] or ""))

print("\n-- 2b. real book, CSP (confirm_and_log) -- Russ 2026-09-27: same as longs")
META = {"ticker": "META", "opt_type": "PUT", "strike": 710.0, "expiration": "2026-10-30",
        "dte": 36, "bid": 11.80, "ask": 12.20, "mid": 12.00, "delta": 0.22, "theta": -0.30,
        "gamma": 0.002, "vega": 0.9, "iv": 49.6, "oi": 2400, "spread_pct": 3.3,
        "source": "ibkr-synthetic", "direction": "SHORT"}
CSPCFG = oc.STRATEGY_CONFIG["CSP"]
REAL_CSP = "select count(*) from positions where ticker=? and strategy='CSP' and book='REAL'"
WC = "one contract (one-sigma move) is $12,034, over the $5,000 cap -- not taken as a CSP (W160)"
c0 = count(REAL_CSP, "META")
with feed("1\n12.00\n\n") as out:
    oc.confirm_and_log("META", "CSP", [dict(META)], CSPCFG, 772.55)
t = out.getvalue()
ok("CSP decline shows the one-contract one-sigma figure", "Declined: " + WC in t)
ok("  ... offers a put spread or skipping it", "A put spread takes this" in t)
ok("  ... Enter records nothing", count(REAL_CSP, "META") == c0
   and "Nothing was recorded -- declined (W160)." in t)
with feed("1\n12.00\nabc\n") as out:
    oc.confirm_and_log("META", "CSP", [dict(META)], CSPCFG, 772.55)
ok("  ... unreadable count refused", count(REAL_CSP, "META") == c0
   and "Refusing rather than sizing this myself" in out.getvalue())
with feed("1\n12.00\n1\ny\n") as out:
    oc.confirm_and_log("META", "CSP", [dict(META)], CSPCFG, 772.55)
t = out.getvalue()
row = sqlite3.connect(COPY).execute(
    "select total_contracts, notes from positions where ticker='META' and strategy='CSP' "
    "and book='REAL' order by created_at desc limit 1").fetchone()
ok("CSP typed count overrides and books 1",
   "Override: 1 contract(s) over the $5,000 cap (W160)" in t
   and count(REAL_CSP, "META") == c0 + 1 and row[0] == 1)
ok("  ... and the override is recorded in positions.notes",
   "OVER-CAP OVERRIDE (W160): " + WC + "; booked 1 contract(s) as typed" in (row[1] or ""), row[1])
PRICEY = dict(META, ticker="COST", strike=400.0, mid=3.00, bid=2.95, ask=3.05, iv=15.0)
cost0 = count(REAL_CSP, "COST")
with feed("1\n3.00\n2\ny\n") as out:
    oc.confirm_and_log("COST", "CSP", [dict(PRICEY)], CSPCFG, 420.0)
t = out.getvalue()
ok("CSP over the 5% cash ceiling: same decline, override names the ceiling",
   "Declined: one contract (collateral) is $40,000, over the 5% cash ceiling" in t
   and "Override: 2 contract(s) over the 5% cash ceiling (W160)" in t
   and count(REAL_CSP, "COST") == cost0 + 1)
CALM = dict(META, ticker="KO", strike=65.0, mid=0.80, bid=0.78, ask=0.82, iv=18.0)
ko0 = count(REAL_CSP, "KO")      # the live book already holds a historical KO CSP
with feed("1\n0.80\n\ny\n") as out:
    oc.confirm_and_log("KO", "CSP", [dict(CALM)], CSPCFG, 70.0)
t = out.getvalue()
ok("CSP control: an under-cap CSP books at its suggestion, no override",
   "Declined" not in t and "Override" not in t and count(REAL_CSP, "KO") == ko0 + 1)

# ------------------------------------------------------ 3. real diagonal
print("\n-- 3. real book, diagonal (_confirm_diagonal)")
import helm.cli.diagonal as dg                              # noqa: E402
D = {"short": {"strike": 250.0, "expiration": "2026-11-20", "mid": 4.10, "delta": 0.30,
               "dte": 54, "iv": 40.0, "oi": 900, "mid_source": "synthetic"},
     "long": {"strike": 200.0, "expiration": "2027-06-17", "mid": 76.60, "delta": 0.80,
              "dte": 263, "iv": 38.0, "oi": 500, "mid_source": "synthetic"}}
REAL_DG = "select count(*) from positions where ticker='AMAT' and strategy='DIAGONAL' and book='REAL'"
d0 = count(REAL_DG)
with feed("1\n\n") as out:
    dg._confirm_diagonal("AMAT", 230.0, [dict(D)], args=[])
t = out.getvalue()
ok("decline shown on the long leg",
   "Declined: one contract (long leg) is $7,660, over the $5,000 cap (W201)" in t)
ok("Enter records nothing", count(REAL_DG) == d0 and "Nothing was recorded" in t)
with feed("1\nxyz\n") as out:
    dg._confirm_diagonal("AMAT", 230.0, [dict(D)], args=[])
ok("unreadable count refused (was: silently substituted)",
   count(REAL_DG) == d0 and "Cannot read the contract count" in out.getvalue())
with feed("1\n1\n4.10\n76.60\ny\n") as out:
    dg._confirm_diagonal("AMAT", 230.0, [dict(D)], args=[])
t = out.getvalue()
ok("typed count overrides and books", "Override: 1 contract(s)" in t and count(REAL_DG) == d0 + 1)
ok("  ... and the override is recorded in positions.notes",
   "OVER-CAP OVERRIDE (W201): one contract (long leg) is $7,660" in (sqlite3.connect(COPY).execute(
       "select notes from positions where ticker='AMAT' and strategy='DIAGONAL' and book='REAL' "
       "order by created_at desc limit 1").fetchone()[0] or ""))

# ------------------------------------------------------------- 4. paper
print("\n-- 4. paper book (_book_and_stamp -> bookers)")
import helm.cli._paper_open as po                          # noqa: E402
import helm.cli._paper_generate as pgen                   # noqa: E402
PAPER = "select count(*) from positions where book='PAPER'"
REFUSALS = "select count(*) from paper_refusals"
p0 = count(PAPER)


def refusals():
    try:
        return count(REFUSALS)
    except sqlite3.OperationalError:     # table not created until the first refusal
        return 0


def last_refusal():
    return sqlite3.connect(COPY).execute(
        "select ticker, strategy, rule, one_contract_risk, reason from paper_refusals "
        "order by refused_at desc, rowid desc limit 1").fetchone()


r0 = refusals()
po.evaluate_contracts = lambda *a, **k: [dict(LLY, source="ibkr")]
pid, why = pgen._book_and_stamp({}, "LLY", "LONG_CALL", 780.0, "TEST")
# Paper fills a long at the ASK (module rule: conservative fills), so its one
# contract is $161.50 x 100 = $16,150, not the $16,095 mid.
ok("paper long call refused with a named reason (filled at the ask: $16,150)",
   pid is None and why == "refused by W201: one contract is $16,150, over the $5,000 cap", why)
ok("  ... nothing booked, and one paper_refusals row",
   count(PAPER) == p0 and refusals() == r0 + 1
   and last_refusal() == ("LLY", "LONG_CALL", "W201", 16150.0, why), last_refusal())

po.evaluate_diagonals = lambda *a, **k: [{
    "long_ask": 76.60, "short_bid": 4.10, "long_strike": 200.0, "long_exp": "2027-06-17",
    "long_delta": 0.8, "long_iv": 38.0, "long_dte": 263, "short_strike": 250.0,
    "short_exp": "2026-11-20", "short_delta": 0.3, "short_iv": 40.0, "short_dte": 54,
    "width": 50.0}]
pid, why = pgen._book_and_stamp({}, "AMAT", "DIAGONAL", 230.0, "TEST")
ok("paper diagonal refused on the long leg",
   pid is None and why == "refused by W201: one contract (long leg) is $7,660, over the $5,000 cap", why)
ok("  ... nothing booked, and one paper_refusals row",
   count(PAPER) == p0 and refusals() == r0 + 2 and last_refusal()[2] == "W201")

po.evaluate_contracts = lambda *a, **k: [dict(META, source="ibkr")]
pid, why = pgen._book_and_stamp({"id": "SIG-TEST-META"}, "META", "CSP", 772.55, "SELL_SCREEN")
ok("paper CSP refused by W160 on one contract's one-sigma move",
   pid is None and why == "refused by W160: one contract (one-sigma move) is $12,034, over the $5,000 cap", why)
lr = sqlite3.connect(COPY).execute(
    "select rule, one_contract_risk, reason, signal_id, origin_screen from paper_refusals "
    "order by refused_at desc, rowid desc limit 1").fetchone()
ok("  ... logged 'refused by W160', with the $ figure, signal and screen",
   count(PAPER) == p0 and refusals() == r0 + 3
   and lr == ("W160", 12034.09, why, "SIG-TEST-META", "SELL_SCREEN"), lr)   # stored to the cent
po.evaluate_contracts = lambda *a, **k: [dict(META, source="ibkr", iv=None)]
pid, why = pgen._book_and_stamp({}, "META", "CSP", 772.55, "SELL_SCREEN")
ok("paper CSP with no IV refused as not measurable, logged with NULL risk",
   pid is None and why.startswith("refused by W160: one-sigma not measurable")
   and refusals() == r0 + 4 and last_refusal()[3] is None, why)
po.evaluate_contracts = lambda *a, **k: [dict(CALM, source="ibkr")]
pid, why = pgen._book_and_stamp({}, "KO", "CSP", 70.0, "SELL_SCREEN")
ok("paper CSP control: an under-cap CSP still books, nothing logged",
   pid is not None and count(PAPER) == p0 + 1 and refusals() == r0 + 4, why)
p0 = count(PAPER)

po.evaluate_contracts = lambda *a, **k: [dict(KO, source="ibkr")]
pid, why = pgen._book_and_stamp({}, "KO", "LONG_CALL", 71.0, "TEST")
ok("paper control: an under-cap call still books", pid is not None and count(PAPER) == p0 + 1, why)

po.evaluate_contracts = lambda *a, **k: [dict(LLY, source="ibkr", opt_type="PUT", direction="LONG")]
pid, why = pgen._book_and_stamp({}, "LLY", "LONG_PUT", 780.0, "TEST")
ok("paper long put untouched by W201 (not decided): still books", pid is not None, why)

# -------------------------------------------------------------- 5. PG
if "--no-pg" not in sys.argv and os.path.isdir(PG):
    print("\n-- 5. PG board (%s)" % PG)
    sys.path.insert(0, PG)
    import helm_engine as eng                              # noqa: E402
    oc.evaluate_contracts = lambda *a, **k: [dict(LLY)]
    r = eng.evaluate("LLY", "LONG_CALL")
    ok("board: suggested_contracts is an int 0 (s123 passed a tuple here)",
       r.get("suggested_contracts") == 0 and type(r.get("suggested_contracts")) is int,
       repr(r.get("suggested_contracts")))
    ok("board: sizing note is the engine's wording", r.get("sizing_note") == W, r.get("sizing_note"))
    import app as pgapp                                    # noqa: E402
    cl = pgapp.app.test_client()
    html = cl.get("/open/LLY?strategy=LONG_CALL").get_data(as_text=True)
    ok("board page shows the decline", "Declined:</b> " + W in html)
    ok("board page has no tuple text", "(0, " not in html and "risk_cap'" not in html)
    oc.evaluate_contracts = lambda *a, **k: [dict(META)]   # spot comes from the yfinance stand-in
    html = cl.get("/open/META?strategy=CSP").get_data(as_text=True)
    ok("board CSP page shows the one-sigma decline (stand-in spot $772.55)", "Declined:</b> " + WC in html)
    oc.evaluate_contracts = lambda *a, **k: [dict(KO)]
    html = cl.get("/open/KO?strategy=LONG_CALL").get_data(as_text=True)
    ok("board control: 'Suggested size: 16 contracts (sized to 16 by ...)'",
       "Suggested size: 16 contracts" in html and "sized to 16 by the $5,000/trade risk cap (W160)" in html)
    for path in ("/api/risk", "/positions", "/api/exposure"):
        ok("board %s still 200" % path, cl.get(path).status_code == 200)
else:
    print("\n-- 5. PG board skipped")

# -------------------------------------------------------- 6. both ways
print("\n-- 6. verify both ways (risk_cap self-test)")
with contextlib.redirect_stdout(io.StringIO()):
    saved = risk_cap.DECLINE_AT_ZERO
    risk_cap.DECLINE_AT_ZERO = ("CSP",)            # W201 removed, in memory only
    broken = risk_cap._selftest()
    risk_cap.DECLINE_AT_ZERO = saved
    restored = risk_cap._selftest()
ok("self-test FAILS with the W201 rule removed", broken != 0)
ok("self-test PASSES restored", restored == 0)
ok("risk_cap.py byte-identical after the run",
   hashlib.sha256(open(RC_PATH, "rb").read()).hexdigest() == RC_SHA)
ok("live database untouched (this run wrote only to the copy)", str(DB_PATH) == COPY)
ok("no IB gateway connection was attempted", not IB_ATTEMPTS, IB_ATTEMPTS[:2])
# control for that zero: a deliberate connect must be caught by the block
if "ib_insync" in sys.modules:
    import helm.ibkr as _hi
    try:
        _hi.get_ib()
        caught = False
    except Exception:
        caught = True
    ok("control: a deliberate get_ib() is blocked and counted", caught and len(IB_ATTEMPTS) == 1)
else:
    ok("control: ib_insync not installed here, so nothing can connect", True, "no ib_insync")

print("\n%d checks, %d failed%s" % (N, len(FAILS), (": " + "; ".join(FAILS)) if FAILS else ""))
sys.exit(1 if FAILS else 0)
