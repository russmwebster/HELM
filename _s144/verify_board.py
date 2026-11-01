"""s144: start the board as production starts it (python3 app.py, HELM_ROOT, real engine)
against a fresh DB copy; render the pages; compare every table before/after."""
import os, sys, sqlite3, subprocess, time, hashlib, urllib.request, re, html
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PG = HOME + "/mnt/helm-pg"
COPY = "/tmp/s144/board.db"
if os.path.exists(COPY): os.remove(COPY)
sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (COPY,))
def dump():
    c = sqlite3.connect(COPY); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        h = hashlib.sha256()
        for r in c.execute("select * from %s order by rowid" % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out
before = dump()
env = dict(os.environ, HELM_ROOT=ROOT, HELM_DB=COPY, PORT="8771", HELM_PG_ENGINE="real", COLUMNS="200")
p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=PG, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    for _ in range(60):
        try: urllib.request.urlopen("http://127.0.0.1:8771/today", timeout=2); break
        except Exception: time.sleep(0.5)
    pages = ["/today", "/book", "/watchlist", "/agents", "/positions", "/api/book"]
    res = {}
    for u in pages:
        try:
            r = urllib.request.urlopen("http://127.0.0.1:8771" + u, timeout=60); res[u] = (r.status, r.read().decode())
        except Exception as e: res[u] = (getattr(e, "code", "ERR"), str(e))
    for u in pages: print(u, res[u][0], len(res[u][1]))
    t = res["/today"][1]
    m = re.search(r'<div class="tdy-mkt">(.*?)<a', t, re.S)
    print("TODAY LINE:", html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else None)
    b = res["/book"][1]
    print("BOOK has svg:", 'class="riskcurve"' in b, "points:", b.count('class="rc-dot"'), "table:", 'rc-tab' in b)
    tab = re.search(r'<table class="bk rc-tab">(.*?)</table>', b, re.S)
    print("CURVE TABLE:", re.sub(r"<[^>]+>", " ", re.sub(r"\s+", " ", tab.group(1))) if tab else None)
    print("errors in pages:", [u for u in pages if "Traceback" in res[u][1] or "Could not read" in res[u][1] or "unavailable" in res[u][1]])
    open(HOME + "/s144/book.html", "w").write(b); open(HOME + "/s144/positions.html", "w").write(res["/positions"][1]); open(HOME + "/s144/today.html", "w").write(t)
finally:
    p.terminate(); out = p.communicate(timeout=10)[0].decode()
    errs = [l for l in out.splitlines() if "Traceback" in l or "Error" in l]
    print("server errors:", errs[:5])
after = dump()
print("tables changed:", [k for k in before if before[k] != after.get(k)] + [k for k in after if k not in before])
