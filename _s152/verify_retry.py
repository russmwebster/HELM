"""s152 (2026-11-05): verify the snapshot-retry change -- the Book of Books' retry wrapper.

  check_cmd.retry_env / RETRY_NOTE   a retry (try 2, 3 ...) ends its run note with
                                     "retry: try N, first started ..."
  advice.write_files(slot_at=)       a retry is labelled with its scheduled slot; files' layout unchanged
  audit_cmd                          a retried slot is timed from the scheduled start and judged
                                     by its last try; earlier tries set aside, not counted lost

Run in the device VM:  TMPDIR=/tmp HELM_ROOT=$HOME/mnt/helm PYTHONPATH=/tmp/pylib \
    python3 -B _s152/verify_retry.py [NEWTREE] [OLDTREE]
NEWTREE defaults to the repo; OLDTREE (built from the .bak-20261105-s152 files) to /tmp/s152old.
Database copies only (VACUUM INTO /tmp); the live helm.db is opened read-only. Prints which code it loaded.
"""
import json, os, shutil, sqlite3, subprocess, sys, tempfile
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEW = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else REPO
OLD = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else "/tmp/s152old"
LIVE = os.path.join(REPO, "data", "helm.db")
WORK = tempfile.mkdtemp(prefix="s152v_", dir="/tmp")
FAILS = []
def ok(cond, what):
    print(("PASS " if cond else "FAIL ") + what)
    if not cond: FAILS.append(what)

def build_old():
    if os.path.exists(OLD): shutil.rmtree(OLD)
    os.makedirs(OLD)
    shutil.copytree(os.path.join(REPO, "helm"), os.path.join(OLD, "helm"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*"))
    shutil.copy2(os.path.join(REPO, "helm.py"), OLD)
    for f in ("helm/cli/check_cmd.py", "helm/cli/audit_cmd.py", "helm/advice.py"):
        shutil.copy2(os.path.join(REPO, f + ".bak-20261105-s152"), os.path.join(OLD, f))
    os.symlink(os.path.join(REPO, "logs"), os.path.join(OLD, "logs"))
    os.makedirs(os.path.join(OLD, "data"))

def copy_db(name):
    p = os.path.join(WORK, name + ".db")
    src = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
    src.execute("VACUUM INTO ?", (p,)); src.close()
    return p

def audit(tree, db, date):
    env = dict(os.environ, HELM_DB=db, HELM_ROOT=tree, PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=tree + os.pathsep + os.environ.get("PYTHONPATH", ""))
    out = subprocess.run([sys.executable, "-B", "-c",
        "import helm.cli.audit_cmd as A, sys; print('LOADED', A.__file__, file=sys.stderr); "
        "sys.argv=['helm','eod','--date','%s','--json','--no-report','--no-notify']; A.run()" % date],
        cwd=tree, env=env, capture_output=True, text=True)
    loaded = [l for l in out.stderr.splitlines() if l.startswith("LOADED")]
    txt = out.stdout[out.stdout.index("\n{") + 1:] if not out.stdout.startswith("{") else out.stdout
    try:
        return json.loads(txt), (loaded[0] if loaded else "?")
    except Exception:
        print(out.stdout[-2000:], out.stderr[-2000:]); raise

def res(j, name):
    return [r for r in j["results"] if r["name"] == name]

def snap_agent(j):
    return j["agents"]["com.helm.snapshot.daily"]

def strip_new(j):
    j = json.loads(json.dumps(j))
    e = snap_agent(j)
    e.pop("superseded_tries", None)
    for a in j["agents"].values():
        a.pop("superseded_tries", None)
        for r in a.get("runs", []):
            r.pop("retry_attempt", None); r.pop("first_start", None)
    j.pop("run_at", None)
    return j

DAY = "2026-11-02"           # a clean day: 10:00, 12:30, 15:15 each read 7 of 7
NOTE = "retry: try %d, first started %s"
def scenario(name, edits):
    db = copy_db(name)
    c = sqlite3.connect(db)
    for sql, args in edits:
        c.execute(sql, args)
    c.commit(); c.close()
    return db
def run_row(start, att, jn, notes=None, status="OK"):
    return ("INSERT INTO agent_runs (agent, started_at, finished_at, slot, attempted, journaled, failed, status, notes) "
            "VALUES ('com.helm.snapshot.daily', ?, ?, NULL, ?, ?, 0, ?, ?)", (start, start[:17] + "40", att, jn, status, notes))
FAIL_1515 = ("UPDATE agent_runs SET journaled=0, status='EMPTY', notes='not-journaled (7): x' "
             "WHERE agent='com.helm.snapshot.daily' AND started_at LIKE ?", (DAY + "T15:15%",))
DROP_1515 = ("DELETE FROM agent_runs WHERE agent='com.helm.snapshot.daily' AND started_at LIKE ?", (DAY + "T15:15%",))
F = DAY + "T15:15:00"

def main():
    build_old()
    print("NEW tree:", NEW); print("OLD tree:", OLD); print("work:", WORK)

    # ---- 1. retry_env and the note round trip -------------------------------------------------
    sys.path.insert(0, NEW)
    os.environ.setdefault("HELM_DB", copy_db("unit"))
    import importlib
    cc = importlib.import_module("helm.cli.check_cmd")
    au = importlib.import_module("helm.cli.audit_cmd")
    adv = importlib.import_module("helm.advice")
    print("LOADED", cc.__file__, au.__file__, adv.__file__)
    st = DAY + "T15:25:05.1"
    E = lambda **k: dict({"RETRY_NAME": "helm-snapshot", "RETRY_ATTEMPT": "2", "RETRY_FIRST_START": F}, **k)
    ok(cc.retry_env(st, {}) is None, "retry_env: a manual run (no variables) is not a retry")
    ok(cc.retry_env(st, E()) == (2, F), "retry_env: try 2 read")
    ok(cc.retry_env(st, E(RETRY_ATTEMPT="1")) == (1, F), "retry_env: the scheduled run is try 1")
    ok(cc.retry_env(st, E(RETRY_NAME="other")) is None, "retry_env: another job's variables ignored")
    ok(cc.retry_env(st, E(RETRY_ATTEMPT="x")) is None, "retry_env: a bad attempt ignored")
    ok(cc.retry_env(st, E(RETRY_FIRST_START="soon")) is None, "retry_env: a bad first start ignored")
    ok(cc.retry_env(st, E(RETRY_FIRST_START=DAY + "T15:30:00")) is None, "retry_env: a first start after the run ignored")
    ok(cc.retry_env(st, E(RETRY_FIRST_START=DAY + "T14:00:00")) is None, "retry_env: a first start over an hour back ignored")
    ok(cc.retry_env(st, E(RETRY_FIRST_START=DAY + "T15:24:30")) == (2, DAY + "T15:24:30"), "retry_env: 35 s earlier is fine")
    note = "not-journaled (2): A; B | " + cc.RETRY_NOTE % (3, F)
    a, f, rest = au._split_retry(note, st)
    ok((a, f, rest) == (3, F, "not-journaled (2): A; B"), "audit reads the note check_cmd writes, and the rest is untouched")
    ok(au._split_retry(cc.RETRY_NOTE % (2, F), st)[2] is None, "a note that is only the marker leaves no note")
    ok(au._split_retry("not-journaled (1): A", st) == (None, None, "not-journaled (1): A"), "an ordinary note is unchanged")
    ok(au._split_retry(cc.RETRY_NOTE % (2, DAY + "T13:00:00"), st)[0] is None, "audit ignores a stale first start too")

    # ---- 2. the snapshot writes the marker (IBKR and the calendar stood in) -----------------------
    import helm.market_calendar as mc, helm.exit_alert as xa, helm.exit_flags as xf
    mc.agent_should_run = lambda *a, **k: (True, "test")
    xa.run_post_snapshot = lambda *a, **k: None
    xf.scan = lambda *a, **k: None
    xf.settle_closed = lambda *a, **k: None
    cc.check_one = lambda *a, **k: None          # reads nothing: journaled 0, as on 11-03
    db = sqlite3.connect("file:%s?mode=ro" % os.environ["HELM_DB"], uri=True)
    acct = db.execute("SELECT account_id FROM positions WHERE status='OPEN' AND book='REAL' LIMIT 1").fetchone()[0]
    db.close()
    cc.get_active_account = lambda: acct
    os.environ["HELM_PAPER_EXITS_ON"] = ""
    def last_note():
        d = sqlite3.connect(os.environ["HELM_DB"])
        r = d.execute("SELECT notes, attempted, journaled FROM agent_runs WHERE agent='com.helm.snapshot.daily' "
                      "ORDER BY id DESC LIMIT 1").fetchone(); d.close(); return r
    from datetime import datetime
    now = datetime.now().replace(microsecond=0)
    for k in ("RETRY_NAME", "RETRY_ATTEMPT", "RETRY_FIRST_START"): os.environ.pop(k, None)
    cc.cmd_snapshot(["--all", "--silent"])
    n0 = last_note()
    ok(n0[0] is None or "retry:" not in n0[0], "snapshot without the wrapper: no marker (%r)" % (n0[0] or "")[:60])
    os.environ.update(RETRY_NAME="helm-snapshot", RETRY_ATTEMPT="1", RETRY_FIRST_START=now.isoformat())
    cc.cmd_snapshot(["--all", "--silent"])
    ok("retry:" not in (last_note()[0] or ""), "snapshot as try 1: no marker")
    os.environ.update(RETRY_ATTEMPT="2")
    cc.cmd_snapshot(["--all", "--silent"])
    n2 = last_note()
    ok((n2[0] or "").endswith(cc.RETRY_NOTE % (2, now.isoformat())) and n2[0].startswith("not-journaled ("),
       "snapshot as try 2: marker last, after the not-journaled names (%s)" % (n2[0] or "")[-60:])
    a, f, rest = au._split_retry(n2[0], datetime.now().isoformat())
    ok(a == 2 and rest == n0[0], "the audit strips it back to exactly the note try 1 wrote")
    for k in ("RETRY_NAME", "RETRY_ATTEMPT", "RETRY_FIRST_START"): os.environ.pop(k, None)

    # ---- 3. advice: the slot label and the files' layout -----------------------------------------
    outd = os.path.join(WORK, "adv"); os.makedirs(outd)
    os.environ["HELM_ADVICE_DIR"] = outd
    snap = {"started": DAY + "T15:58:05", "finished": DAY + "T15:58:40", "attempted": 7, "journaled": 7}
    s_plain = adv.write_files(snapshot=snap, source="helm snapshot")
    ok(s_plain["slot"] is None, "advice: a run at 15:58 with no first start has no slot (as before)")
    s_retry = adv.write_files(snapshot=snap, source="helm snapshot", slot_at=F)
    ok(s_retry["slot"] == "15:15", "advice-status: a run at 15:58 given the 15:15 first start is labelled 15:15")
    j = json.load(open(os.path.join(outd, "advice.json")))
    ok(j["slot"] == "15:15", "advice.json: labelled 15:15")
    ok(sorted(s_plain) == sorted(s_retry) and j["snapshot"] == snap, "advice files: same keys either way; snapshot block unchanged")
    snap2 = dict(snap, started=DAY + "T15:16:00")
    ok(adv.write_files(snapshot=snap2, source="helm snapshot")["slot"] == "15:15", "advice: an on-time run still 15:15")

    # ---- 4. the audit, old code vs new, on day copies -------------------------------------------
    A = scenario("A_base", [])
    jn, ln = audit(NEW, A, DAY); jo, lo = audit(OLD, A, DAY)
    print(ln); print(lo)
    ok(NEW in ln and OLD in lo, "each audit run loaded its own tree")
    ok(strip_new(jn) == strip_new(jo), "A (no retries): new audit identical to old, apart from the new empty fields")
    ok(jn["severity"] == "PASS", "A: the clean day is PASS (%s)" % jn["severity"])

    B = scenario("B_retry10", [FAIL_1515, run_row(DAY + "T15:25:05", 7, 7, NOTE % (2, F))])
    jn, _ = audit(NEW, B, DAY); jo, _ = audit(OLD, B, DAY)
    ok(jo["severity"] == "LOST", "B old: a failed 15:15 retried at 15:25 reads LOST (%s)" % jo["severity"])
    ok(jn["severity"] == "PASS", "B new: PASS -- the slot is judged by its last try (%s: %s)" % (jn["severity"], jn["severity_why"][:80]))
    sl = res(jn, "slot: snapshot 15:15")
    ok(sl and sl[0]["status"] == "PASS" and "try 2" in sl[0]["detail"] and "15:15 0/7" in sl[0]["evidence"],
       "B new: slot line names the try and the set-aside 15:15 0/7 (%s | %s)" % (sl and sl[0]["detail"], sl and sl[0]["evidence"]))
    cov = res(jn, "reading coverage")
    ok(cov and cov[0]["status"] == "PASS", "B new: reading coverage PASS")
    e = snap_agent(jn)
    ok(e.get("outcome") == "COMPLETE" and len(e["superseded_tries"]) == 1, "B new: brief COMPLETE, one try set aside (%s)" % e.get("outcome"))
    r = [x for x in e["runs"] if x["started_at"].startswith(DAY + "T15:25")][0]
    ok(r["slot"] == "15:15" and r["drift_min"] == 0 and not r["mistimed"] and r["retry_attempt"] == 2,
       "B new: the 15:25 run is slot 15:15, drift 0, try 2")

    tries = [run_row(DAY + "T15:%02d:05" % (15 + 5 * k), 7, 0, "not-journaled (7): x | " + NOTE % (k + 1, F), "EMPTY")
             for k in range(1, 6)]
    C = scenario("C_retry30", [FAIL_1515] + tries + [run_row(DAY + "T15:45:05", 7, 7, NOTE % (7, F))])
    jn, _ = audit(NEW, C, DAY); jo, _ = audit(OLD, C, DAY)
    ok(any(x["status"] == "FAIL" for x in res(jo, "slot timing: snapshot")), "C old: a try at 15:45 is a slot-timing FAIL")
    ok(not res(jn, "slot timing: snapshot"), "C new: no slot-timing FAIL for tries 2-7")
    ok(jn["severity"] == "PASS" and len(snap_agent(jn)["superseded_tries"]) == 6,
       "C new: PASS, six tries set aside (%s)" % jn["severity"])

    D = scenario("D_late", [DROP_1515, run_row(DAY + "T15:38:05", 7, 0, "not-journaled (7): x", "EMPTY"),
                            run_row(DAY + "T15:43:05", 7, 7, NOTE % (2, DAY + "T15:38:00"))])
    jn, _ = audit(NEW, D, DAY)
    ok(any(x["status"] == "FAIL" for x in res(jn, "slot timing: snapshot")),
       "D new: a scheduled run that itself fired at 15:38 is still a slot-timing FAIL")
    r = [x for x in snap_agent(jn)["runs"] if x["started_at"].startswith(DAY + "T15:43")][0]
    ok(r["mistimed"] and r["drift_min"] == 23, "D new: its retry is timed from 15:38 -- drift 23, mistimed")

    E_ = scenario("E_allfail", [FAIL_1515] + tries)
    jn, _ = audit(NEW, E_, DAY)
    ok(jn["severity"] == "LOST" and "15:15 (try 6 at 15:40)" in jn["severity_why"],
       "E new: every try failed -- still LOST, named by its slot (%s)" % jn["severity_why"][:70])

    G = scenario("G_manual", [run_row(DAY + "T15:45:05", 7, 7, None)])
    jn, _ = audit(NEW, G, DAY); jo, _ = audit(OLD, G, DAY)
    ok(strip_new(jn) == strip_new(jo), "G: a manual run at 15:45 is judged exactly as before")
    H = scenario("H_stale", [run_row(DAY + "T15:45:05", 7, 7, NOTE % (2, DAY + "T13:00:00"))])
    jh, _ = audit(NEW, H, DAY)
    ok(any(x["status"] == "FAIL" for x in res(jh, "slot timing: snapshot")),
       "H new: a marker with a first start over an hour back is ignored (timed at 15:45)")

    live = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
    ok(live.execute("SELECT COUNT(*) FROM agent_runs WHERE notes LIKE '%retry: try%'").fetchone()[0] == 0,
       "the live database was not written")
    live.close()
    print("\n%d failed" % len(FAILS)); sys.exit(1 if FAILS else 0)

if __name__ == "__main__":
    main()
