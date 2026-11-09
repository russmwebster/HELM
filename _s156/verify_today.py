"""s156 step 4: Today (helm/todayview.py, helm/decide.py pmcc_read; helm-pg engine_store.today_view, templates/today.html).
(Harness built from verify_pmcc_card.py.) s156 step 3: the PMCC rule rows (helm/advice.py pmcc_rows), the card's data (helm/decide.py decide_pmcc), the
card's ruler (helm/thesis.py) and the open PMCC's new card (helm-pg pmcc_card.html, engine_store.py, app.py).

Board started as production starts it on a fresh DB copy, old code (.bak-20261108-s156) and new side by side from /tmp
copies. Expected numbers come from SQL on the copy, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s156/verify_pmcc_card.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s156t"
ENG_BAK = {"helm/decide.py": ".bak-20261108-s156b"}
ENG_NEW_ONLY = ["helm/todayview.py"]
PG_BAK = {"engine_store.py": ".bak-20261108-s156b", "templates/today.html": ".bak-20261108-s156"}
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
    NEW_ENG, NEW_PG = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
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
paths = ["/thesis/" + i for i in ALL + CLOSED] + ["/today", "/book", "/watchlist", "/agents"]
o, lo = render(OLD_ENG, OLD_PG, paths); nw, ln = render(NEW_ENG, NEW_PG, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "every page renders, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "no tracebacks in the board's log")
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
same = [p for p in paths if p != "/today"]
diff = [p for p in same if norm(o[p][1]) != norm(nw[p][1])]
ok(not diff, "every page other than Today is identical old vs new (%d pages): %s" % (len(same), diff))
def text(b):
    b = re.sub(r"<style.*?</style>", " ", b, flags=re.S); b = re.sub(r"<script.*?</script>", " ", b, flags=re.S)
    b = re.sub(r"<svg.*?</svg>", " ", b, flags=re.S); b = re.sub(r"<[^>]+>", " ", b)
    return re.sub(r"\s+", " ", H.unescape(b)).strip()
T = date.today()
def money(x, sign=True): return ("−" if x < 0 else ("+" if sign and x > 0 else "")) + "$" + "{:,.0f}".format(abs(x))
B = nw["/today"][1]; t = text(B); t_old = text(o["/today"][1])
ok("Needs your attention" in t_old and "Rules broken today" in t_old, "control: the old Today had its sections")
for w in ("Needs your attention", "Rules broken today", "Calls — covered calls", "Advice only", "HELM leans", "Log a close",
          "Data health", "would be declined", "Prices as of"):
    ok(w not in t, "old Today text gone: %s" % w)
for word in (" you ", " your ", " would ", "HELM says", "HELM thinks", " should ", "advises"):
    ok(word not in " " + t + " ", "no '%s' on Today" % word.strip())
for js in ("/api/fills/preview", "/api/fills/apply", "/api/puts/run", 'id="fills-go"', 'id="puts-go"', 'id="fills-write"'):
    ok(js in B, "button wiring kept: %s" % js)
# one clock
mk = cx.execute("select max(checked_at) from checks where data_quality='GOOD'").fetchone()[0]
hm = mk[11:16]
ok(t.count("Reading: ") == 1 and (" %s %s" % (date.fromisoformat(mk[:10]).strftime("%a"), mk[5:10] + " " + hm)) in t,
   "one clock: Reading %s %s" % (mk[5:10], hm))
# every open position appears once, as a link
for pid in ALL:
    ok(B.count('href="/thesis/%s"' % pid) == 1, "%s linked exactly once" % pid)
# rows from SQL
def leg_rows(pid):
    legs = [dict(r) for r in cx.execute("select * from legs where position_id=? and option_type in ('CALL','PUT')", (pid,))]
    for l in legs:
        r = cx.execute("select * from leg_checks where leg_id=? order by checked_at desc limit 1", (l["id"],)).fetchone()
        l["lc"] = dict(r) if r else None
    return legs
def row_text(label):
    m = re.search(re.escape(label) + r" ↗ (.*?)(?= [A-Z]{2,5}(?: \d| PMCC| shares)| Other| New | Roll prices| Next short| Update book| $)", t)
    return m.group(1) if m else ""
for p in cx.execute("select * from positions where book='REAL' and status='OPEN' and strategy='CSP' order by ticker"):
    l = next(x for x in leg_rows(p["id"]) if x["status"] == "OPEN")
    n_ = l["contracts"]; mid = l["lc"]["current_price"]
    pnl = (l["open_price"] - mid) * 100 * n_
    lab = "%s %g P %s ×%d" % (p["ticker"], l["strike"], l["expiration"][5:10], n_)
    dte = (date.fromisoformat(l["expiration"][:10]) - T).days
    rt = row_text(lab)
    ok(rt and (" %d %s" % (dte, money(pnl)) in " " + rt), "%s: days %d and P&L %s at the mid: '%s'" % (lab, dte, money(pnl), rt[:90]))
    sp = cx.execute("select spot_price from checks where position_id=? and spot_price is not null order by checked_at desc limit 1", (p["id"],)).fetchone()[0]
    if sp <= l["strike"]:
        ok(("tested" in rt) and (money(-mid * 100 * n_) in rt) and (money(l["strike"] * 100 * n_, False) in rt),
           "%s: in Decisions: tested, close %s, if assigned %s" % (lab, money(-mid * 100 * n_), money(l["strike"] * 100 * n_, False)))
    else:
        cus = (sp - l["strike"]) / sp * 100
        ok((" %.1f%% " % cus) in " " + rt + " " and ("21 days: %s" % date.fromordinal(date.fromisoformat(l["expiration"][:10]).toordinal() - 21).isoformat()[5:]) in rt,
           "%s: in Holding: cushion %.1f%%, 21-day date" % (lab, cus))
c = row_text("CSCO 105 P 11-20 ×3")
ok("tested · 21-day" in c and "none · earnings 11-12 (11-02)" in c and "assign: yes · B" in c, "CSCO: rules, stale roll dated, default: '%s'" % c)
ok("−$1,178" not in t and "6.20" not in t, "CSCO: the stale 11-02 close figure is gone")
ok("Roll prices not from today · cd ~/Projects/helm && helm roll CSCO,DELL" in t, "one command line for the stale rolls")
for p in cx.execute("select * from positions where book='REAL' and status='OPEN' and strategy='PMCC' order by ticker"):
    legs = leg_rows(p["id"])
    L = next(x for x in legs if x["status"] == "OPEN" and x["direction"] == "LONG")
    Sh = next((x for x in legs if x["status"] == "OPEN" and x["direction"] == "SHORT"), None)
    net = sum(x["open_price"] * 100 * x["contracts"] - ((x["close_price"] or 0) * 100 * x["contracts"] if x["status"] != "OPEN" else 0)
              for x in legs if x["direction"] == "SHORT")
    close = L["lc"]["current_price"] * 100 - (Sh["lc"]["current_price"] * 100 if Sh else 0)
    whole = -L["open_price"] * 100 + net + close
    if Sh:
        lab = "%s PMCC %g C %s" % (p["ticker"], Sh["strike"], Sh["expiration"][5:10]); rt = row_text(lab)
        dS = (date.fromisoformat(Sh["expiration"][:10]) - T).days
        kept = (Sh["open_price"] - Sh["lc"]["current_price"]) / Sh["open_price"] * 100
        ok((" %d %s %.0f%%" % (dS, money(whole), kept)) in " " + rt, "%s: holding row days %d, P&L %s, short kept %.0f%%: '%s'" % (lab, dS, money(whole), kept, rt))
    else:
        e = L["expiration"][:10]
        lab = "%s PMCC %g C %s-%s" % (p["ticker"], L["strike"], e[5:10], e[2:4]); rt = row_text(lab)
        ok("no short" in rt and money(whole) in rt and money(close) in rt and "next short: none passes" in rt,
           "%s: in Decisions: no short, P&L %s, close %s, none passes: '%s'" % (lab, money(whole), money(close), rt))
# GOOGL shares
sh = cx.execute("select * from stock_positions").fetchall()
for s_ in sh:
    px = cx.execute("select spot_price from checks c join positions p on p.id=c.position_id where p.ticker=? and c.spot_price is not null "
                    "order by c.checked_at desc limit 1", (s_["ticker"],)).fetchone()
    rt = row_text("%s shares ×%d" % (s_["ticker"], s_["shares"]))
    ok(rt and ("basis $%.2f" % s_["effective_basis"]) in rt and "covered call · blocked" in rt,
       "%s shares in Holding with basis and the calls run's result: '%s'" % (s_["ticker"], rt))
# figures and book rules
sv = re.search(r"Sleeve (\$[\d,]+) In use (\$[\d,]+) Room (−?\$[\d,]+)", t)
ok(sv is not None and sv.groups() == ("$295,710", "$262,335", "$33,375"), "figures row: %s" % (sv.groups(),) if sv else "figures row missing")
ok("Book delta limit (half the sleeve) over $201,594 / $147,855 DELL $37,946 · GOOGL sh $35,561 · VRT $32,800" in t,
   "book rules: delta over with its largest")
ok("Technology sector limit (30%) near $80,790 / $88,713 CSCO · DELL · NVDA" in t, "book rules: Technology near (91%)")
ok("Industrials" not in t.split("Book rules")[1].split("Decisions")[0], "Industrials at 89.6% not listed")
# advice over-rule recs all represented
sys.path.insert(0, NEW_ENG)
# puts from SQL
run = cx.execute("select * from put_runs order by run_at desc limit 1").fetchone()
cands = [dict(r) for r in cx.execute("select * from put_candidates where run_id=? and rank is not null order by rank", (run["id"],))]
for cnd in cands[:5]:
    want = "%d %s ↗ %s %g P %s %d %.2f $%.2f" % (cnd["rank"], cnd["ticker"], cnd["grade"], cnd["strike"], cnd["expiry"][5:10], cnd["dte"], cnd["delta"], cnd["mid"])
    ok(want in t, "put row from SQL: %s" % want)
nd = len([x for x in cands if not x["size_n"]])
ok(("%d → %d → %d passed · %d declined by limits" % (run["n_universe"], run["n_checked"], run["n_found"], nd)) in t,
   "put list counts from SQL (declined %d)" % nd)
ok("Best that fit 13 FCX" in t, "best that fit starts at rank 13 FCX")
# advice.json and helm advise unchanged
def adv(eng):
    env = dict(os.environ, HELM_ROOT=eng, HELM_DB=DB, PYTHONPATH=eng + ":/tmp/pylib", HOME=W + "/home", COLUMNS="200")
    j = subprocess.run([sys.executable, "-B", "-c", "import json;from helm import advice;d=advice.build();d.pop('generated_at',None);"
                        "print(json.dumps(d,sort_keys=True,default=str))"], cwd=eng, env=env, capture_output=True, text=True)
    c = subprocess.run([sys.executable, "-B", "helm.py", "advise"], cwd=eng, env=env, capture_output=True, text=True)
    return j.stdout, c.stdout
ja, ca = adv(OLD_ENG); jb, cb = adv(NEW_ENG)
ok(ja and ja == jb, "advice.json identical old vs new (%d bytes)" % len(ja))
ok(ca and ca == cb, "`helm advise` identical old vs new (%d chars)" % len(ca))
ok(dump(DB) == before, "no table changed")
print("\n%d checks, %d failed" % (n[0], len(fails)))
open(W + "/new-today.html", "w").write(B); open(W + "/new-today.txt", "w").write(t)
sys.exit(1 if fails else 0)
