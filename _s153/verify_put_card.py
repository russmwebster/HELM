"""s153c: "Deciding from here" at the top of a put's card (helm/decide.py; helm-pg engine_store + thesis.html).

Display only. Board started as production starts it on a fresh DB copy, old code (.bak-20261107-s153/-s153b)
and new side by side from /tmp copies. Expected numbers come from SQL on the copy, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s153/verify_put_card.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s153c"
ENG_NEW_ONLY = ["helm/decide.py"]
PG_BAK = {"engine_store.py": ".bak-20261107-s153b", "templates/thesis.html": ".bak-20261107-s153"}
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
    NEW_ENG, NEW_PG = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
else:
    NEW_ENG, NEW_PG = tree(ROOT, W + "/eng-new", ENG_IGN), tree(PGROOT, W + "/pg-new", PG_IGN)
OLD_ENG = tree(ROOT, W + "/eng-old", ENG_IGN)
for f in ENG_NEW_ONLY: os.remove(OLD_ENG + "/" + f)
OLD_PG = tree(PGROOT, W + "/pg-old", PG_IGN)
for f, b in PG_BAK.items(): shutil.copy(PGROOT + "/" + f + b, OLD_PG + "/" + f)
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for d, fs in ((NEW_ENG, ENG_NEW_ONLY), (NEW_PG, list(PG_BAK))):
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
before = dump(DB)
cx = sqlite3.connect(DB); cx.row_factory = sqlite3.Row
CSPS = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy='CSP' order by ticker")]
OTHER = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy!='CSP' order by ticker")]
PORT = [8840]
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
paths = ["/thesis/" + i for i in CSPS + OTHER] + ["/today"]
o, lo = render(OLD_ENG, OLD_PG, paths); nw, ln = render(NEW_ENG, NEW_PG, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "every card and Today render, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "no tracebacks in the board's log")
SEC = re.compile(r'<div class="decide">.*?</div>\s*</div>\s*</div>\s*(?=\{#|\s*<div class="rulebox|\s*\n)', re.S)
def sec(b):
    i = b.find('<div class="decide">')
    if i < 0: return ""
    j = b.find("<div class=\"rulebox", i); k = b.find('<div class="dealbox">', i)
    end = min(x for x in (j, k) if x > 0)
    return b[i:end]
READ_NEW = '<div class="readbox"><b>The read:</b> see <i>Deciding from here</i> above.</div>'
READ_OLD = re.compile(r'<div class="readbox"><b>The read:</b> .*?</div>', re.S)
def norm(b): b = re.sub(r"<style>.*?</style>", "", b, flags=re.S); b = re.sub(r"\b(c[ab])\d+\b", r"\1N", b); return re.sub(r"\s+", " ", re.sub(r'id="c\d+"|url\(#c\d+\)|clip-path="url\(#[^)]*\)"|id="[a-z]+\d{4,}"', "X", re.sub(r"\?v=\d+", "?v=N", b)))
def text(h): return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", h))).strip()
today_txt = text(nw["/today"][1])
for pid in CSPS:
    p = "/thesis/" + pid; ob, nb = o[p][1], nw[p][1]; s = sec(nb); t = text(s)
    tk = pid.split("-")[0]
    ok(s and not sec(ob), "%s: the new section is on the new card only" % tk)
    ok(READ_NEW in nb and READ_NEW not in ob, "%s: the old verdict points to the new section" % tk)
    ok(norm(READ_OLD.sub("R", nb.replace(s, ""))) == norm(READ_OLD.sub("R", ob)), "%s: the card is otherwise identical, old vs new" % tk)
    # independent numbers
    leg = cx.execute("select * from legs where position_id=? and status='OPEN' and direction='SHORT'", (pid,)).fetchone()
    lc = cx.execute("select * from leg_checks where leg_id=? order by checked_at desc limit 1", (leg["id"],)).fetchone()
    spot = cx.execute("select spot_price from checks where position_id=? and data_quality='GOOD' order by checked_at desc limit 1", (pid,)).fetchone()[0]
    K, m, nct = leg["strike"], lc["current_price"], leg["contracts"]
    tv = max(m - max(K - spot, 0), 0) * 100 * nct
    ok(("${:,.0f} of time value left".format(tv)) in t, "%s: time value left ${:,.0f} (SQL)".format(tv) % tk)
    ok(("about ${:,.0f} at the ask".format(lc["current_ask"] * 100 * nct)) in t, "%s: close at the ask (SQL)" % tk)
    ok(("Pay ${:,.0f} for {} shares".format(K * 100 * nct, 100 * nct)) in t, "%s: if assigned, the cash and the shares (SQL)" % tk)
    eb = K - leg["open_price"]
    ok(("effective basis ${:.2f}".format(eb)) in t, "%s: effective basis ${:.2f} (SQL)".format(eb) % tk)
    head = re.search(r"as on Today\)</div>\s*<div class=\"dz-p\"><b>(.*?)</b>", s, flags=re.S)
    hl = text(head.group(1)).replace(" — hold", "") if head else ""   # Today's Holding rows drop " -- hold"
    ok(head and hl in today_txt, "%s: the advice headline is Today's, word for word (%s)" % (tk, hl))
    rid = cx.execute("select id from put_runs order by run_at desc, rowid desc limit 1").fetchone()[0]
    bf = cx.execute("select ticker, rank from put_candidates where run_id=? and size_n>0 and ticker!=? order by rank limit 1",
                    (rid, tk)).fetchone()
    ok(bf and ("Best put that fits" in t and (" %s " % bf["ticker"]) in t and ("#%d" % bf["rank"]) in t),
       "%s: the switch is the best put that fits on the latest list (%s #%s, SQL)" % (tk, bf and bf["ticker"], bf and bf["rank"]))
for pid in OTHER:
    p = "/thesis/" + pid
    ok(not sec(nw[p][1]) and norm(o[p][1]) == norm(nw[p][1]), "%s (%s): not a put -- card identical, no new section" % (pid.split("-")[0], pid.split("-")[1]))
ok(dump(DB) == before, "no table in the copy changed")
print("\n%d checks, %d failed" % (n[0], len(fails)))
sys.exit(1 if fails else 0)
