"""s151: the PMCC at a glance -- one picture at the top of a PMCC card (helm.thesis.pmcc_glance_svg) and
the same ruler on each PMCC row of Today (pmcc_ruler_svg, via engine_store._latest_calls). Display only.

Starts the board as production starts it (python3 app.py, real engine) on fresh DB copies, old code
(the .bak-20261104-s151 files) and new code side by side from /tmp copies. Fixtures only on copies.
Usage: TMPDIR=/tmp python3 -B _s151/verify_glance.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date, timedelta
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s151v"; BAK = ".bak-20261104-s151"
ENG_FILES = ["helm/thesis.py"]; PG_FILES = ["engine_store.py", "templates/thesis_diag.html", "templates/today.html"]
ENG_IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records")
PG_IGN = ("*.bak-*", "__pycache__", ".git", "_to_delete")

def tree(src, dst, files, use_bak, ign):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign))
    for f in files:
        if use_bak: shutil.copy2(src + "/" + f + BAK, dst + "/" + f)
    return dst
os.makedirs(W + "/home", exist_ok=True)
NEW_ENG = os.path.abspath(sys.argv[1]) if len(sys.argv) > 2 else tree(ROOT, W + "/eng-new", ENG_FILES, False, ENG_IGN)
NEW_PG = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else tree(PGROOT, W + "/pg-new", PG_FILES, False, PG_IGN)
OLD_ENG = tree(ROOT, W + "/eng-old", ENG_FILES, True, ENG_IGN)
OLD_PG = tree(PGROOT, W + "/pg-old", PG_FILES, True, PG_IGN)
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for f in ENG_FILES: print("   ", f, hashlib.sha256(open(NEW_ENG + "/" + f, "rb").read()).hexdigest()[:12])
for f in PG_FILES: print("   ", f, hashlib.sha256(open(NEW_PG + "/" + f, "rb").read()).hexdigest()[:12])

fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)

def copy(name):
    p = W + "/" + name + ".db"
    if os.path.exists(p): os.remove(p)
    sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (p,))
    return p
def dump(p):
    c = sqlite3.connect(p); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        h = hashlib.sha256(); h.update(repr([r[1] for r in c.execute('pragma table_info("%s")' % t)]).encode())
        for r in c.execute('select * from "%s" order by rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out

PORT = [8790]
def render(db, eng, pg, paths):
    PORT[0] += 1; port = str(PORT[0])
    acct = sqlite3.connect(db).execute("select id from accounts").fetchone()[0]
    open(W + "/home/.helm_profile", "w").write(acct)
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, PORT=port, HELM_PG_ENGINE="real",
               COLUMNS="200", PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out")
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
                r = urllib.request.urlopen("http://127.0.0.1:%s%s" % (port, path), timeout=120)
                out[path] = (r.status, r.read().decode())
            except Exception as e:
                out[path] = (getattr(e, "code", "ERR"), str(e))
    finally:
        p.terminate(); log = p.communicate(timeout=10)[0].decode()
    return out, log

def norm(b):
    b = re.sub(r"\?v=[0-9a-f.]+", "?v=X", b)
    b = re.sub(r'id="c[a-z0-9]+"', 'id="cX"', b); b = re.sub(r"url\(#c[a-z0-9]+\)", "url(#cX)", b)
    return b
def text(b):
    b = re.sub(r"<svg.*?</svg>", " ", b, flags=re.S); b = re.sub(r"<style.*?</style>", " ", b, flags=re.S)
    return [w for w in html.unescape(re.sub(r"<[^>]+>", " ", b)).split()]
def svg_text(b, cls):
    m = re.search(r'<div class="%s"[^>]*>(<svg.*?</svg>)' % cls, b, flags=re.S)
    return (html.unescape(" ".join(re.findall(r">([^<>]+)</text>", m.group(1)))) if m else None), m

# ---------------------------------------------------------------- the live copy
db = copy("live"); before = dump(db)
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
opens = [dict(r) for r in c.execute("select id, ticker, strategy from positions where book='REAL' and status='OPEN' order by id")]
pm = [p for p in opens if p["strategy"] == "PMCC"]
print("open:", [(p["ticker"], p["strategy"]) for p in opens])
paths = ["/today"] + ["/thesis/" + p["id"] for p in opens]
new, nlog = render(db, NEW_ENG, NEW_PG, paths)
old, olog = render(db, OLD_ENG, OLD_PG, paths)
ok(dump(db) == before, "no table changed on the copy by rendering")
for pth in paths:
    ok(new[pth][0] == 200 and old[pth][0] == 200, "%s renders 200 old and new (%s/%s)" % (pth, old[pth][0], new[pth][0]))

def expect(pid):
    """the numbers from the database itself, not from the engine"""
    L = [dict(r) for r in c.execute("select * from legs where position_id=? and status='OPEN'", (pid,))]
    lg = [l for l in L if l["direction"] == "LONG"][0]; sh = [l for l in L if l["direction"] == "SHORT"]
    allsh = [dict(r) for r in c.execute("select * from legs where position_id=? and direction='SHORT' and status!='OPEN'", (pid,))]
    rent = sum((l["open_price"] - (l["close_price"] or 0)) for l in allsh)
    ck = c.execute("select * from checks where position_id=? order by checked_at desc limit 1", (pid,)).fetchone()
    e = {"spot": ck["spot_price"], "floor": lg["strike"] + lg["open_price"] - rent, "long": lg["strike"]}
    if sh:
        s = sh[0]
        lc = c.execute("select current_price from leg_checks where leg_id=? order by checked_at desc limit 1", (s["id"],)).fetchone()
        e.update(short=s["strike"], cushion=(s["strike"] - ck["spot_price"]) / ck["spot_price"] * 100,
                 half=int(s["open_price"] * 50 + 0.5 - 1e-9) / 100.0 if (s["open_price"] * 50) % 1 >= 0.5 else round(s["open_price"] * 0.5, 2),
                 day21=(date.fromisoformat(s["expiration"][:10]) - timedelta(days=21)), exp=date.fromisoformat(s["expiration"][:10]),
                 mark=lc["current_price"] if lc else None, sold=s["open_price"])
    return e
md = lambda d: "%s %d" % (d.strftime("%b"), d.day)

for p in pm:
    pth = "/thesis/" + p["id"]; nb, ob = norm(new[pth][1]), norm(old[pth][1])
    t, m = svg_text(nb, "gpic"); e = expect(p["id"])
    ok(m is not None, "%s: the card leads with the picture" % p["ticker"])
    if not m: continue
    ok("$%.2f" % e["spot"] in t, "%s: the stock at $%.2f" % (p["ticker"], e["spot"]))
    ok("long %g" % e["long"] in t and ("short %g" % e["short"]) in t, "%s: long %g and short %g on the ruler" % (p["ticker"], e["long"], e["short"]))
    ok("next short above $%.2f" % e["floor"] in t, "%s: next short above $%.2f (the width floor)" % (p["ticker"], e["floor"]))
    ok("%.1f%% cushion to the short" % e["cushion"] in t, "%s: %.1f%% cushion" % (p["ticker"], e["cushion"]))
    ok("%s · 21 days" % md(e["day21"]) in t and "%s expiry" % md(e["exp"]) in t, "%s: 21 days on %s, expiry %s" % (p["ticker"], md(e["day21"]), md(e["exp"])))
    ok("50%% · buy back $%.2f" % e["half"] in t, "%s: 50%% buy-back $%.2f" % (p["ticker"], e["half"]))
    if e["mark"] is not None:
        kept = (e["sold"] - e["mark"]) / e["sold"] * 100
        want = ("%.0f%% kept · short $%.2f of $%.2f" % (kept, e["mark"], e["sold"])) if kept >= 0 else \
               ("the short is at $%.2f — above the $%.2f you sold it for" % (e["mark"], e["sold"]))
        ok(want in t, "%s: premium line '%s'" % (p["ticker"], want))
    ok("loss limit" in t and "Rent vs decay" in t, "%s: loss-limit strip and rent line drawn" % p["ticker"])
    ok('<details class="words">' in nb, "%s: the words sit under 'In words'" % p["ticker"])
    ok(sorted(text(ob)) == sorted(text(nb.replace("In words", "").replace("where I stand · the next lines to watch · dates", ""))),
       "%s: every word of the old card is still on the new card" % p["ticker"])
    td = new["/today"][1]; i = td.find(p["id"]); j = td.find("</tr>", i)
    rt = " ".join(re.findall(r">([^<>]+)</text>", td[i:j]))
    ok("%.1f%% cushion to the short" % e["cushion"] in html.unescape(rt) and "$%.2f" % e["spot"] in rt,
       "%s: Today's row carries the ruler (%.1f%%)" % (p["ticker"], e["cushion"]))
for p in opens:
    if p["strategy"] == "PMCC": continue
    pth = "/thesis/" + p["id"]
    ok(norm(new[pth][1]) == norm(old[pth][1]), "%s %s card byte-identical" % (p["ticker"], p["strategy"]))
ws = lambda b: re.sub(r"\s+", " ", b)       # whitespace runs only: the ruler sits on its own template line
strip = lambda b: ws(re.sub(r'<div class="pmcc-ruler".*?</svg></div>', "", norm(b), flags=re.S))
_a, _b = ws(norm(old["/today"][1])), strip(new["/today"][1])
ok(_a == _b, "Today identical apart from the PMCC rulers")
if _a != _b:
    k = next(i for i in range(min(len(_a), len(_b))) if _a[i] != _b[i]); print("    old:", repr(_a[k-80:k+120])); print("    new:", repr(_b[k-80:k+120]))
ok("Traceback" not in nlog, "no traceback in the new board's log")

# ---------------------------------------------------------------- fixtures
if pm:
    P = pm[0]; pid = P["id"]
    def fixture(name, sqls):
        f = copy(name); k = sqlite3.connect(f)
        for q, a in sqls: k.execute(q, a)
        k.commit(); k.close()
        r, lg = render(f, NEW_ENG, NEW_PG, ["/today", "/thesis/" + pid])
        return r, lg
    last = "(select id from checks where position_id=? order by checked_at desc limit 1)"
    sk = c.execute("select strike from legs where position_id=? and direction='SHORT' and status='OPEN'", (pid,)).fetchone()[0]
    r, lg = fixture("tested", [("update checks set spot_price=? where id=" + last, (sk * 1.04, pid))])
    t, _ = svg_text(norm(r["/thesis/" + pid][1]), "gpic")
    ok(t and "through the short's strike" in t, "tested short: the picture says 'through the short's strike'")
    r, lg = fixture("pastlimit", [("update checks set pnl_unrealized=-99999 where id=" + last, (pid,))])
    t, _ = svg_text(norm(r["/thesis/" + pid][1]), "gpic")
    ok(t and "past the loss limit" in t, "past the loss limit: said on the picture")
    r, lg = fixture("nospot", [("update checks set spot_price=NULL where position_id=?", (pid,))])
    b = norm(r["/thesis/" + pid][1])
    ok(r["/thesis/" + pid][0] == 200 and 'class="gpic"' not in b and "Where I stand" in b,
       "no stock reading: no picture, the old At a glance in words")
    ok('class="pmcc-ruler"' not in r["/today"][1].split(pid)[1].split("</tr>")[0], "no stock reading: no ruler on Today's row")
    r, lg = fixture("noshort", [("update legs set status='CLOSED', close_price=open_price*0.5, close_date=date('now') "
                                 "where position_id=? and direction='SHORT' and status='OPEN'", (pid,))])
    t, _ = svg_text(norm(r["/thesis/" + pid][1]), "gpic")
    ok(r["/thesis/" + pid][0] == 200 and t and "No short open" in t and "next short above" in t,
       "no short open: the picture says so, with the next short's floor")
    ok("Traceback" not in lg, "no traceback on the fixtures")

print("\n%d checks, %d failed" % (n[0], len(fails)))
for f in fails: print("  FAIL", f)
sys.exit(1 if fails else 0)
