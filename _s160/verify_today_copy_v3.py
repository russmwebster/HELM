"""s160: the Today copy -- board saves its own page; the snapshot and `helm today-copy` ask it to.

Run in the device VM:  python3 -B verify_today_copy.py OLD_PG NEW_PG TREE LIVE_DB WORK
  OLD_PG  helm-pg as it is now (the baseline)        NEW_PG  helm-pg with todaycopy.py + the route
  TREE    helm with helm/todaycopy.py, the snapshot hook and the command
  LIVE_DB the live database (read only; copied fresh)  WORK  scratch folder
Starts both boards the way production starts them (python3 app.py, PORT=...), on fresh DB copies.
"""
import os, re, sys, json, time, shutil, sqlite3, subprocess, urllib.request
from datetime import date, timedelta

OLD_PG, NEW_PG, TREE, LIVE_DB, WORK = sys.argv[1:6]
OK = BAD = 0


def check(cond, msg):
    global OK, BAD
    if cond:
        OK += 1
    else:
        BAD += 1
        print("FAIL", msg)


def fresh_db(name):
    p = os.path.join(WORK, name)
    if os.path.exists(p):
        os.remove(p)
    c = sqlite3.connect("file:%s?mode=ro" % LIVE_DB, uri=True)
    c.execute("VACUUM INTO '%s'" % p)
    c.close()
    return p


def tables(db):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by 1"):
        out[t] = (tuple(r[1] for r in c.execute("pragma table_info(%s)" % t)),
                  sorted(map(repr, c.execute("select * from %s" % t))))
    c.close()
    return out


HOME = os.path.join(WORK, "home")
os.makedirs(HOME, exist_ok=True)
open(os.path.join(HOME, ".helm_profile"), "w").write("fidelity_9e60c8")


def env(**kw):
    e = dict(os.environ, HOME=HOME, PYTHONPATH="/tmp/pylib", HELM_ROOT=TREE, TMPDIR="/tmp",
             PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
    e.update({k: str(v) for k, v in kw.items()})
    return e


def start(pg, port, db, out):
    log = open(os.path.join(WORK, "board_%d.log" % port), "w")
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=pg, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         env=env(PORT=port, HELM_DB=db, HELM_ADVICE_DIR=out))
    for _ in range(40):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/reference" % port, timeout=2)
            return p
        except Exception:
            time.sleep(0.5)
    raise SystemExit("board on %d did not start" % port)


def get(port, path, data=None, headers=None):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def norm(s):
    return re.sub(r"\?v=\d+", "?v=N", s)          # the static files' cache-buster is a file time


def nums(s):
    return re.findall(r"[−+-]?\$?\d[\d,]*(?:\.\d+)?%?", s)


def visible_main(html):
    """The live page's words after the copy's own stripping rules -- what the copy must carry."""
    sys.path.insert(0, NEW_PG)
    import todaycopy as T
    root = T._parse(html)
    T._strip(root)
    main = T._find(root, "main")
    pieces = []

    def walk(n):
        for k in n.kids:
            if isinstance(k, str):
                pieces.append(k)
            elif k.tag not in ("style", "script"):
                walk(k)
    walk(main)
    return pieces, root


print("loaded: old", OLD_PG, "| new", NEW_PG, "| engine", TREE)
OUT = os.path.join(WORK, "out")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
db_old, db_new = fresh_db("old.db"), fresh_db("new.db")
pre = tables(db_new)
po, pn = start(OLD_PG, 8791, db_old, OUT), start(NEW_PG, 8792, db_new, OUT)
try:
    # 1. every page the menu reaches is the same, old vs new
    s, home = get(8792, "/today")
    links = sorted(set(re.findall(r'<a href="(/[a-z_\-/]*)"', home.split('<nav class="nav">')[1].split("</nav>")[0])))
    check(len(links) >= 3, "menu links found %s" % links)
    for path in sorted(set(links + ["/reference", "/book", "/agents"])):
        so, bo = get(8791, path)
        sn, bn = get(8792, path)
        check(so == sn == 200 and norm(bo) == norm(bn), "page %s differs old vs new (%s/%s)" % (path, so, sn))
    live = norm(get(8792, "/today")[1])

    # 2. the save, through the command, as Russ would run it
    r = subprocess.run([sys.executable, "-B", "helm.py", "today-copy"], cwd=TREE, capture_output=True, text=True,
                       env=env(HELM_DB=db_new, HELM_PG_URL="http://127.0.0.1:8792"), timeout=90)
    line = r.stdout.strip()
    check(re.fullmatch(r"today copy: saved \d{4}-\d{2}-\d{2} \d{2}:\d{2} Eastern", line), "command line %r %s" % (line, r.stderr[-300:]))
    hh = line.split(" ")[-2] if line else ""
    dated = sorted(os.listdir(os.path.join(OUT, "today")))
    check(len(dated) == 2 and dated[0].endswith(hh.replace(":", "") + ".html"), "dated copies %s" % dated)
    page = open(os.path.join(OUT, "today.html")).read()
    text = open(os.path.join(OUT, "today.txt")).read()
    check(page == open(os.path.join(OUT, "today", dated[0])).read(), "dated html = newest html")
    check(text == open(os.path.join(OUT, "today", dated[1])).read(), "dated txt = newest txt")

    # 3. what the copy keeps and drops
    for bad in ("<button", "<script", "<aside", "/api/", 'href="/', "tradingview", "<nav", "helm roll", "helm calls "):
        check(bad not in page, "saved html still has %r" % bad)
    check(re.search(r"Reading: [^<·]*\d{2}:\d{2} Eastern", page) is not None, "page reading time says Eastern")
    check(re.search(r"^Reading: .*\d{2}:\d{2} Eastern", text, re.M) is not None, "text reading time says Eastern")
    check(re.search(r"^Fixed copy of HELM's Today page, not live · saved \w{3} \d{2} \w{3} \d{4}, \d{2}:\d{2} Eastern$",
                    text, re.M) is not None, "text header")
    words, root = visible_main(live)
    want = [x for w in words for x in nums(w)]
    have_t, have_p = nums(text), nums(page)
    from collections import Counter
    miss_t = Counter(want) - Counter(have_t)
    miss_p = Counter(want) - Counter(have_p)
    check(not miss_t, "numbers on the page missing from the text: %s" % dict(list(miss_t.items())[:8]))
    check(not miss_p, "numbers on the page missing from the saved html: %s" % dict(list(miss_p.items())[:8]))
    check(len(want) > 100, "the page carried %d numbers (a real page)" % len(want))
    for h in re.findall(r"<h3>([^<]+)", live):
        check(("## " + h.strip()) in text, "section %r in the text" % h.strip())

    # 4. colour comes through as words
    import todaycopy as T
    colored = []

    def walk(n):
        for k in n.kids:
            if isinstance(k, T._Node):
                c = k.classes()
                if ("red" in c or "amber" in c) and T._ws(T._inline(k)):
                    colored.append(("red" if "red" in c else "amber", T._ws(T._inline(k))))
                walk(k)
    walk(T._find(root, "main"))
    check(len(colored) >= 3, "coloured items on the page: %d" % len(colored))
    for kind, w in colored:
        word = "(over)" if kind == "red" else "(near)"
        last = w.split(" ")[-1].lower()
        check((w + " " + word) in text or (last in T.SAYS_IT and w in text), "colour lost on %r" % w)
    check(text.count("(near)") + text.count("(over)") >= len(colored) - sum(1 for _, w in colored if w.split(" ")[-1].lower() in T.SAYS_IT) - 1,
          "colour marks counted")

    # 5. only from this machine; the database untouched
    s, b = get(8792, "/api/today/save", data=b"{}", headers={"Content-Type": "application/json"})
    check(s == 200 and json.loads(b).get("ok"), "local POST saves")
    sys.path.insert(0, NEW_PG)
    os.environ.update(env(HELM_DB=db_new, HELM_ADVICE_DIR=OUT))
finally:
    po.terminate(); pn.terminate(); po.wait(); pn.wait()
check(tables(db_new) == pre, "no table changed by the board saving")

# 6. remote callers refused (Flask test client, a foreign address), board app from NEW_PG
shutil.rmtree(os.path.join(WORK, "out_remote"), ignore_errors=True)
r = subprocess.run([sys.executable, "-B", "-c", (
    "import app; c=app.app.test_client(); r=c.post('/api/today/save', environ_base={'REMOTE_ADDR':'10.0.0.5'});"
    "print(r.status_code)")], cwd=NEW_PG, capture_output=True, text=True,
    env=env(HELM_DB=db_new, HELM_ADVICE_DIR=os.path.join(WORK, "out_remote")), timeout=60)
check(r.stdout.strip().endswith("403"), "remote POST refused: %r %s" % (r.stdout, r.stderr[-200:]))
check(not os.path.exists(os.path.join(WORK, "out_remote")), "remote POST wrote nothing")

# 7. board down: the command says so and writes nothing
before = sorted(os.listdir(os.path.join(OUT, "today")))
r = subprocess.run([sys.executable, "-B", "helm.py", "today-copy"], cwd=TREE, capture_output=True, text=True,
                   env=env(HELM_DB=db_new, HELM_PG_URL="http://127.0.0.1:8793"), timeout=60)
check(r.stdout.strip().startswith("today copy: not written -- board not answering"), "down board line %r" % r.stdout)
check(r.returncode == 0 and sorted(os.listdir(os.path.join(OUT, "today"))) == before, "down board harmless")

# 8. the snapshot hook: called after advice.json, skipped on a stood-down day, never raises
r = subprocess.run([sys.executable, "-B", "-c", (
    "import sys; sys.path.insert(0,'.')\n"
    "import helm.cli.check_cmd as C, helm.todaycopy as T\n"
    "calls=[]\n"
    "T.request_save=lambda *a,**k: (calls.append(1), {'ok':True,'saved_at':'X Eastern'})[1]\n"
    "C.SNAPSHOT_INFO.clear(); C._today_copy_after_snapshot(); print('normal', len(calls))\n"
    "C.SNAPSHOT_INFO['stood_down']='market closed'; C._today_copy_after_snapshot(); print('stood', len(calls))\n"
    "C.SNAPSHOT_INFO.clear()\n"
    "def boom(*a,**k): raise RuntimeError('x')\n"
    "T.request_save=boom; C._today_copy_after_snapshot(); print('raised-ok')\n"
    "import inspect; src=inspect.getsource(C.run_snapshot); i=src.index('_advice_after_snapshot(_err)'); j=src.index('_today_copy_after_snapshot()')\n"
    "print('order', i < j)\n")], cwd=TREE, capture_output=True, text=True, env=env(HELM_DB=db_new), timeout=60)
o = r.stdout
check("normal 1" in o and "stood 1" in o and "raised-ok" in o and "order True" in o and
      "today copy post-pass FAILED: RuntimeError" in o, "snapshot hook: %r %s" % (o, r.stderr[-300:]))

# 9. pruning: only dated copies older than 14 days, by the date in the name
sys.path.insert(0, NEW_PG)
import todaycopy as T
d = os.path.join(WORK, "prune")
shutil.rmtree(d, ignore_errors=True)
os.makedirs(d)
today = date(2026, 11, 17)
names = []
for back in (0, 13, 14, 15, 40):
    for ext in ("html", "txt"):
        n = "%s-1000.%s" % ((today - timedelta(days=back)).isoformat(), ext)
        open(os.path.join(d, n), "w").write("x")
        names.append(n)
open(os.path.join(d, "notes.txt"), "w").write("keep")
open(os.path.join(d, "2026-01-01-1000.html.bak"), "w").write("keep")
removed = T.prune(d, today=today)
left = sorted(os.listdir(d))
check(removed == 4, "pruned %d" % removed)
check(left == sorted([n for n in names if not n.startswith(("2026-10-08", "2026-11-02"))] + ["notes.txt", "2026-01-01-1000.html.bak"]),
      "left after prune %s" % left)

# 10. Eastern regardless of the machine's zone (the device VM runs on UTC)
r = subprocess.run([sys.executable, "-B", "-c", "import todaycopy as T, datetime as D; "
                    "e=T._now_eastern(); u=D.datetime.utcnow(); h=round((u-e).total_seconds()/3600); print(h)"],
                   cwd=NEW_PG, capture_output=True, text=True, env=env(TZ="UTC"), timeout=30)
check(r.stdout.strip() in ("4", "5"), "Eastern is UTC-5/-4: %r %s" % (r.stdout, r.stderr[-200:]))

print("ok", OK, "fail", BAD)
