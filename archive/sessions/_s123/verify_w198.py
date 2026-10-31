"""_s123/verify_w198.py -- W198 (the paper exit agent evaluates after every
snapshot) on a FRESH database copy. Never opens the live database for writing;
no network; verdicts and closes are FAKED (W198 changes WHEN the agent runs,
not what it decides).

    python3 _s123/verify_w198.py

  1. run_after_snapshot -- runs after 10:00 / 12:30, not from 15:00 (the 15:35
     run covers the 15:15 snapshot); HELM_W198_OFF=1 turns it off
  2. main(trigger)      -- the same pass; ledger note "after snapshot HH:MM"
  3. the lock           -- a second pass waits, then records "lock busy"
  4. helm snapshot      -- calls it in-process after --all / --paper only; a
                           hook failure is printed and never costs the snapshot
  5. audit eod          -- post-snapshot rows neither satisfy the 15:35 slot
                           nor read as launchd catch-ups; a real catch-up still FAILs
Harness preamble (copy, network isolation) is verify_w201's, unchanged.
"""
import contextlib, hashlib, io, os, sqlite3, sys, tempfile

os.environ["COLUMNS"] = "300"          # rich wraps at 80 off a tty; keep lines whole
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PG = os.path.join(os.path.dirname(ROOT), "helm-pg")
sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, "data", "helm.db")
TMP = tempfile.mkdtemp(prefix="w198_")
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
        raise ConnectionRefusedError("verify_w198: IB gateway blocked in the harness")
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


# W198 harness extras: the lock lives in the temp dir (never logs/), and the
# snapshot's exit-alert post-pass is off -- on the Mac it raises a real
# macOS notification (osascript).
os.environ["HELM_PAPER_EXITS_LOCK"] = os.path.join(TMP, ".paper_exits.lock")
import helm.exit_alert as _xa                              # noqa: E402
_xa.run_post_snapshot = lambda *a, **k: None
import importlib.util as ilu                               # noqa: E402
from datetime import datetime as _real_dt                  # noqa: E402
from helm import agent_runs as AR                          # noqa: E402
import helm.cli.check_cmd as cc                            # noqa: E402
import helm.cli.close_cmd as clc                           # noqa: E402
import helm.market_calendar as mcal                        # noqa: E402

spec = ilu.spec_from_file_location("paper_exit_agent", os.path.join(ROOT, "paper_exit_agent.py"))
pea = ilu.module_from_spec(spec)
spec.loader.exec_module(pea)
assert str(pea.LOCK_PATH).startswith(TMP), pea.LOCK_PATH   # never the real logs/ lock
c = sqlite3.connect(COPY)
AR.ensure_table(c)
c.close()

# Stubs: every verdict and close is faked -- the question is WHEN the agent runs
# and what it records, not what the decision core says (unchanged by W198).
mcal.agent_should_run = lambda *a, **k: (True, "test: session")
CALLS = {"check_one": 0, "closes": []}
def fake_check_one(pos, legs, persist=False):
    CALLS["check_one"] += 1
    return {"core_reason": "PROFIT_TARGET" if pos["ticker"] == "KO" else "HOLD",
            "pnl_mtm": 1.0, "kept_pct": 0.5}
cc.check_one = fake_check_one
def fake_close(pobj, lobjs, prices, reason):
    CALLS["closes"].append((pobj.ticker, reason))
    return {"ok": True, "realized_pnl": 10.0}
clc._finalize_close = fake_close
pea.leg_mid = lambda tk, lg: 1.00

def ledger(since_id=0):
    c = sqlite3.connect(COPY); c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "select * from agent_runs where agent=? and id>? order by id", (AR.AGENT_EXITS, since_id))]
    finally:
        c.close()
def top_id():
    return sqlite3.connect(COPY).execute("select coalesce(max(id),0) from agent_runs").fetchone()[0]
N_PAPER = count("select count(*) from positions where book='PAPER' and status='OPEN'")
N_KO = count("select count(*) from positions where book='PAPER' and status='OPEN' and ticker='KO'")
print("         paper book on the copy: %d open (%d KO -> faked PROFIT_TARGET)\n" % (N_PAPER, N_KO))

# ------------------------------------------------ 1. when it runs
print("-- 1. run_after_snapshot: the cutoff and the off switch")
got = []
_real_main = pea.main
pea.main = lambda trigger=None: got.append(trigger)
ok("10:00 snapshot -> runs, trigger 'after snapshot 10:00'",
   pea.run_after_snapshot("2026-09-28T10:00:03") is None and got == ["after snapshot 10:00"], got)
ok("12:30 snapshot -> runs", pea.run_after_snapshot("2026-09-28T12:30:01") is None
   and got[-1] == "after snapshot 12:30")
ok("14:59 snapshot -> runs", pea.run_after_snapshot("2026-09-28T14:59:59") is None)
n = len(got)
ok("15:15 snapshot -> not run (15:35 is the late-day pass)",
   pea.run_after_snapshot("2026-09-28T15:15:02") == "cutoff" and len(got) == n)
ok("no start time -> not run", pea.run_after_snapshot(None) == "cutoff" and len(got) == n)
os.environ["HELM_W198_OFF"] = "1"
ok("HELM_W198_OFF=1 -> not run", pea.run_after_snapshot("2026-09-28T10:00:03") == "off" and len(got) == n)
del os.environ["HELM_W198_OFF"]
pea.main = _real_main

# ------------------------------------------------ 2. one pass, recorded
print("\n-- 2. main(trigger): the same pass, a marked ledger row")
i0 = top_id()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    pea.main(trigger="after snapshot 10:00")
t = buf.getvalue()
rows = ledger(i0)
ok("every open paper position evaluated (%d)" % N_PAPER, CALLS["check_one"] == N_PAPER, CALLS["check_one"])
ok("the faked PROFIT_TARGET positions closed (%d)" % N_KO,
   len(CALLS["closes"]) == N_KO and all(r == "PROFIT_TARGET" for _, r in CALLS["closes"]), CALLS["closes"])
ok("one ledger row, notes lead with 'after snapshot 10:00'",
   len(rows) == 1 and rows[0]["notes"].startswith("after snapshot 10:00; held ")
   and rows[0]["attempted"] == N_PAPER and rows[0]["journaled"] == N_KO, rows)
ok("start line names the trigger", "ACTING, after snapshot 10:00" in t, t.splitlines()[0])
i0 = top_id()
with contextlib.redirect_stdout(io.StringIO()):
    pea.main()
rows = ledger(i0)
ok("the scheduled run is unchanged: notes 'held N', no trigger",
   len(rows) == 1 and rows[0]["notes"].startswith("held "), rows)

# ------------------------------------------------ 3. the lock
print("\n-- 3. two passes never overlap")
held_lock = pea._acquire_lock(1)
pea.LOCK_WAIT_S = 1
CALLS["check_one"] = 0
i0 = top_id()
with contextlib.redirect_stdout(io.StringIO()) as b3:
    pea.main(trigger="after snapshot 12:30")
rows = ledger(i0)
ok("lock held elsewhere -> this pass evaluates nothing", CALLS["check_one"] == 0)
ok("  ... and says so in the ledger", len(rows) == 1
   and rows[0]["notes"] == "after snapshot 12:30; not run: lock busy 1s" and rows[0]["attempted"] == 0, rows)
held_lock.close()
with contextlib.redirect_stdout(io.StringIO()):
    pea.main(trigger="after snapshot 12:30")
ok("lock released -> the next pass runs", CALLS["check_one"] == N_PAPER)
pea.LOCK_WAIT_S = 600

# ------------------------------------------------ 4. wired into helm snapshot
print("\n-- 4. helm snapshot calls it (in-process), only when PAPER is covered")
class FakeDT(_real_dt):
    NOW = _real_dt(2026, 9, 28, 10, 0, 3)
    @classmethod
    def now(cls, tz=None):
        return cls.NOW
cc.datetime = FakeDT
os.environ["HELM_PAPER_DRY"] = "0"
i0 = top_id(); CALLS["check_one"] = 0
with contextlib.redirect_stdout(io.StringIO()) as b4:
    cc.cmd_snapshot(["--all"])
rows = ledger(i0)
n_all = count("select count(*) from positions where status='OPEN'")
ok("10:00 snapshot --all: a post-snapshot exits row 'after snapshot 10:00'",
   len(rows) == 1 and rows[0]["notes"].startswith("after snapshot 10:00"), (rows, b4.getvalue()[-300:]))
ok("  ... after the snapshot's own pass (every position, then the paper book again)",
   CALLS["check_one"] == n_all + N_PAPER, (CALLS["check_one"], n_all, N_PAPER))
i0 = top_id()
with contextlib.redirect_stdout(io.StringIO()):
    cc.cmd_snapshot([])
ok("REAL-only snapshot: no paper-exit pass", ledger(i0) == [])
FakeDT.NOW = _real_dt(2026, 9, 28, 15, 15, 2)
i0 = top_id()
with contextlib.redirect_stdout(io.StringIO()) as b5:
    cc.cmd_snapshot(["--all"])
ok("15:15 snapshot --all: no pass, and the log says why",
   ledger(i0) == [] and "the scheduled 15:35 run is the late-day evaluation" in b5.getvalue())
FakeDT.NOW = _real_dt(2026, 9, 28, 10, 0, 3)
_saved = pea.__file__
def boom(*a, **k):
    raise RuntimeError("synthetic")
_real_ex = ilu.spec_from_file_location
ilu.spec_from_file_location = boom
with contextlib.redirect_stdout(io.StringIO()) as b6:
    cc.cmd_snapshot(["--all"])
ilu.spec_from_file_location = _real_ex
ok("a failure in the hook is printed, and the snapshot still finishes",
   "paper exits after snapshot FAILED: RuntimeError: synthetic" in b6.getvalue()
   and "snapshot:" in b6.getvalue())
cc.datetime = _real_dt

# ------------------------------------------------ 5. audit eod
print("\n-- 5. audit eod: post-snapshot rows are neither the 15:35 slot nor strays")
import helm.cli.audit_cmd as au                            # noqa: E402
def day(d, exits):
    c = sqlite3.connect(COPY)
    for hh in ("10:00", "12:30", "15:15"):
        c.execute("insert into agent_runs (agent, started_at, finished_at, attempted, journaled, failed) "
                  "values (?,?,?,?,?,0)", (AR.AGENT_SNAPSHOT, "%sT%s:02" % (d, hh), "%sT%s:30" % (d, hh), 80, 80))
    for hhmm, notes in exits:
        c.execute("insert into agent_runs (agent, started_at, finished_at, attempted, journaled, failed, notes) "
                  "values (?,?,?,?,?,0,?)", (AR.AGENT_EXITS, "%sT%s:05" % (d, hhmm), "%sT%s:50" % (d, hhmm), 60, 1, notes))
    c.commit(); c.close()
    a = au.Audit(d)
    a.is_today = False
    a.load_runs()
    a.machine_liveness()
    a.check_slots()
    return a
a = day("2026-01-05", [("10:07", "after snapshot 10:00; held 59"), ("12:37", "after snapshot 12:30; held 59"),
                        ("15:35", "held 58")])
res = {r["name"]: r for r in a.results}
ok("15:35 slot PASS from the scheduled run", res.get("slot: paper exits 15:35", {}).get("status") == au.PASS
   and "15:35" in res["slot: paper exits 15:35"]["detail"], res.get("slot: paper exits 15:35"))
ok("post-snapshot runs reported as PASS", res.get("post-snapshot: paper exits", {}).get("status") == au.PASS)
ok("no 'slot timing: paper exits' FAIL for the 10:07 / 12:37 passes", "slot timing: paper exits" not in res,
   res.get("slot timing: paper exits"))
a = day("2026-01-06", [("10:07", "after snapshot 10:00"), ("15:22", "after snapshot 14:59")])
res = {r["name"]: r for r in a.results}
ok("a missed 15:35 is NOT covered by a 15:22 post-snapshot pass",
   res.get("slot: paper exits 15:35", {}).get("status") != au.PASS, res.get("slot: paper exits 15:35"))
a = day("2026-01-07", [("10:07", "after snapshot 10:00"), ("15:35", None), ("19:02", None)])
res = {r["name"]: r for r in a.results}
ok("control: a real launchd catch-up (19:02, no marker) still FAILs slot timing",
   res.get("slot timing: paper exits", {}).get("status") == au.FAIL)
try:
    ag = au.Audit("2026-01-06"); ag.is_today = False; ag.load_runs(); ag.machine_liveness()
    ex = ag.build_agents(False)
    ex = ex.get("com.helm.paper.exits") if isinstance(ex, dict) else None
    if ex is None:
        ex = ag.__dict__.get("agents", {}).get("com.helm.paper.exits")
    posts = [r for r in (ex or {}).get("runs", []) if r.get("post_snapshot")]
    ok("agents brief: post-snapshot runs carry no slot, and 15:35 is still missing",
       len(posts) == 2 and all(r["slot"] is None for r in posts) and "15:35" in ex.get("missing_slots", []),
       (posts, ex and ex.get("missing_slots")))
except Exception as e:
    ok("agents brief builds", False, "%s: %s" % (type(e).__name__, e))

ok("IB connect never attempted", not IB_ATTEMPTS, IB_ATTEMPTS)
ok("helm.config DB_PATH is the copy throughout", str(DB_PATH) == COPY)
print("\n%s -- %d checks, %d failed" % ("PASS" if not FAILS else "FAIL", N, len(FAILS)))
for f in FAILS:
    print("  FAIL: " + f)
sys.exit(1 if FAILS else 0)
