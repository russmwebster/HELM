"""s150: Today's calls section (covered calls + PMCC shorts), roll choices under the advice that asks
for them, and "not read yet" for a position no snapshot has read.

Starts the board as production starts it (python3 app.py, real engine) on fresh DB copies, with the
engine and board loaded from copies under /tmp so the old code (.bak-20261103-s150) and the new code
run side by side. Fixtures are written ONLY to the copies.
Usage: TMPDIR=/tmp python3 -B _s150/verify_today_calls.py [NEW_ENGINE_DIR NEW_PG_DIR]
  (default: the code on disk; a perturbed copy can be passed instead -- the harness says which it loaded)
"""
import hashlib, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date, datetime
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s150"; BAK = ".bak-20261103-s150"
ENG_FILES = ["helm/advice.py", "helm/rolltool.py"]; PG_FILES = ["engine_store.py", "templates/today.html"]
TODAY = date.today().isoformat()

def tree(src, dst, files, use_bak, ign):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign))
    for f in files:
        if use_bak: shutil.copy(src + "/" + f + BAK, dst + "/" + f)
    return dst
os.makedirs(W + "/home", exist_ok=True)
ENG_IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records")
NEW_ENG = os.path.abspath(sys.argv[1]) if len(sys.argv) > 2 else tree(ROOT, W + "/eng-new", ENG_FILES, False, ENG_IGN)
NEW_PG = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else tree(PGROOT, W + "/pg-new", PG_FILES, False, ("*.bak-*", "__pycache__", ".git", "_to_delete"))
OLD_ENG = tree(ROOT, W + "/eng-old", ENG_FILES, True, ENG_IGN)
OLD_PG = tree(PGROOT, W + "/pg-old", PG_FILES, True, ("*.bak-*", "__pycache__", ".git", "_to_delete"))
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
        h = hashlib.sha256()
        h.update(repr([r[1] for r in c.execute('pragma table_info("%s")' % t)]).encode())
        for r in c.execute('select * from "%s" order by rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out

PORT = [8780]
def render(db, eng, pg):
    PORT[0] += 1; port = str(PORT[0])
    acct = sqlite3.connect(db).execute("select id from accounts").fetchone()[0]
    open(W + "/home/.helm_profile", "w").write(acct)
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, PORT=port, HELM_PG_ENGINE="real",
               COLUMNS="200", PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out")
    os.makedirs(W + "/advice-out", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=pg, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    body, st = "", None
    try:
        for _ in range(120):
            try: urllib.request.urlopen("http://127.0.0.1:%s/health" % port, timeout=2); break
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.5)
        r = urllib.request.urlopen("http://127.0.0.1:%s/today" % port, timeout=120)
        st, body = r.status, r.read().decode()
    except Exception as e:
        st, body = getattr(e, "code", "ERR"), str(e)
    finally:
        p.terminate(); log = p.communicate(timeout=10)[0].decode()
    return st, body, log

def sec(body, h2):
    """the HTML of the Today section whose <h2> starts with h2 ('' when absent)"""
    i = body.find("<h2>" + h2)
    if i < 0: return ""
    j = body.find('<div class="tdy-sec">', i)
    return body[i:j if j > 0 else len(body)]
def text(h):
    import html as H
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", h))).replace(" \u2197", "").replace(" ,", ",").strip()
def strip_new(body):
    """Today minus the parts s150 changes: the calls section, the roll boxes, the never-read headlines."""
    b = re.sub(r'<div class="tdy-roll">.*?</div>\s*</div>|<div class="tdy-roll">.*?</div>', "", body, flags=re.S)
    b = re.sub(r'\{# s150.*?#\}', "", b, flags=re.S)
    i = b.find("<h2>Calls"); j = b.find("<h2>Covered calls")
    k = i if i >= 0 else j
    if k >= 0:
        e = b.find('<div class="tdy-sec">\n  <h2>Holding</h2>', k)
        b = b[:b.rfind('<div class="tdy-sec">', 0, k)] + b[e:]
    b = b.replace("not read yet -- first reading at the next snapshot", "X").replace(
        "not read yet — first reading at the next snapshot", "X").replace(
        "no advice from an old reading", "X")
    b = re.sub(r"\.tdy-roll \{[^}]*\}", "", b)
    b = re.sub(r"\?v=\d+", "?v=N", b)          # static files cache-bust on their file times
    return re.sub(r"\s+", " ", b)

# ---------------------------------------------------------------- 1. live state, old vs new
A = copy("live"); before = dump(A)
st_o, old, log_o = render(A, OLD_ENG, OLD_PG)
st_n, new, log_n = render(A, NEW_ENG, NEW_PG)
ok(st_o == 200 and st_n == 200, "Today renders, old and new code (%s, %s)" % (st_o, st_n))
ok("Traceback" not in log_n and "Traceback" not in log_o, "no tracebacks in the board's log")
ok(dump(A) == before, "rendering Today changed no table in the copy")
ok(strip_new(old) == strip_new(new), "everything outside the changed parts is identical, old vs new")
open(W + "/today-old.html", "w").write(old); open(W + "/today-new.html", "w").write(new)
cA = sqlite3.connect(A); cA.row_factory = sqlite3.Row
never = [r["ticker"] for r in cA.execute("select ticker from positions p where book='REAL' and status='OPEN' and not exists "
                                         "(select 1 from checks c where c.position_id=p.id) order by ticker")]
print("       open positions never read:", never)
for t in never:
    ok(re.search(r">%s [^<]*— not read yet — first reading at the next snapshot<" % t, new)
       and not re.search(r">%s [^<]*not read yet" % t, old), "%s: 'not read yet' (new), not before" % t)
ok(new.count("not read yet") == len(never), "only never-read positions say so (%d)" % new.count("not read yet"))
calls = sec(new, "Calls")
pm = [r for r in cA.execute("select id, ticker from positions where book='REAL' and status='OPEN' and strategy='PMCC' order by ticker")]
ok(calls and not sec(old, "Covered calls") and not sec(old, "Calls"), "the calls section shows with no shares held (it didn't before)")
for r in pm:
    sh = cA.execute("select strike, expiration from legs where position_id=? and direction='SHORT' and status='OPEN'",
                    (r["id"],)).fetchone()
    d21 = date.fromordinal(date.fromisoformat(sh["expiration"][:10]).toordinal() - 21).isoformat()
    line = "%s PMCC" % r["ticker"]
    ok(line in text(calls) and ("the %s $%g call" % (sh["expiration"][5:10], sh["strike"])) in text(calls)
       and ("reaches 21 days on %s" % d21[5:]) in text(calls) and "/thesis/" + r["id"] in calls,
       "%s: short %g %s open, 21 days on %s, links its card" % (r["ticker"], sh["strike"], sh["expiration"][:10], d21))
from importlib.machinery import SourceFileLoader
sys.path.insert(0, NEW_ENG)
os.environ["HELM_ROOT"] = NEW_ENG; os.environ["HELM_DB"] = A
import helm.calltool as C
for h in C.pmcc_holdings(cA):
    ok(("above $%.2f (the width test)" % h["floor_strike"]) in text(calls), "%s: width floor $%.2f, from calltool" % (h["ticker"], h["floor_strike"]))
# roll boxes: exactly the needs-you items whose advice points to helm roll
needs = sec(new, "Needs your attention")
hint_items = re.findall(r'href="/thesis/([^"]+)">[^<]*</a>.*?(?=<div class="tdy |</div>\s*</div>\s*$)', needs, flags=re.S)
want = []
for blk in re.split(r'(?=<div class="tdy need">)', needs)[1:]:
    pid = re.search(r'/thesis/([^"]+)"', blk).group(1)
    if "helm roll" in re.sub(r'<div class="tdy-roll">.*', "", blk, flags=re.S):
        want.append(pid)
got = [re.search(r'/thesis/([^"]+)"', b).group(1) for b in re.split(r'(?=<div class="tdy need">)', needs)[1:] if 'class="tdy-roll"' in b]
print("       needs-you items pointing to helm roll:", [p.split("-")[0] for p in want])
ok(want and got == want, "a roll box under each and only those: %s" % [p.split("-")[0] for p in got])
lf = {r["position_id"]: r["run_at"] for r in cA.execute("select i.position_id, r.run_at from roll_items i join roll_runs r on r.id=i.run_id")}
for pid in want:
    blk = [b for b in re.split(r'(?=<div class="tdy need">)', needs) if "/thesis/" + pid in b][0]
    if pid in lf:
        ok(("run %s" % lf[pid][5:16]) in blk and (("from an earlier day" in blk) == (lf[pid][:10] < TODAY)),
           "%s: roll prices from %s, %s" % (pid.split("-")[0], lf[pid][:16], "labelled old" if lf[pid][:10] < TODAY else "today"))
    else:
        ok("No roll prices yet" in blk, "%s: no recorded roll -> the command" % pid.split("-")[0])

# ---------------------------------------------------------------- 2. fixtures on a copy: PMCC with no short, runs, a missing roll
B = copy("fix"); cB = sqlite3.connect(B); cB.row_factory = sqlite3.Row
nke = cB.execute("select id from positions where book='REAL' and status='OPEN' and strategy='PMCC' and ticker='NKE'").fetchone()
nvda = cB.execute("select id from positions where book='REAL' and status='OPEN' and strategy='PMCC' and ticker='NVDA'").fetchone()
if nke and nvda:
    nke, nvda = nke[0], nvda[0]
    cB.execute("update legs set status='CLOSED', close_price=0.44, close_date='2026-11-03' where position_id=? and direction='SHORT' and status='OPEN'", (nke,))
    cB.execute("update legs set status='CLOSED', close_price=1.70, close_date='2026-11-03' where position_id=? and direction='SHORT' and status='OPEN'", (nvda,))
    C.ensure_tables(cB)
    def run(rid, at, rejects, cands):
        cB.execute("insert into call_runs (id, run_at, rule_version, market_open, n_holdings, hypothetical, rejects_json, note) "
                   "values (?,?,?,?,?,?,?,?)", (rid, at, "test", 1, 2, 0, json.dumps(rejects), "s150 fixture"))
        for c in cands:
            cols = ",".join(c); cB.execute("insert into call_candidates (run_id,%s) values (?%s)" % (cols, ",?" * len(c)), (rid,) + tuple(c.values()))
    fl = {h["ticker"]: h for h in C.pmcc_holdings(cB)}
    nk = fl["NKE"]
    cand = lambda i, k, mid: {"kind": "pmcc", "ticker": "NKE", "choice": i, "hypothetical": 0, "contracts": 1, "expiry": "2026-12-18",
                              "dte": 45, "strike": k, "mid": mid, "delta": 0.25, "ann_return": 0.18, "if_called": 1500.0 + i,
                              "rest_price": 0.3, "earnings": "EARNINGS 2026-12-17 INSIDE THE TRADE -- allowed for a PMCC, flagged",
                              "position_id": nke, "long_strike": 27.5, "floor_strike": nk["floor_strike"]}
    run("calls_before", "2026-11-02 14:00:00", {"NKE (PMCC)": "a short is already open (the 42.5 call 2026-12-18) -- nothing to sell until it closes"}, [])
    cB.commit(); bB = dump(B)
    st, body, log = render(B, NEW_ENG, NEW_PG); cs = text(sec(body, "Calls"))
    ok(st == 200 and "NKE PMCC No short open — run cd ~/Projects/helm && helm calls" in cs,
       "no short, the only run is from before the short closed (and said 'already open') -> the command: %s" % cs[:160])
    ok(dump(B) == bB, "rendering changed no table (fixture copy)")
    run("calls_after", "%s 13:30:00" % TODAY, {"NVDA (PMCC)": "no call passes: strike not above the width-test floor -- delta outside 0.20-0.30"},
        [cand(1, 45.0, 0.95), cand(2, 47.5, 0.60)])
    cB.commit()
    st, body, log = render(B, NEW_ENG, NEW_PG); cs = text(sec(body, "Calls"))
    ok("1. the 12-18 $45 call ×1, 45 days, delta 0.25" in cs and "2. the 12-18 $47.5 call" in cs
       and "18% a year on the long's cost" in cs and "if called at least $1,501" in cs and "EARNINGS 2026-12-17 INSIDE" in cs,
       "NKE: today's run's two choices, with yearly return, if-called, earnings flag")
    ok("NVDA PMCC No short open · no call on the last helm calls run (%s): no call passes" % ("%s 13:30" % TODAY[5:]) in cs,
       "NVDA: the run's reason it has no call: %s" % cs[cs.find("NVDA PMCC"):][:150])
    ok("Covered calls" not in body and "no call: " not in sec(body, "Calls").split("PMCC")[0],
       "PMCC rejects are not listed as share holdings")
    # a roll box for a position with no recorded roll: drop one needs-you position's roll rows
    if want and want[0] in lf:
        cB.execute("delete from roll_contracts where position_id=?", (want[0],)); cB.execute("delete from roll_items where position_id=?", (want[0],)); cB.commit()
        st, body, log = render(B, NEW_ENG, NEW_PG)
        blk = [b for b in re.split(r'(?=<div class="tdy need">)', sec(body, "Needs your attention")) if "/thesis/" + want[0] in b][0]
        tk = want[0].split("-")[0]
        ok("No roll prices yet — run cd ~/Projects/helm && helm roll %s" % tk in text(blk), "%s with no recorded roll -> the command" % tk)
    # shares held: the shares part still shows as before
    cB.execute("insert into stock_positions (account_id, ticker, shares, cost_basis) values ((select id from accounts limit 1), 'ZZZT', 100, 10.0)"); cB.commit()
    st, body, log = render(B, NEW_ENG, NEW_PG)
    ok("From helm calls, run" in text(sec(body, "Calls")) and "NKE PMCC" in text(sec(body, "Calls")),
       "shares and PMCCs side by side in one section (shares part as before: the run's header)")
    ok("Traceback" not in log, "no tracebacks (fixtures)")
else:
    ok(False, "fixtures need open NKE and NVDA PMCCs")
print("verify_today_calls: %d checks, %d failed" % (n[0], len(fails)))
sys.exit(1 if fails else 0)
