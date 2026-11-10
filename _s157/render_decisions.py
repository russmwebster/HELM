"""s157: render Today on a fresh DB copy with the board's old template (.bak-20261109-s157) and the new one, board
started as production starts it; writes _s157/render/{old,new}.html with style.css inlined, for the width check
(_s157/measure_decisions.py, run with playwright). Also checks the page TEXT is identical (only styling changed).
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s157/render_decisions.py [NEW_PG_DIR]"""
import html as H, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
os.environ["TZ"] = "America/New_York"; time.tzset()
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"; W = "/tmp/s157r"
OUT = ROOT + "/_s157/render"; os.makedirs(OUT, exist_ok=True); os.makedirs(W + "/home", exist_ok=True)
def tree(src, dst, ign):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign))
    cfg = dst + "/helm/config.py"
    if os.path.exists(cfg):
        c = open(cfg).read(); open(cfg, "w").write(re.sub(r"^HELM_PYTHON = .*$", "HELM_PYTHON = %r" % sys.executable, c, flags=re.M))
    return dst
ENG = tree(ROOT, W + "/eng", ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records"))
PGI = ("*.bak-*", "__pycache__", ".git", "_to_delete")
NEW = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else tree(PGROOT, W + "/pg-new", PGI)
OLD = tree(PGROOT, W + "/pg-old", PGI); shutil.copy(PGROOT + "/templates/today.html.bak-20261109-s157", OLD + "/templates/today.html")
DB = W + "/copy.db"
if os.path.exists(DB): os.remove(DB)
sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (DB,))
acct = sqlite3.connect(DB).execute("select id from accounts").fetchone()[0]
port = [9260]
def render(pg):
    port[0] += 1; p_ = str(port[0]); open(W + "/home/.helm_profile", "w").write(acct)
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=ENG, HELM_DB=DB, PORT=p_, HELM_PG_ENGINE="real", PYTHONPATH="/tmp/pylib",
               HELM_ADVICE_DIR=W + "/adv", TZ="America/New_York")
    os.makedirs(W + "/adv", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=pg, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(120):
            try: urllib.request.urlopen("http://127.0.0.1:%s/health" % p_, timeout=2); break
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.5)
        r = urllib.request.urlopen("http://127.0.0.1:%s/today" % p_, timeout=120); return r.status, r.read().decode()
    finally:
        p.terminate(); p.wait()
css = open(PGROOT + "/static/style.css").read()
res = {}
for name, pg in (("old", OLD), ("new", NEW)):
    st, page = render(pg); res[name] = page
    page = re.sub(r'<link[^>]+style\.css[^>]*>', "<style>%s</style>" % css, page)
    open("%s/%s.html" % (OUT, name), "w").write(page); print(name, st, len(page))
text = lambda s: " ".join(H.unescape(re.sub(r"<style>.*?</style>|<[^>]+>", " ", s, flags=re.S)).split())
print("page text identical:", text(res["old"]) == text(res["new"]))
print("decisions rows:", res["new"].count('class="t wr"') // 3)
