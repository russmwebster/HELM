"""s157: Today's data-check line (helm/advice.py coverage): a slot is judged only after its 40-minute window;
a close booked by the import counts from when it was booked. Board started as production starts it on a fresh
DB copy, old engine (.bak-20261109-s157) and new side by side, TZ America/New_York (the Mac's clock).
Expected misses come from SQL on the copy, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s157/verify_coverage.py [NEW_ENGINE_DIR]"""
import hashlib, html as H, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta
os.environ["TZ"] = "America/New_York"; time.tzset()
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"; W = "/tmp/s157t"
IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records")
def tree(src, dst, ign):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign))
    cfg = dst + "/helm/config.py"
    if os.path.exists(cfg):
        c = open(cfg).read(); open(cfg, "w").write(re.sub(r"^HELM_PYTHON = .*$", "HELM_PYTHON = %r" % sys.executable, c, flags=re.M))
    return dst
os.makedirs(W + "/home", exist_ok=True)
NEW = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else tree(ROOT, W + "/eng-new", IGN)
OLD = tree(ROOT, W + "/eng-old", IGN); shutil.copy(ROOT + "/helm/advice.py.bak-20261109-s157", OLD + "/helm/advice.py")
PG = tree(PGROOT, W + "/pg", ("*.bak-*", "__pycache__", ".git", "_to_delete"))
print("new engine:", NEW, hashlib.sha256(open(NEW + "/helm/advice.py", "rb").read()).hexdigest()[:12])
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
DAY = cx.execute("select max(substr(k.checked_at,1,10)) from checks k join positions p on p.id=k.position_id where p.book='REAL'").fetchone()[0]
PORT = [9160]
def render(eng, paths):
    PORT[0] += 1; port = str(PORT[0])
    open(W + "/home/.helm_profile", "w").write(cx.execute("select id from accounts").fetchone()[0])
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=DB, PORT=port, HELM_PG_ENGINE="real",
               PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out", TZ="America/New_York")
    os.makedirs(W + "/advice-out", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=PG, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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
        p.terminate(); p.wait()
    return out
def norm(s): return re.sub(r"\?v=\d+", "?v=", re.sub(r"clip[0-9a-z_-]+", "clip", s))
def line(page):
    m = re.search(r"Reading:(.*?)</", page, re.S); return H.unescape(re.sub(r"<[^>]+>", " ", m.group(1))) if m else ""
NOW = datetime.now()
print("day", DAY, "now", NOW.strftime("%H:%M"))
paths = ["/today", "/agents?date=%s" % DAY, "/book", "/watchlist", "/reference"]
o, nw = render(OLD, paths), render(NEW, paths)
for pth in paths: ok(o[pth][0] == 200 and nw[pth][0] == 200, "%s 200 on both (%s, %s)" % (pth, o[pth][0], nw[pth][0]))
lo, ln = line(o["/today"][1]), line(nw["/today"][1])
print("  old:", " ".join(lo.split())[:300]); print("  new:", " ".join(ln.split())[:300])
# expected misses, from SQL: real positions on DAY, slot window ended, booked before the slot, not closed (import booking time) before it, no check in window
exp = []
for p in cx.execute("select id,ticker,opened_at,created_at,closed_at from positions where book='REAL' and substr(opened_at,1,10)<=? "
                    "and (closed_at is null or substr(closed_at,1,10)>=?) order by ticker", (DAY, DAY)):
    imp = cx.execute("select max(imported_at) from broker_fills where position_id=? and outcome='BOOKED'", (p["id"],)).fetchone()[0]
    for slot in ("10:00", "12:30", "15:15"):
        st = datetime.fromisoformat(DAY + "T" + slot); en = st + timedelta(minutes=40)
        if en > NOW: continue
        known = max(p["opened_at"] or "", p["created_at"] or "")
        if known[:10] > DAY or (known[:10] == DAY and len(known) > 10 and known[11:16] > slot): continue
        gone = p["closed_at"] or ""
        if imp and imp[:10] == DAY and imp[:16] < gone[:16]: gone = imp
        if gone[:10] == DAY and (len(gone) <= 10 or gone[11:16] <= slot): continue
        if not cx.execute("select 1 from checks where position_id=? and checked_at>=? and checked_at<?", (p["id"], st.isoformat(), en.isoformat())).fetchone():
            exp.append("%s %s" % (p["ticker"], slot))
print("  expected misses (SQL):", exp or "none")
for e in exp: ok(re.search(r"\b%s missed [^;]*%s" % tuple(e.split()), ln) is not None, "new line names %s" % e)
ok(("missed" in ln) == bool(exp), "new line says 'missed' only when SQL finds one")
ok("CSCO" not in ln, "CSCO's import close (09:36) is not a miss")
ok("15:15" not in ln or NOW >= datetime.fromisoformat(DAY + "T15:55"), "15:15 not judged before its window ends")
ok("CSCO missed 10:00" in lo, "control: the old code shows CSCO's false 10:00 miss")
strip = lambda s: norm(re.sub(r"Reading:.*?</div>", "", s, flags=re.S))
ok(strip(o["/today"][1]) == strip(nw["/today"][1]), "Today identical outside the reading line")
for pth in paths[2:]: ok(norm(o[pth][1]) == norm(nw[pth][1]), "%s identical" % pth)
ok(dump(DB) == before, "no table changed")
print("%d checks, %d failed" % (n[0], len(fails))); sys.exit(1 if fails else 0)
