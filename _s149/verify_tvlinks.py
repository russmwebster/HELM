"""s149: TradingView links on Today's put list and covered-call rows.

Starts the board as production starts it (python3 app.py, real engine) on fresh DB copies:
  A) the live data copy  -> every put row has its arrow, rest of the page identical to the old template
  B) the same copy with a made-up 100-share holding and a made-up `helm calls` run (one choice-1 row,
     one choice-2 row, one reject) -> the covered-call section shows arrows on choice 1 and the reject only.
Usage: TMPDIR=/tmp python3 -B _s149/verify_tvlinks.py [PG_DIR]   (PG_DIR: board code; default ~/mnt/helm-pg)
"""
import json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"
PG = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else HOME + "/mnt/helm-pg"
W = "/tmp/s149tv"; os.makedirs(W + "/home", exist_ok=True)
fails = []
def ok(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)

def fresh(name):
    p = W + "/" + name
    if os.path.exists(p): os.remove(p)
    sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (p,))
    return p

def render(pg, db, port):
    acct = sqlite3.connect(db).execute("select id from accounts").fetchone()[0]
    open(W + "/home/.helm_profile", "w").write(acct)
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=ROOT, HELM_DB=db, PORT=str(port),
               HELM_PG_ENGINE="real", COLUMNS="200", PYTHONPATH="/tmp/pylib")
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=pg, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(80):
            try: urllib.request.urlopen("http://127.0.0.1:%d/today" % port, timeout=2); break
            except Exception: time.sleep(0.5)
        r = urllib.request.urlopen("http://127.0.0.1:%d/today" % port, timeout=90)
        return r.status, r.read().decode()
    finally:
        p.terminate(); p.wait(10)

TV = re.compile(r' ?<a class="tvlnk" href="https://www.tradingview.com/chart/\?symbol=([^"]+)"[^>]*>&#8599;</a>')
def norm(h):   # strip the new links (and random chart ids) to compare the rest of the page
    return re.sub(r'(clip|id)="?c[a-z]*\d+', 'X', TV.sub("", h))

# old board = a copy of the board code with the backed-up template
OLD = W + "/pg_old"
if os.path.exists(OLD): shutil.rmtree(OLD)
shutil.copytree(PG, OLD, ignore=shutil.ignore_patterns("*.bak*", "__pycache__", ".git"))
shutil.copy(PG + "/templates/today.html.bak-20261103-s149", OLD + "/templates/today.html")
print("board code:", PG)

# A) live copy
db = fresh("a.db")
st_new, new = render(PG, db, 8781)
st_old, old = render(OLD, db, 8782)
ok(st_new == 200 and st_old == 200 and "Traceback" not in new, "Today renders, new and old (%s, %s)" % (st_new, st_old))
c = sqlite3.connect(db)
rid = c.execute("select id from put_runs order by run_at desc, rowid desc limit 1").fetchone()[0]
listed = [r[0] for r in c.execute("select ticker from put_candidates where run_id=? and rank is not null and rank<=5 order by rank", (rid,))]
sec = new[new.find("Puts from"):new.find("Covered calls") if "Covered calls" in new else len(new)]
got = TV.findall(sec)
print("       put rows on Today:", listed, "| arrows:", got)
ok(got == listed and len(got) > 0, "every put row on Today has its own TradingView arrow, in order")
ok(TV.findall(old) == [] or set(TV.findall(old)) <= set(), "the old template has no arrows on Today")
ok(norm(new) == norm(old), "apart from the arrows, Today is byte-identical to the old template")

# B) made-up holding + calls run
db = fresh("b.db")
c = sqlite3.connect(db)
acct = c.execute("select id from accounts").fetchone()[0]
c.execute("insert into stock_positions (account_id,ticker,shares,cost_basis,acquired_at,effective_basis) values (?,?,?,?,?,?)",
          (acct, "ZZT", 100, 50.0, "2026-11-03", 48.0))
c.execute("insert into stock_positions (account_id,ticker,shares,cost_basis,acquired_at,effective_basis) values (?,?,?,?,?,?)",
          (acct, "ZZR", 100, 40.0, "2026-11-03", 39.0))
c.execute("insert into call_runs values ('calls_s149test','2099-01-01 10:00:00','test',1,2,0,?, 'test')",
          (json.dumps({"ZZR": "no call passes"}),))
cols = [r[1] for r in c.execute("pragma table_info(call_candidates)")]
for ch, k in ((1, 55.0), (2, 57.5)):
    row = dict(run_id="calls_s149test", ticker="ZZT", choice=ch, hypothetical=0, shares=100, contracts=1, cost_basis=50.0,
               effective_basis=48.0, expiry="2026-12-18", dte=45, strike=k, spot=52.0, bid=1.0, ask=1.1, mid=1.05,
               spread_pct=9.5, delta=0.3, ann_return=0.2, if_called=800.0, rest_price=0.53, kind="shares")
    row = {k2: v for k2, v in row.items() if k2 in cols}
    c.execute("insert into call_candidates (%s) values (%s)" % (",".join(row), ",".join("?" * len(row))), tuple(row.values()))
c.commit()
st, page = render(PG, db, 8783)
cc = page[page.find("<h2>Covered calls</h2>"):]
cc = cc[:cc.find("</table>")]
ok(st == 200 and "<h2>Covered calls</h2>" in page, "Today shows the covered-call section with the made-up holding (%s)" % st)
got = TV.findall(cc)
print("       covered-call arrows:", got)
ok(got == ["ZZT", "ZZR"], "arrows on the first choice and on the 'no call' row only (not on choice 2)")

print("\n%d failure(s)" % len(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
