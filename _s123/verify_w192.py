"""_s123/verify_w192.py -- W192 (bear call / bull put spreads managed at 21 DTE,
not 7) on FRESH database copies. Never opens the live database for writing;
no network (nothing here fetches -- the verdicts are computed on fixed marks).

    python3 _s123/verify_w192.py

  1. the apply  -- dry run changes nothing; apply changes exactly the two rows;
                   every other strategy_settings row identical; re-run is a no-op
  2. verdicts   -- decision.evaluate at 5 / 15 / 25 DTE, before and after, for
                   both spreads, and the controls that must NOT move (iron
                   condor, covered call, CSP)
  3. readers    -- rule_read and the entry-runway floor read the new value; the
                   entry window stays 30-45 DTE
  4. the guard  -- with an open spread inside 7-21 DTE the apply refuses unless
                   --force, and lists it
  5. defaults   -- setup.DEFAULTS for a new account carry 21 for the two only
"""
import contextlib, io, os, sqlite3, subprocess, sys, tempfile, uuid
from datetime import date, timedelta
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
SRC = os.path.join(ROOT, "data", "helm.db")
TMP = tempfile.mkdtemp(prefix="w192_")


def fresh(name):
    p = os.path.join(TMP, name)
    s = sqlite3.connect("file:" + SRC + "?mode=ro", uri=True)
    s.execute("VACUUM INTO ?", (p,))
    s.close()
    return p


COPY = fresh("a.db")
os.environ["HELM_DB"] = COPY
os.environ["HELM_ROOT"] = ROOT
from helm.config import DB_PATH, get_active_account       # noqa: E402
from helm import decision as D                             # noqa: E402
from helm import rule_read as RR                           # noqa: E402
import helm.cli.open_cmd as oc                             # noqa: E402
from helm.entry_bands import effective_bands              # noqa: E402
assert str(DB_PATH) == COPY, DB_PATH
A = get_active_account()
print("harness: code from %s\n         db copy  %s (fresh VACUUM INTO)\n" % (ROOT, COPY))

N, FAILS = 0, []


def ok(label, cond, detail=""):
    global N
    N += 1
    print(("PASS  " if cond else "FAIL  ") + label + (("   [" + str(detail) + "]") if detail else ""))
    if not cond:
        FAILS.append(label)


def apply(db, *args):
    env = dict(os.environ, HELM_DB=db, HELM_ROOT=ROOT)
    r = subprocess.run([sys.executable, os.path.join(HERE, "w192_apply.py")] + list(args),
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def settings(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    try:
        return {r["strategy"]: dict(r) for r in c.execute(
            "SELECT * FROM strategy_settings WHERE account_id=?", (A,))}
    finally:
        c.close()


def verdict(strategy, days, legs=2):
    exp = (date.today() + timedelta(days=days)).isoformat()
    pos = NS(net_premium=100.0, account_id=A, strategy=strategy, max_profit=100.0, id="T")
    ls = [NS(id="L%d" % i, direction=("SHORT" if i == 0 else "LONG"), open_price=1.0,
             contracts=1, multiplier=100, expiration=exp) for i in range(legs)]
    marks = {l.id: 1.0 for l in ls}          # flat: no profit target in play
    return D.evaluate(pos, ls, marks)[0]


SPREADS = ("BEAR_CALL_SPREAD", "BULL_PUT_SPREAD")
CONTROLS = (("IRON_CONDOR", 4), ("COVERED_CALL", 1), ("CSP", 1))

# ---------------------------------------------------------------- before
print("-- 0. before (the live setting, on the copy)")
pre = {(s, d): verdict(s, d) for s in SPREADS for d in (5, 15, 25)}
pre_c = {(s, d): verdict(s, d, n) for s, n in CONTROLS for d in (5, 15, 25)}
ok("spreads at 7: 5 DTE manages, 15 and 25 hold",
   all(pre[(s, 5)] == "DTE_MANAGE" and pre[(s, 15)] is None and pre[(s, 25)] is None for s in SPREADS), pre)

# ---------------------------------------------------------------- 1. apply
print("\n-- 1. the apply")
s0 = settings(COPY)
rc, out = apply(COPY, "--dry-run")
ok("dry run: exit 0, reports 0 open spreads in 7-21, changes nothing",
   rc == 0 and "between 7 and 21 DTE: 0" in out and settings(COPY) == s0, out[-300:])
rc, out = apply(COPY)
s1 = settings(COPY)
ok("apply: 2 rows updated, 'APPLIED'", rc == 0 and "updated 2 row(s)" in out and "W192 APPLIED" in out, out[-300:])
ok("  ... the two spreads now 21, last_modified and notes stamped",
   all(s1[s]["dte_exit_threshold"] == 21 and "W192" in (s1[s]["notes"] or "") for s in SPREADS))
ok("  ... every other row byte-for-byte unchanged (%d rows)" % (len(s0) - 2),
   all(s1[k] == v for k, v in s0.items() if k not in SPREADS) and set(s1) == set(s0))
ok("  ... nothing else in the two rows moved (review threshold stays 21, target stays)",
   all({k: v for k, v in s1[s].items() if k not in ("dte_exit_threshold", "last_modified", "notes")}
       == {k: v for k, v in s0[s].items() if k not in ("dte_exit_threshold", "last_modified", "notes")}
       for s in SPREADS))
rc, out = apply(COPY)
ok("re-run: a no-op, 'already applied'", rc == 0 and "updated 0 row(s) -- already applied" in out
   and settings(COPY) == s1, out[-200:])

# ---------------------------------------------------------------- 2. verdicts
print("\n-- 2. decision.evaluate after")
post = {(s, d): verdict(s, d) for s in SPREADS for d in (5, 15, 21, 22, 25)}
ok("spreads at 21: 5, 15 and 21 DTE manage; 22 and 25 hold",
   all(post[(s, 5)] == post[(s, 15)] == post[(s, 21)] == "DTE_MANAGE"
       and post[(s, 22)] is None and post[(s, 25)] is None for s in SPREADS), post)
post_c = {(s, d): verdict(s, d, n) for s, n in CONTROLS for d in (5, 15, 25)}
ok("controls unchanged: iron condor, covered call, CSP give the same verdict at 5/15/25",
   post_c == pre_c, post_c)
ok("  ... and the controls really differ from each other (covered call still 7: 15 holds)",
   post_c[("COVERED_CALL", 15)] is None and post_c[("IRON_CONDOR", 15)] == "DTE_MANAGE")

# ---------------------------------------------------------------- 3. readers
print("\n-- 3. the other readers")
ok("rule_read reads 21 for both", all(RR._thresholds({"account_id": A, "strategy": s})[1] == 21
                                      for s in SPREADS))
oc._DTE_EXIT_CACHE.clear()
floors = {}
for s in SPREADS:
    b, _ = effective_bands(s, oc.STRATEGY_CONFIG[s])
    floors[s] = (oc._manage_threshold(s), oc._entry_dte_floor(s, b["dte_min"]), b["dte_max"])
ok("entry runway: threshold 21, entry floor 30, max 45 -- the entry window does not move",
   all(v == (21, 30, 45) for v in floors.values()), floors)

# ---------------------------------------------------------------- 4. the guard
print("\n-- 4. the guard: an open spread inside 7-21 DTE")
C2 = fresh("b.db")
c = sqlite3.connect(C2)
pid = "TEST-W192-" + uuid.uuid4().hex[:6]
exp = (date.today() + timedelta(days=15)).isoformat()
c.execute("insert into positions (id, account_id, strategy, ticker, status, opened_at, total_contracts, book) "
          "values (?,?,?,?,?,?,?,?)", (pid, A, "BEAR_CALL_SPREAD", "ZZT", "OPEN", "2026-09-01T10:00:00", 1, "PAPER"))
for role, d in (("SHORT_CALL", "SHORT"), ("LONG_CALL", "LONG")):
    c.execute("insert into legs (id, position_id, leg_role, direction, contracts, multiplier, open_price, "
              "open_date, status, expiration, strike, option_type) values (?,?,?,?,?,?,?,?,?,?,?,?)",
              (uuid.uuid4().hex, pid, role, d, 1, 100, 1.0, "2026-09-01", "OPEN", exp, 100.0, "CALL"))
c.commit(); c.close()
s2 = settings(C2)
rc, out = apply(C2)
ok("refuses (exit 2), lists ZZT at 15 DTE, changes nothing",
   rc == 2 and "ZZT" in out and "would close (paper)" in out and "NOT APPLIED" in out
   and settings(C2) == s2, out[-400:])
rc, out = apply(C2, "--force")
ok("  ... --force applies", rc == 0 and "W192 APPLIED" in out)

# ---------------------------------------------------------------- 5. defaults
print("\n-- 5. new-account defaults")
from helm.cli import setup as SU                           # noqa: E402
ok("setup.DEFAULTS: the two spreads 21",
   all(SU.DEFAULTS[s]["dte_exit_threshold"] == 21 for s in SPREADS))
# every other default exactly as committed (compared with setup.py at HEAD, not
# with the live rows -- the new-account defaults already differ from the live
# account in places, e.g. CSP 7 here vs 21 live; W192 does not touch that)
_head = subprocess.run(["git", "--no-optional-locks", "-C", ROOT, "show", "HEAD:helm/cli/setup.py"],
                       capture_output=True, text=True).stdout
_ns = {}
_blk = _head[_head.index("DEFAULTS = {"):]
exec(compile(_blk[:_blk.index("\n}\n") + 3], "setup@HEAD:DEFAULTS", "exec"), _ns)
_old = _ns["DEFAULTS"]
ok("  ... every other strategy's defaults identical to HEAD; the two differ only in dte_exit_threshold",
   all(SU.DEFAULTS[k] == _old[k] for k in _old if k not in SPREADS) and set(SU.DEFAULTS) == set(_old)
   and all({k: v for k, v in SU.DEFAULTS[s].items() if k != "dte_exit_threshold"}
           == {k: v for k, v in _old[s].items() if k != "dte_exit_threshold"}
           and _old[s]["dte_exit_threshold"] in (7, 21) for s in SPREADS))   # 7 before the W192 commit, 21 after

ok("helm.config DB_PATH is the copy throughout", str(DB_PATH) == COPY)
print("\n%s -- %d checks, %d failed" % ("PASS" if not FAILS else "FAIL", N, len(FAILS)))
for f in FAILS:
    print("  FAIL: " + f)
sys.exit(1 if FAILS else 0)
