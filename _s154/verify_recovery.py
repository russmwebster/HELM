"""s154c: `helm recovery` and "Odds if assigned" on the put cards.

The CLI run as production runs it (python3 helm.py recovery) on fresh DB copies, IB Gateway stood in for
(/tmp/fakeib: refused, or CSCO's saved bars only); the board started as production starts it, old code
(decide .bak-20261108-s154b, thesis.html .bak-20261108-s154c) beside new. Expected card numbers are read from
the recorded JSON here, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s154/verify_recovery.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date, timedelta
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"; W = "/tmp/s154c"
ENG_BAK = {"helm/decide.py": ".bak-20261108-s154b", "helm.py": ".bak-20261108-s154"}
ENG_NEW = ["helm/recovery.py", "helm/cli/recovery_cmd.py"]
PG_BAK = {"templates/thesis.html": ".bak-20261108-s154c"}
ENG_IGN = ("data", "archive", "_s1[0-3]*", "_s14*", "_s150", "_s151", "_s152", "_s153", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records")
PG_IGN = ("*.bak-*", "__pycache__", ".git", "_to_delete")
def fixcfg(dst):
    cfg = dst + "/helm/config.py"; c = open(cfg).read()
    open(cfg, "w").write(re.sub(r"^HELM_PYTHON = .*$", "HELM_PYTHON = %r" % sys.executable, c, flags=re.M))
def tree(src, dst, ign, eng=False):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign))
    if eng:
        fixcfg(dst)
        shutil.copytree(ROOT + "/_s122/px", dst + "/_s122/px")     # the price files the model reads
    return dst
os.makedirs(W + "/home", exist_ok=True)
if len(sys.argv) > 2: NEW_ENG, NEW_PG = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
else: NEW_ENG, NEW_PG = tree(ROOT, W + "/eng-new", ENG_IGN, True), tree(PGROOT, W + "/pg-new", PG_IGN)
OLD_ENG = tree(ROOT, W + "/eng-old", ENG_IGN, True); OLD_PG = tree(PGROOT, W + "/pg-old", PG_IGN)
for f, b in ENG_BAK.items(): shutil.copy2(ROOT + "/" + f + b, OLD_ENG + "/" + f)
for f in ENG_NEW: os.remove(OLD_ENG + "/" + f)
for f, b in PG_BAK.items(): shutil.copy2(PGROOT + "/" + f + b, OLD_PG + "/" + f)
for d, fs in ((NEW_ENG, list(ENG_BAK) + ENG_NEW), (NEW_PG, list(PG_BAK))):
    for f in fs: print("  new", f, hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest()[:12])
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
def fresh(p):
    if os.path.exists(p): os.remove(p)
    sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (p,)); return p
def dump(p):
    c = sqlite3.connect(p); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        h = hashlib.sha256(); h.update(repr([r[1] for r in c.execute('pragma table_info("%s")' % t)]).encode())
        for r in c.execute('select * from "%s" order by rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out
def cli(eng, db, args, fake="down"):
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, COLUMNS="200", FAKEIB=fake,
               PYTHONPATH="/tmp/fakeib:/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out", TMPDIR="/tmp")
    p = subprocess.run([sys.executable, "-B", "helm.py"] + args, cwd=eng, env=env, capture_output=True, text=True, timeout=170)
    return p.returncode, p.stdout + p.stderr
DB = fresh(W + "/copy.db"); before = dump(DB)
cx = sqlite3.connect(DB); cx.row_factory = sqlite3.Row
acct = cx.execute("select id from accounts").fetchone()[0]
PUTS = [(r["id"], r["ticker"]) for r in cx.execute("select p.id, p.ticker from positions p join legs l on l.position_id=p.id "
        "where p.book='REAL' and p.status='OPEN' and p.strategy='CSP' and l.status='OPEN' and l.direction='SHORT' order by p.ticker")]
OTHER = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy!='CSP'")]
cx.close()
PORT = [8910]
def render(eng, pg, db, paths):
    PORT[0] += 1; port = str(PORT[0]); open(W + "/home/.helm_profile", "w").write(acct)
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, PORT=port, HELM_PG_ENGINE="real",
               PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out", TMPDIR="/tmp")
    os.makedirs(W + "/advice-out", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=pg, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = {}
    try:
        for _ in range(120):
            try: urllib.request.urlopen("http://127.0.0.1:%s/health" % port, timeout=2); break
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.5)
        for path in paths:
            try: r = urllib.request.urlopen("http://127.0.0.1:%s%s" % (port, path), timeout=120); out[path] = (r.status, r.read().decode())
            except Exception as e: out[path] = (getattr(e, "code", "ERR"), str(e))
    finally:
        p.terminate(); log = p.communicate(timeout=10)[0].decode()
    return out, log
txt = lambda b: re.sub(r"\s+([.,;)])", r"\1", re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", b))))
norm = lambda b: re.sub(r'\b(c[a-z]|clip|rc|g)[-_]?\d{4,}\b', 'ID', b)
BLOCK = re.compile(r"\{# s154.*?\{% endif %\}", re.S)
# --- 0. help lists the command; self-test
rc, o = cli(NEW_ENG, DB, ["help"]); ok("recovery" in o, "helm help lists recovery")
rc, o = cli(NEW_ENG, DB, ["recovery", "--selftest"]); ok(rc == 0 and "PASS" in o, "helm recovery --selftest passes")
# --- 1. before any run: every put card says 'not run yet' with its command; everything else unchanged
cards = ["/thesis/" + i for i, _ in PUTS]; others = ["/thesis/" + i for i in OTHER] + ["/today"]
o0, _ = render(OLD_ENG, OLD_PG, DB, cards + others); n0, l0 = render(NEW_ENG, NEW_PG, DB, cards + others)
ok(all(n0[p][0] == 200 for p in cards + others) and "Traceback" not in l0, "new board: every card and Today render before any run")
ok(all("Odds if assigned: not run yet — cd ~/Projects/helm && helm recovery %s" % t in txt(n0["/thesis/" + i][1]) for i, t in PUTS),
   "before a run, each put card says 'not run yet' with its own command")
ws = lambda b: re.sub(r">\s+<", "><", re.sub(r"\s+", " ", b))
strip = lambda b: ws(re.sub(r'<div class="dz-p dim">Odds if assigned: not run yet.*?</div>', "", norm(b), flags=re.S))
ok(all(strip(n0[p][1]) == ws(norm(o0[p][1])) for p in cards), "put cards otherwise identical to the old code (whitespace aside)")
if not all(strip(n0[p][1]) == ws(norm(o0[p][1])) for p in cards):
    import difflib
    a, b = ws(norm(o0[cards[0]][1])), strip(n0[cards[0]][1])
    k = next((j for j in range(min(len(a), len(b))) if a[j] != b[j]), min(len(a), len(b)))
    print("    first difference:", repr(a[max(0, k - 80):k + 60]), "|", repr(b[max(0, k - 80):k + 60]))
ok(all(norm(n0[p][1]) == norm(o0[p][1]) for p in others), "PMCC cards and Today identical to the old code")
ok(dump(DB) == before, "rendering wrote nothing")
# --- 2. a run with IB Gateway refused: files only, said; one row a put; only the two new tables
rc, o = cli(NEW_ENG, DB, ["recovery", "--paths", "800", "--force"], "down")
ok(rc == 0 and "IB Gateway not reachable" in o and "Recorded" in o, "run with the gateway refused: says so, records")
after = dump(DB)
ok({k for k in after if after.get(k) != before.get(k)} == {"recovery_runs", "recovery_results", "sqlite_sequence"},
   "only recovery_runs / recovery_results (and the id counter) changed: %s" % sorted(k for k in after if after.get(k) != before.get(k)))
cx = sqlite3.connect(DB); cx.row_factory = sqlite3.Row
rows = {r["position_id"]: r for r in cx.execute("select * from recovery_results where run_id=(select max(id) from recovery_runs)")}
ok(set(rows) == {i for i, _ in PUTS}, "one recorded row for every open put (%d)" % len(rows))
good = {i: json.loads(r["result"]) for i, r in rows.items() if r["result"]}
print("    results:", {t: ("ok" if i in good else rows[i]["error"][:60]) for i, t in PUTS})
cs = [i for i, t in PUTS if t == "CSCO"][0]
ok(cs in good and "implied vol" in good[cs]["fit"]["start_note"], "CSCO without IBKR: closes end 09-25, so the start vol comes from implied vol, labelled")
for i, r in good.items():
    if r["basis"] <= 0 or r["now"]["touch"] != sorted(r["now"]["touch"]): ok(False, "result shape for %s" % r["ticker"])
# --- 3. a run with CSCO's bars: merged, GARCH start vol; the card shows the newest run's numbers
rc, o = cli(NEW_ENG, DB, ["recovery", "CSCO", "--paths", "800", "--force"], "csco")
ok(rc == 0 and "IBKR daily bars to 2026-11-06" in o, "with IBKR bars: CSCO's closes run to 11-06")
r2 = json.loads(cx.execute("select result from recovery_results where position_id=? order by run_id desc limit 1", (cs,)).fetchone()[0])
ok("GARCH" in r2["fit"]["start_note"] and abs(r2["fit"]["start_vol"] - 0.49) < 0.01, "fresh closes: start vol is GARCH's vol now (%.3f)" % r2["fit"]["start_vol"])
n1, l1 = render(NEW_ENG, NEW_PG, DB, cards + others)
ok(all(n1[p][0] == 200 for p in cards + others) and "Traceback" not in l1, "every card renders after the runs")
def expect(r, day_note):
    hz = r["horizons"]; i, y = hz.index(90), hz.index(365); a, c = r["now"], r["calm"]
    return ("Odds if assigned at $%.2f on %s (a model, %s): back to $%.2f on price alone — 3 months %.0f%% · a year %.0f%% (calm: %.0f%% · %.0f%%)."
            % (r["spot"], r["start"][5:], day_note, r["basis"], a["touch"][i], a["touch"][y], c["touch"][i], c["touch"][y]),
            "With covered calls, made whole — 3 months %.0f%% · a year %.0f%% (calm: %.0f%% · %.0f%%)." % (a["whole"][i], a["whole"][y], c["whole"][i], c["whole"][y]),
            "rent in a year about $%.2f a share (${:,.0f})".format(a["rent"][y] * r["shares"]) % a["rent"][y])
t = txt(n1["/thesis/" + cs][1]); e = expect(r2, "run today")
ok(all(x in t for x in e), "CSCO's card shows the newest run's numbers word for word")
if not all(x in t for x in e): print("    card:", t[t.find("Odds if"):][:700], "\n    want:", e)
for i, tk in PUTS:
    if i in good and i != cs:
        ok(all(x in txt(n1["/thesis/" + i][1]) for x in expect(good[i], "run today")), "%s's card shows its run's numbers" % tk)
    elif i not in good:
        ok("Odds if assigned couldn't be worked out" in txt(n1["/thesis/" + i][1]), "%s's card shows its run's error" % tk)
ok(all(norm(n1[p][1]) == norm(o0[p][1]) for p in others), "PMCC cards and Today still identical to the old code")
ok(all(re.sub(r'<div class="dz-p[^"]*">(?:<b>)?Odds if assigned.*?(?=</div>\s*</div>\s*<div><div class="dz-q">Keep vs switch)', "", norm(n1[p][1]), flags=re.S).count("Odds if") == 0 for p in cards),
   "on put cards the new lines are the only addition")
# --- 4. an older run is labelled with its day and the rerun command
cx.execute("update recovery_runs set run_at=?", ((date.today() - timedelta(days=3)).isoformat() + "T10:00:00",)); cx.commit()
n2, _ = render(NEW_ENG, NEW_PG, DB, ["/thesis/" + cs])
t2 = txt(n2["/thesis/" + cs][1]); d3 = (date.today() - timedelta(days=3)).isoformat()[5:]
ok("(a model, run %s)" % d3 in t2 and "cd ~/Projects/helm && helm recovery CSCO" in t2, "a run from an earlier day says its day and the rerun command")
# --- 5. --last shows the latest without a new run
nruns = cx.execute("select count(*) from recovery_runs").fetchone()[0]
rc, o = cli(NEW_ENG, DB, ["recovery", "--last"])
ok(rc == 0 and "CSCO put -- if assigned" in o and cx.execute("select count(*) from recovery_runs").fetchone()[0] == nruns, "--last shows the record, adds no run")
cx.close()
print("\n%d checks, %s" % (n[0], "ALL PASS" if not fails else "%d FAIL" % len(fails)))
sys.exit(1 if fails else 0)
