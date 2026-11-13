"""s157b: the put list's Held column (helm/todayview.py held_on / puts_view, helm/cli/puts_cmd.py, helm-pg templates/today.html).
The latest put run on the copy is set to 11-12's (the one that offered PFE again). Old (.bak-20261113-s157b) vs new.
(Harness built from _s156/verify_today.py.) Was: s156 step 4: Today (helm/todayview.py, helm/decide.py pmcc_read; helm-pg engine_store.today_view, templates/today.html).
(Harness built from verify_pmcc_card.py.) s156 step 3: the PMCC rule rows (helm/advice.py pmcc_rows), the card's data (helm/decide.py decide_pmcc), the
card's ruler (helm/thesis.py) and the open PMCC's new card (helm-pg pmcc_card.html, engine_store.py, app.py).

Board started as production starts it on a fresh DB copy, old code (.bak-20261108-s156) and new side by side from /tmp
copies. Expected numbers come from SQL on the copy, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s156/verify_pmcc_card.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s157h"
ENG_BAK = {"helm/todayview.py": ".bak-20261113-s157b", "helm/cli/puts_cmd.py": ".bak-20261113-s157b"}
ENG_NEW_ONLY = []
PG_BAK = {"templates/today.html": ".bak-20261113-s157b"}
PG_NEW_ONLY = []
ENG_IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records")
PG_IGN = ("*.bak-*", "__pycache__", ".git", "_to_delete")
def fixcfg(dst):
    cfg = dst + "/helm/config.py"
    if os.path.exists(cfg):
        c = open(cfg).read()
        open(cfg, "w").write(re.sub(r"^HELM_PYTHON = .*$", "HELM_PYTHON = %r" % sys.executable, c, flags=re.M))
def tree(src, dst, ign):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign)); fixcfg(dst); return dst
os.makedirs(W + "/home", exist_ok=True)
if len(sys.argv) > 2:
    NEW_ENG, NEW_PG = tree(os.path.abspath(sys.argv[1]), W + "/eng-new2", ENG_IGN), tree(os.path.abspath(sys.argv[2]), W + "/pg-new2", PG_IGN)   # copied, so the config fix applies
else:
    NEW_ENG, NEW_PG = tree(ROOT, W + "/eng-new", ENG_IGN), tree(PGROOT, W + "/pg-new", PG_IGN)
OLD_ENG = tree(ROOT, W + "/eng-old", ENG_IGN)
for f, b in ENG_BAK.items(): shutil.copy(ROOT + "/" + f + b, OLD_ENG + "/" + f)
OLD_PG = tree(PGROOT, W + "/pg-old", PG_IGN)
for f, b in PG_BAK.items(): shutil.copy(PGROOT + "/" + f + b, OLD_PG + "/" + f)
for f in PG_NEW_ONLY: os.remove(OLD_PG + "/" + f)
for f in ENG_NEW_ONLY: os.remove(OLD_ENG + "/" + f)
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for d, fs in ((NEW_ENG, list(ENG_BAK) + ENG_NEW_ONLY), (NEW_PG, list(PG_BAK) + PG_NEW_ONLY)):
    for f in fs: print("   ", f, hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest()[:12])
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
DB = W + "/copy.db"
if os.path.exists(DB): os.remove(DB)
sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (DB,))
def dump(p):
    c = sqlite3.connect(p); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        h = hashlib.sha256(); h.update(repr([r[1] for r in c.execute('pragma table_info("%s")' % t)]).encode())
        for r in c.execute('select * from "%s" order by rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out
cz = sqlite3.connect(DB)
for (rid,) in cz.execute("select id from put_runs where run_at > '2026-11-12 23:59'").fetchall():
    cz.execute("delete from put_candidates where run_id=?", (rid,)); cz.execute("delete from put_runs where id=?", (rid,))
cz.commit(); cz.close()
before = dump(DB)
cx = sqlite3.connect(DB); cx.row_factory = sqlite3.Row
PMCC = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy='PMCC' order by ticker")]
OTHER = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy!='PMCC' order by ticker")]
CLOSED = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status!='OPEN' and strategy in "
                                      "('PMCC','DIAGONAL') order by closed_at desc limit 3")] + \
         [r["id"] for r in cx.execute("select id from positions where book='REAL' and status!='OPEN' and strategy='CSP' "
                                      "order by closed_at desc limit 2")]
PORT = [9060]
def render(eng, pg, paths):
    PORT[0] += 1; port = str(PORT[0])
    open(W + "/home/.helm_profile", "w").write(cx.execute("select id from accounts").fetchone()[0])
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=DB, PORT=port, HELM_PG_ENGINE="real",
               PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out")
    os.makedirs(W + "/advice-out", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=pg, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = {}
    try:
        for _ in range(120):
            try: urllib.request.urlopen("http://127.0.0.1:%s/health" % port, timeout=2); break
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.5)
        for path in paths:
            try:
                r = urllib.request.urlopen("http://127.0.0.1:%s%s" % (port, path), timeout=120); out[path] = (r.status, r.read().decode())
            except Exception as e: out[path] = (getattr(e, "code", "ERR"), str(e))
    finally:
        p.terminate(); log = p.communicate(timeout=10)[0].decode()
    return out, log


ALL = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' order by ticker")]
paths = ["/thesis/" + i for i in ALL] + ["/today", "/book", "/watchlist", "/reference"]
o, lo = render(OLD_ENG, OLD_PG, paths); nw, ln = render(NEW_ENG, NEW_PG, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "every page renders, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "no tracebacks in the board's log")
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
same = [p for p in paths if p != "/today"]
diff = [p for p in same if norm(o[p][1]) != norm(nw[p][1])]
ok(not diff, "every page other than Today identical old vs new (%d pages): %s" % (len(same), diff))
t_new = nw["/today"][1]
strip = re.sub(r'<th class="t">Held</th>', "", t_new)
strip = re.sub(r'(<td class="tk">.*?</td>)<td class="t">[^<]*</td>', r"\1", strip)
strip = strip.replace('colspan="13">Best that fit', 'colspan="12">Best that fit')
ok(norm(strip) == norm(o["/today"][1]), "Today identical old vs new once the Held column is taken out")
# expected Held from SQL, per offered row (independent of the code under test)
run = cx.execute("select id from put_runs order by run_at desc, rowid desc limit 1").fetchone()[0]
rows = re.findall(r'<td class="tk">([A-Z]+)<.*?</td><td class="t">([^<]*)</td>', t_new)
held = dict(rows)
def exp_held(tk, expiry, strike):
    out = []
    for r in cx.execute("select p.strategy, l.option_type, l.strike, l.expiration, l.contracts from positions p join legs l on "
                        "l.position_id=p.id where p.book='REAL' and p.status='OPEN' and l.status='OPEN' and l.direction='SHORT' "
                        "and p.strategy in ('CSP','COVERED_CALL') and p.ticker=? order by l.expiration, l.strike", (tk,)):
        if r["strategy"] == "CSP" and r["expiration"][:10] == expiry and abs(r["strike"] - strike) < 1e-9:
            out.append("%d × same put" % r["contracts"])
        else:
            out.append("%d × %s %g %s" % (r["contracts"], r["expiration"][5:10], r["strike"], "P" if r["option_type"] == "PUT" else "C"))
    if cx.execute("select 1 from positions where book='REAL' and status='OPEN' and strategy in ('PMCC','DIAGONAL') and ticker=?", (tk,)).fetchone():
        out.append("PMCC")
    s = cx.execute("select sum(shares) from stock_positions where ticker=?", (tk,)).fetchone()[0]
    if s: out.append("%d sh" % s)
    return " · ".join(out) or "—"
shown = 0
for c in cx.execute("select ticker, expiry, strike, rank, size_n from put_candidates where run_id=? order by rank", (run,)):
    if c["ticker"] in held:
        shown += 1
        e = exp_held(c["ticker"], c["expiry"][:10], c["strike"])
        ok(H.unescape(held[c["ticker"]]) == e, "Held for %s = %r (SQL %r)" % (c["ticker"], H.unescape(held[c["ticker"]]), e))
ok(shown >= 7, "%d offered rows checked" % shown)
ok(H.unescape(held.get("PFE", "")) == "10 × same put", "PFE (offered again on 11-12) reads 10 × same put")
ok(H.unescape(held.get("CRM", "x")) == "—", "a name not held reads —")
# the GOOG / GOOGL pair, straight from the function
sys.path.insert(0, NEW_ENG)
os.environ["HELM_DB"] = DB
from helm import todayview as TV
cr = sqlite3.connect(DB)
ok(TV.held_on(cr, "GOOG") == "GOOGL 100 sh" and TV.held_on(cr, "GOOGL") == "100 sh", "GOOG shows GOOGL's shares, labelled")
ok(TV.held_on(cr, "CVX") == "PMCC" and TV.held_on(cr, "NVDA") == "PMCC" and TV.held_on(cr, "NKE") == "PMCC",
   "a PMCC name reads PMCC only (its legs aren't listed as puts or calls): CVX %r" % TV.held_on(cr, "CVX"))
# the CLI
env = dict(os.environ, HELM_ROOT=NEW_ENG, HELM_DB=DB, COLUMNS="250", PYTHONPATH="/tmp/pylib", HOME=W + "/home")
r = subprocess.run([sys.executable, "-B", "helm.py", "puts", "--last"], cwd=NEW_ENG, env=env, capture_output=True, text=True, timeout=120)
pl = [l for l in r.stdout.split("\n") if " PFE " in l]
ok("Held" in r.stdout and pl and "10 × same put" in pl[0], "helm puts --last: Held column, PFE 10 × same put")
envo = dict(env, HELM_ROOT=OLD_ENG)
ro = subprocess.run([sys.executable, "-B", "helm.py", "puts", "--last"], cwd=OLD_ENG, env=envo, capture_output=True, text=True, timeout=120)
ok(ro.returncode == 0 and r.returncode == 0, "old and new CLI both run")
ok(dump(DB) == before, "no table changed")
print("\n%d checks, %d failed" % (n[0], len(fails)) if fails else "\n%d checks, all passed" % n[0])
sys.exit(1 if fails else 0)
