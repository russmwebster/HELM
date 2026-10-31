#!/usr/bin/env python3
"""_s141/verify_snapshot_ids.py -- prove the snapshot's own client numbers (W103).

Runs a real `helm snapshot` (REAL book) against a fresh VACUUM INTO copy of
helm.db, four times, each in its own process, with every IBKR connect logged:

  old      the code before s141 (.bak-20261031-s141), while a blocker holds
           11, 12, 13                     -> expect refusals (the defect)
  new      today's code, blocker holding 11, 12, 13
                                          -> expect every connect on 31-33, none refused
  pert     today's code with SNAPSHOT_IDS pointed back at 11-13 in memory,
           blocker holding 11-13          -> expect refusals (the test can fail)
  control  today's code, no blocker (run LAST)
                                          -> expect every connect on 31-33, none refused

Safety: the live database is only read (VACUUM INTO); HELM_DB points at the
copy; the exit-alert notification is switched off; the market-calendar check
is bypassed only inside this process.  The blocker holds 11-13 for about a
minute per case -- a board action in that minute could be refused, nothing
else (the sampler uses 21, the watchdog its own).  Don't run it inside a
snapshot window.

Usage (on the Mac, IB Gateway up):
  cd ~/Projects/helm && python3 _s141/verify_snapshot_ids.py
"""
import json, os, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE_DB = REPO / "data" / "helm.db"
BLOCK = [11, 12, 13]

BLOCKER = r'''
import sys, time
from ib_insync import IB
held = []
for cid in %r:
    ib = IB()
    try:
        ib.connect("127.0.0.1", 4002, clientId=cid, readonly=True, timeout=10)
        held.append(ib)
    except Exception as e:
        print("BLOCKER-FAILED %%d %%s" %% (cid, e), flush=True); sys.exit(2)
print("HELD", flush=True)
time.sleep(600)
''' % (BLOCK,)

def case_main(case, db, out):
    os.environ["HELM_DB"] = db
    os.environ.setdefault("HELM_ROOT", str(REPO))
    sys.path.insert(0, str(REPO))
    import importlib.machinery as ilm, importlib.util as ilu
    if case == "old":                       # load the pre-s141 modules in their place
        for name, rel in (("helm.ibkr", "helm/ibkr.py.bak-20261031-s141"),
                          ("helm.cli.check_cmd", "helm/cli/check_cmd.py.bak-20261031-s141")):
            import helm, helm.cli         # packages first
            loader = ilm.SourceFileLoader(name, str(REPO / rel))
            spec = ilu.spec_from_loader(name, loader)
            mod = ilu.module_from_spec(spec); sys.modules[name] = mod
            loader.exec_module(mod)
    import helm.ibkr as ibkr
    import helm.cli.check_cmd as cc
    loaded = [ibkr.__file__ if hasattr(ibkr, "__file__") else "?", cc.__file__]
    if case == "pert":
        ibkr.SNAPSHOT_IDS.update({"check": 11, "stock": 12, "option": 13})
    import helm.market_calendar as mc
    mc.agent_should_run = lambda now=None: (True, "verify (s141)")
    import helm.exit_alert as xa
    xa._notify = lambda *a, **k: None
    from ib_insync import IB
    log = []
    real_connect = IB.connect
    def logged(self, *a, **k):
        cid = k.get("clientId", a[2] if len(a) > 2 else None)
        try:
            r = real_connect(self, *a, **k); log.append([cid, "ok", ""]); return r
        except Exception as e:
            log.append([cid, "refused", str(e)[:80]]); raise
    IB.connect = logged
    sys.argv = ["helm", "--real"]
    t0 = time.time()
    cc.run_snapshot()
    Path(out).write_text(json.dumps({"case": case, "loaded": loaded, "log": log,
                                     "secs": round(time.time() - t0)}))

def fresh_copy(tmp, name):
    dst = Path(tmp) / f"{name}.db"
    src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    src.execute("VACUUM INTO ?", (str(dst),)); src.close()
    return str(dst)

def run_case(case, hold, tmp):
    db = fresh_copy(tmp, case); out = Path(tmp) / f"{case}.json"
    blk = None
    if hold:
        blk = subprocess.Popen([sys.executable, "-c", BLOCKER], stdout=subprocess.PIPE, text=True)
        line = blk.stdout.readline().strip()
        if line != "HELD":
            blk.kill(); sys.exit(f"ABORT: the blocker could not hold 11-13 ({line}) -- "
                                 "something else is using them; try again in a minute.")
    try:
        r = subprocess.run([sys.executable, "-B", __file__, "--case", case, db, str(out)],
                           capture_output=True, text=True, timeout=900)
    finally:
        if blk: blk.kill(); blk.wait()
    if not out.exists():
        print(r.stdout[-1500:], r.stderr[-1500:]); sys.exit(f"ABORT: case {case} wrote no result")
    res = json.loads(out.read_text())
    ids = sorted({c for c, _, _ in res["log"]})
    refused = [c for c, s, _ in res["log"] if s == "refused"]
    n = len(res["log"])
    print(f"  {case:8} connects {n:3}  ids {ids}  refused {len(refused)}  ({res['secs']}s)")
    print(f"           loaded: {', '.join(Path(p).name for p in res['loaded'])}")
    return n, ids, refused

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--case":
        case_main(sys.argv[2], sys.argv[3], sys.argv[4]); return
    tmp = tempfile.mkdtemp(prefix="helm-s141-ids-")
    print(f"copies in {tmp}")
    res = {}
    for case, hold in (("old", True), ("new", True), ("pert", True), ("control", False)):
        res[case] = run_case(case, hold, tmp)
        time.sleep(3)
    ok = True
    def check(cond, msg):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + msg); ok &= bool(cond)
    print()
    n, ids, ref = res["old"];     check(n and ref, "old code, 11-13 held: the snapshot's connects are refused (defect reproduced)")
    n, ids, ref = res["new"];     check(n and not ref and set(ids) <= {31, 32, 33}, "new code, 11-13 held: every connect on 31-33, none refused")
    n, ids, ref = res["pert"];    check(n and ref, "perturbed (31-33 pointed back at 11-13): refusals again -- the test can fail")
    n, ids, ref = res["control"]; check(n and not ref and set(ids) <= {31, 32, 33}, "control, nothing held: every connect on 31-33, none refused")
    print("\nALL PASS" if ok else "\nNOT PASSED -- send me the output")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
