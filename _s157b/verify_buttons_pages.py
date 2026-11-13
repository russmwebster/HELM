"""s157c: the board's "Price covered calls" and "Run the PMCC search" buttons and Today's New PMCCs section.
PART 1 (pages): old (.bak-20261113-s157c) vs new on a DB copy, the board started as production starts it.
(Harness built from _s157b/verify_held.py <- _s156/verify_today.py.) Was: s156 step 4: Today (helm/todayview.py, helm/decide.py pmcc_read; helm-pg engine_store.today_view, templates/today.html).
(Harness built from verify_pmcc_card.py.) s156 step 3: the PMCC rule rows (helm/advice.py pmcc_rows), the card's data (helm/decide.py decide_pmcc), the
card's ruler (helm/thesis.py) and the open PMCC's new card (helm-pg pmcc_card.html, engine_store.py, app.py).

Board started as production starts it on a fresh DB copy, old code (.bak-20261108-s156) and new side by side from /tmp
copies. Expected numbers come from SQL on the copy, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s156/verify_pmcc_card.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s157c"
ENG_BAK = {"helm/todayview.py": ".bak-20261113-s157c", "helm/boardview.py": ".bak-20261113-s157c"}
ENG_NEW_ONLY = []
PG_BAK = {"templates/today.html": ".bak-20261113-s157c", "templates/reference.html": ".bak-20261113-s157c", "app.py": ".bak-20261113-s157c", "engine_store.py": ".bak-20261113-s157c", "helm_engine.py": ".bak-20261113-s157c"}
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
    NEW_ENG, NEW_PG = tree(os.path.abspath(sys.argv[1]), W + "/eng-new", ENG_IGN), tree(os.path.abspath(sys.argv[2]), W + "/pg-new", PG_IGN)   # copied, so the config fix applies
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
same = [p for p in paths if p not in ("/today", "/reference")]
diff = [p for p in same if norm(o[p][1]) != norm(nw[p][1])]
ok(not diff, "every page but Today and Reference identical old vs new (%d pages): %s" % (len(same), diff))
# Reference: identical once the four new entries are taken out; each new entry present
rn = norm(nw["/reference"][1])
for e in ("pmcc-list", "pmcc-rate", "width-room", "trend"):
    ok(('<dt id="%s">' % e) in rn, "Reference has the %s entry" % e)
rs = re.sub(r'\n<dt id="(pmcc-list|pmcc-rate|width-room|trend)">.*?</dd>', "", rn)
ok(rs == norm(o["/reference"][1]), "Reference identical old vs new once the new entries are taken out")
ok("{{" not in rn and "None" not in re.sub(r"<[^>]+>", "", rn.split('<dt id="pmcc-list">')[1].split("</dl>")[0]),
   "the new entries' constants all filled")
# Today: the calls button block and the PMCC section are the only differences
blank = lambda b: re.sub(r"\n[ \t]*(?=\n)", "", b)   # Jinja's blank lines don't count
tn, to_ = blank(norm(nw["/today"][1])), blank(norm(o["/today"][1]))
cb = re.search(r'<div class="run">\s*<button class="primary" id="calls-go".*?</script>\s*', tn, re.S)
ok(cb is not None, "Today has the Price covered calls button")
tn2 = tn[:cb.start()] + tn[cb.end():] if cb else tn
i = tn2.find("<h3>New PMCCs")
ok(i > 0, "Today has the New PMCCs section")
tail = to_[i:]
ok(tn2[:i] == to_[:i] and tn2.endswith(tail) and tn2[i:len(tn2) - len(tail)].startswith("<h3>New PMCCs"),
   "Today identical old vs new apart from the button and the New PMCCs section")
sec = tn2[i:len(tn2) - len(tail)]
if fails and "apart from the button" in fails[-1]:
    import difflib
    a, b = to_.split("\n"), tn2.split("\n")
    for l in list(difflib.unified_diff(a, b, lineterm="", n=1))[:40]: print("   DIFF", l[:160])
# who gets the calls button -- from SQL: PMCCs with no open short, shares with 100+ free
exp = set()
for p in cx.execute("select id, ticker from positions where book='REAL' and status='OPEN' and strategy='PMCC'"):
    if not cx.execute("select 1 from legs where position_id=? and status='OPEN' and direction='SHORT'", (p["id"],)).fetchone():
        exp.add(p["ticker"])
for s in cx.execute("select ticker, sum(shares) sh from stock_positions group by ticker"):
    cov = cx.execute("select coalesce(sum(l.contracts),0) from legs l join positions p on p.id=l.position_id where p.book='REAL' "
                     "and p.status='OPEN' and p.strategy='COVERED_CALL' and p.ticker=? and l.status='OPEN' and l.direction='SHORT'",
                     (s["ticker"],)).fetchone()[0]
    if s["sh"] - 100 * cov >= 100:
        exp.add(s["ticker"])
m = re.search(r'id="calls-go"[^>]*>Price covered calls</button> <span class="meta">([^<]*)</span>', tn)
ok(m is not None and set(x.strip() for x in m.group(1).split(",")) == exp, "the calls button lists %s (SQL %s)" % (m.group(1) if m else None, sorted(exp)))
# the PMCC table = the latest recorded search, from SQL
run = cx.execute("select * from pmcc_runs order by run_at desc, rowid desc limit 1").fetchone()
ok(("search %s" % run["run_at"][5:16]) in sec and ("%d → %d → %d passed" % (run["n_universe"], run["n_checked"], run["n_found"])) in H.unescape(sec),
   "header: the search's time and counts")
rows = re.findall(r"<tr><td>(\d+)</td><td class=\"tk\">([A-Z.]+)<.*?</tr>", sec, re.S)
top = cx.execute("select * from pmcc_candidates where run_id=? order by rank limit 5", (run["id"],)).fetchall()
ok([int(r[0]) for r in rows] == [t["rank"] for t in top] and [r[1] for r in rows] == [t["ticker"] for t in top],
   "the top %d, in rank order: %s" % (len(top), [r[1] for r in rows]))
for t in top:
    rr = re.search(r"<tr><td>%d</td><td class=\"tk\">%s<.*?</tr>" % (t["rank"], t["ticker"]), sec, re.S).group(0)
    cells = [H.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", rr, re.S)]
    le = t["long_expiry"]
    want = ["%g C %s-%s" % (t["long_strike"], le[5:10], le[2:4]), "%g C %s" % (t["short_strike"], t["short_expiry"][5:10]),
            "${:,.0f}".format(t["net_debit"]), "%.1f×" % t["rent_cover"], "$%.2f" % t["width_room"],
            "downtrend" if t["downtrend"] else "—"]
    got = [cells[4], cells[5], cells[6], cells[7], cells[9], cells[10]]
    ok(got == want, "%s: long, short, net debit, rent cover, width room, trend = SQL (%s)" % (t["ticker"], got if got != want else "ok"))
    ok((cells[12].startswith("fits ×") and t["size_n"]) or (not t["size_n"] and "over" in cells[12] or "full" in cells[12]),
       "%s: limits %r (size %s, %s)" % (t["ticker"], cells[12], t["size_n"], t["size_binding"]))
late = run["market_open"] == 1 and run["run_at"][11:16] >= "15:00"
ok(("read in the last hour of trading" in sec) == late, "the last-hour line shown only for a search read after 15:00 (%s)" % late)
ok("Run the PMCC search" in sec, "the PMCC button, idle")
ids = set(re.findall(r'<dt id="([^"]+)"', rn))
links = set(re.findall(r'href="/reference#([^"]+)"', tn))
ok(links <= ids, "every ? on Today lands on a Reference entry (%d links)%s" % (len(links), "" if links <= ids else ": missing %s" % sorted(links - ids)))
ok(dump(DB) == before, "no table changed")
print("\n%d checks, %d failed" % (n[0], len(fails)) if fails else "\n%d checks, all passed" % n[0])
sys.exit(1 if fails else 0)
