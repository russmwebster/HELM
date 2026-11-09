"""s156 step 5: Details and Watchlist (helm/boardview.py; helm-pg engine_store.book_view/watchlist_view, templates/book.html,
watchlist_real.html, app.py). Also _s156/fix_dal_name.py on a copy. (Harness built from verify_today.py.) Was: s156 step 4: Today (helm/todayview.py, helm/decide.py pmcc_read; helm-pg engine_store.today_view, templates/today.html).
(Harness built from verify_pmcc_card.py.) s156 step 3: the PMCC rule rows (helm/advice.py pmcc_rows), the card's data (helm/decide.py decide_pmcc), the
card's ruler (helm/thesis.py) and the open PMCC's new card (helm-pg pmcc_card.html, engine_store.py, app.py).

Board started as production starts it on a fresh DB copy, old code (.bak-20261108-s156) and new side by side from /tmp
copies. Expected numbers come from SQL on the copy, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s156/verify_pmcc_card.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s156d"
ENG_BAK = {}
ENG_NEW_ONLY = ["helm/boardview.py"]
PG_BAK = {"engine_store.py": ".bak-20261108-s156c", "app.py": ".bak-20261108-s156b", "templates/book.html": ".bak-20261108-s156"}
PG_NEW_ONLY = ["templates/watchlist_real.html"]
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
PORT = [9160]
DBR = [DB]
def render(eng, pg, paths):
    PORT[0] += 1; port = str(PORT[0])
    open(W + "/home/.helm_profile", "w").write(cx.execute("select id from accounts").fetchone()[0])
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=DBR[0], PORT=port, HELM_PG_ENGINE="real",
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
paths = ["/thesis/" + i for i in ALL + CLOSED] + ["/today", "/book", "/watchlist", "/agents", "/positions"]
o, lo = render(OLD_ENG, OLD_PG, paths); nw, ln = render(NEW_ENG, NEW_PG, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "every page renders, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "no tracebacks in the board's log")
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
same = [p for p in paths if p not in ("/book", "/watchlist")]
diff = [p for p in same if norm(o[p][1]) != norm(nw[p][1])]
ok(not diff or diff == ["/agents"], "every page other than Details and Watchlist is identical old vs new (%d pages): %s" % (len(same), diff))
def text(b):
    b = re.sub(r"<style.*?</style>", " ", b, flags=re.S); b = re.sub(r"<script.*?</script>", " ", b, flags=re.S)
    b = re.sub(r"<svg.*?</svg>", " ", b, flags=re.S); b = re.sub(r"<[^>]+>", " ", b)
    return re.sub(r"\s+", " ", H.unescape(b)).strip()
T = date.today()
def money(x, sign=True): return ("−" if x < 0 else ("+" if sign and x > 0 else "")) + "$" + "{:,.0f}".format(abs(x))
d_old, d_new = text(o["/book"][1]), text(nw["/book"][1])
ok("three diagonals" in d_old and "What-ifs are approximate" in d_old, "control: the old Details had its notes")
for w in ("three diagonals", "What-ifs are approximate", "Greeks are from the last snapshot", "signed by position", "Estimates are",
          "place in Fidelity", " you ", " your ", "HELM "):
    ok(w not in " " + d_new.split("Reading: ", 1)[-1] + " ", "Details: '%s' gone" % w.strip())
mk = cx.execute("select max(checked_at) from leg_checks").fetchone()[0]
ok(d_new.count("Reading: ") == 1 and ("Reading: %s %s %s" % (date.fromisoformat(mk[:10]).strftime("%a"), mk[5:10], mk[11:16])) in d_new and "some readings 11-05" in d_new,
   "Details: one clock %s, older readings dated" % mk[:16])
# figures: the old page's sleeve numbers
for lab in ("Sleeve", "In use"):
    pass
m_old = re.search(r"\$([\d,]+) in use of \$([\d,]+)", d_old) or re.search(r"in use \$([\d,]+)", d_old)
ok("Sleeve $295,710" in d_new and "In use $262,335" in d_new and "Room $33,375" in d_new and "Realized since 10-24 −$4,290" in d_new
   and "Fees since 10-24 $42" in d_new, "Details figures")
# limits: in use per name from SQL-free old page; shares computed here
names = {"VRT": 49500, "DELL": 43000, "GOOG + GOOGL": 33500, "VLO": 33000, "CSCO": 31500, "GE": 30000, "FCX": 28750, "NVDA": 6290, "CVX": 5510, "NKE": 1285}
for nm, u in names.items():
    ok(money(u, False) in d_old and ("%s %s %d%%" % (nm, money(u, False), round(u / 59142.078 * 100))) in d_new,
       "name limit row %s %s" % (nm, money(u, False)))
csp_sum = sum(r[0] * 100 * r[1] for r in cx.execute("select l.strike, l.contracts from legs l join positions p on p.id=l.position_id "
              "where p.book='REAL' and p.status='OPEN' and p.strategy='CSP' and l.status='OPEN' and l.direction='SHORT'"))
ok(csp_sum == sum(u for k, u in names.items() if k in ("VRT", "DELL", "VLO", "CSCO", "GE", "FCX")), "CSP collateral by name = SQL")
B = nw["/book"][1]
ok(re.search(r"Technology</td><td>\$80,790</td><td class=\"amber\">91%", B) and re.search(r"Industrials</td><td>\$79,500</td><td class=\"\">89.6%", B),
   "Technology 91% amber; Industrials 89.6% not")
# Greeks rows: the old page's per-position numbers, the same in the new
for p in cx.execute("select id, ticker from positions where book='REAL' and status='OPEN' order by ticker"):
    m = re.search(re.escape(p["ticker"]) + r" [A-Z]+ [a-z ]+ ([+−-]\d+) [+−-]?\$[\d,]+ ([+−-]\$[\d,]+) ([+−-]?\$[\d,]+) ([+−-]?\$[\d,]+) ([+−-]\$[\d,]+)", d_old)
    if m:
        dsh, bw, th, vg, s5 = m.groups()
        fx = lambda s: s.replace("-", "−")
        want = "%s %s %s %s %s" % (fx(dsh), fx(bw), fx(th).replace("+$0", "$0"), fx(vg).replace("+$0", "$0"), fx(s5))
        ok(want in d_new, "%s Greeks row = the old page: %s" % (p["ticker"], want))
    else:
        ok(False, "%s Greeks row not found on the old page" % p["ticker"])
ok("GOOGL B Shares +100 +$35,561" in d_new, "GOOGL shares in the Greeks table")
for lab, val in (("−10%", "−$23,761"), ("−5%", "−$10,980"), ("+5%", "+$9,179"), ("Implied vol +10 points", "−$1,110")):
    ok(("%s %s" % (lab, val)) in d_new, "market moves: %s %s" % (lab, val))
ok("Typical day (±1.2%) −$2,471 / +$2,367" in d_new, "typical day row")
ok("2026-12-18 $141,250 CVX 230 C ×1 · FCX 57.5 P ×5" in d_new and "2028-01-21 $6,795 CVX 170 C ×1 · NKE 27.5 C ×1 (longs)" in d_new,
   "expiries")
for r in cx.execute("select p.ticker, l.strike, l.option_type, l.expiration, l.contracts, l.open_price from legs l join positions p on p.id=l.position_id "
                    "where p.book='REAL' and p.status='OPEN' and p.strategy in ('CSP','PMCC','COVERED_CALL') and l.status='OPEN' and l.direction='SHORT'"):
    import decimal
    bb = float(decimal.Decimal(repr(r["open_price"] / 2)).quantize(decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP))
    want = "%s %g %s %s %d %.2f %.2f" % (r["ticker"], r["strike"], r["option_type"][0], r["expiration"][5:10], r["contracts"], r["open_price"], bb)
    ok(want in d_new, "buy-back row from SQL: %s" % want)
# watchlist
W_ = nw["/watchlist"][1]; w_ = text(W_)
for w in ("Optionable", "Edit it with", "read-only"):
    ok(w not in w_, "Watchlist: '%s' gone" % w)
n_rows = W_.count('<tr data-offered=')
nwl = cx.execute("select count(*) from watchlist where coalesce(active,1)=1").fetchone()[0]
ok(n_rows == nwl == 135, "Watchlist: %d rows = SQL %d" % (n_rows, nwl))
funds = {r[0] for r in cx.execute("select ticker from approved_funds")}
gr = {}
for r in cx.execute("select ticker, grade, date from ownership_quality order by date, updated_at"):
    gr[r[0]] = r[1]
offered = sum(1 for (t,) in cx.execute("select ticker from watchlist where coalesce(active,1)=1") if t in funds or gr.get(t) in ("A", "B", "C"))
held = {r[0] for r in cx.execute("select ticker from positions where book='REAL' and status='OPEN'")} | {r[0] for r in cx.execute("select ticker from stock_positions where shares>0")}
ok(("Names 135 Offered %d Graded A–C" % offered) in w_ and ("Funds %d Held %d" % (len(funds), len(held))) in w_, "Watchlist counts from SQL (offered %d, held %d)" % (offered, len(held)))
past = []
for tr in re.findall(r"<tr data-offered.*?</tr>", W_, flags=re.S):
    cells = [H.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)]
    nxt = cells[7]
    if re.match(r"\d\d-\d\d", nxt) and ("2026-" + nxt) < T.isoformat() and not nxt.startswith("0"):
        past.append((cells[0], nxt))
ok(not past, "no past date under Next report: %s" % past[:5])
row = lambda t: next((re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", tr))).strip() for tr in re.findall(r"<tr data-offered.*?</tr>", W_, flags=re.S)
                      if re.search(r'<td class="tk">%s</td>' % t, tr)), "")
ok(row("CSCO") == "CSCO Cisco Systems, Inc. Technology B 10-24 yes 08-12 11-12 CSP", "CSCO row: %s" % row("CSCO"))
ok(row("CVX") == "CVX Chevron Corporation Energy C 10-24 yes 10-30 not known PMCC", "CVX row (10-30 was its 'next earnings'): %s" % row("CVX"))
ok(row("SPY").startswith("SPY State Street SPDR S&P 500 ETF Trust US equity index FUND — yes — —"), "SPY row: %s" % row("SPY"))
ok(row("GOOGL").endswith("shares"), "GOOGL held: shares")
ok(row("DAL").startswith("DAL — Industrials"), "DAL before the fix: no name")
ok(("As of %s" % T.strftime("%a %m-%d")) in w_, "Watchlist: one date, today's")
ok('data-f="soon"' in W_ and 'data-f="sec:Technology"' in W_, "filters present")
ok(dump(DB) == before, "no table changed")
# DAL after the fix, on its own copy
DAL = W + "/dal.db"
if os.path.exists(DAL): os.remove(DAL)
sqlite3.connect(DB).execute("VACUUM INTO ?", (DAL,))
fx = subprocess.run([sys.executable, ROOT + "/_s156/fix_dal_name.py", "--apply"], env=dict(os.environ, HELM_DB=DAL), capture_output=True, text=True)
DBR[0] = DAL
nd, _ = render(NEW_ENG, NEW_PG, ["/watchlist"])
ok("Delta Air Lines, Inc." in nd["/watchlist"][1] and "done:" in fx.stdout, "DAL after the fix script: Delta Air Lines, Inc.")
print("\n%d checks, %d failed" % (n[0], len(fails)))
open(W + "/new-book.txt", "w").write(d_new); open(W + "/new-watch.txt", "w").write(w_)
sys.exit(1 if fails else 0)
