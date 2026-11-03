"""s148: the PMCC card shows the PMCC rules (helm.advice), not the run-off diagonals'.

A. pmcc_rule_rows at every rule's edge (the same comparisons as advice's PMCC path).
B. On a fresh DB copy, every open PMCC: advice's verb agrees with the card's firing rows.
C. The board started as production starts it, twice -- engine ENGINE_NEW (default ~/mnt/helm)
   and ENGINE_OLD (a tree with the .bak-20261102-s148 thesis/advice) -- on the same copy:
   NVDA's card reads "PMCC" and the PMCC rules; every other card and Today are byte-identical.
Usage: TMPDIR=/tmp python3 -B _s148/verify_pmcc_card.py [ENGINE_NEW] [PG_DIR]
"""
import hashlib, html, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); LIVE = HOME + "/mnt/helm"
NEW = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else LIVE
PG = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else HOME + "/mnt/helm-pg"
W = "/tmp/s148pc"; os.makedirs(W + "/home", exist_ok=True)
sys.path.insert(0, NEW); sys.path.insert(1, "/tmp/pylib")
print("engine new:", NEW, "| board:", PG)
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)

# ---- A. the rules at their edges
from helm import advice as A
print("advice from", A.__file__)
S = {"strike": 225.0, "open_price": 3.40, "option_type": "CALL"}
def fire(**k):
    base = dict(whole=100.0, long_cost=6290.0, long_strike=160.0, long_dte=400, spot=203.0, short=S,
                short_mark=3.0, short_dte=30, width_floor=None)
    base.update(k)
    return {x["kind"] for x in A.pmcc_rule_rows(**base) if x["firing"]}
ok(fire() == set(), "nothing fires on an ordinary reading")
ok("pos_stop" in fire(whole=-3145.0) and "pos_stop" not in fire(whole=-3144.99), "loss limit at exactly half the long's cost fires, a cent short does not")
ok("pmcc_take_profit" in fire(short_mark=1.70) and "pmcc_take_profit" not in fire(short_mark=1.71), "50% kept fires ($1.70 of $3.40), $1.71 does not")
ok("pmcc_21" in fire(short_dte=21) and "pmcc_21" not in fire(short_dte=22), "21 days left fires, 22 does not")
ok("pmcc_itm_expiry" in fire(spot=226.0, short_dte=7) and "pmcc_itm_expiry" not in fire(spot=226.0, short_dte=8)
   and "pmcc_itm_expiry" not in fire(spot=225.0, short_dte=7), "in the money with 7 days fires; 8 days, or at the strike, does not")
ok("pmcc_long_review" in fire(long_dte=90) and "pmcc_long_review" not in fire(long_dte=91), "the long's review at 90 days fires, 91 does not")
nr = A.pmcc_rule_rows(100.0, 6290.0, 160.0, 400, 203.0, short=None, width_floor=59.5)
ok([x["kind"] for x in nr if x["firing"]] == ["pmcc_no_short"] and "219.50" in [x for x in nr if x["kind"] == "pmcc_no_short"][0]["reading"],
   "no short: 'sell the next call', strike above $219.50 (160 + the width floor)")
ok(set(A.PMCC_RULE_VERB) == {x["kind"] for x in A.pmcc_rule_rows(1, 1, 1, 1, 1, short=S, short_mark=1, short_dte=1)} | {"pmcc_no_short"},
   "every rule row maps to an advice verb")

# ---- B. advice and the card agree on every open PMCC (fresh copy)
COPY = W + "/board.db"
if os.path.exists(COPY): os.remove(COPY)
sqlite3.connect("file:%s/data/helm.db?mode=ro" % LIVE, uri=True).execute("VACUUM INTO ?", (COPY,))
acct = sqlite3.connect(COPY).execute("select id from accounts").fetchone()[0]
open(W + "/home/.helm_profile", "w").write(acct)
os.environ.update(HELM_DB=COPY, HELM_ROOT=LIVE, HOME=W + "/home")
from helm import thesis as T, exit_flags as EF
c = sqlite3.connect(COPY); c.row_factory = sqlite3.Row
recs = {r["position_id"]: r for r in A.advise(account_id=acct)}
pm = [r[0] for r in c.execute("select id from positions where strategy='PMCC' and status='OPEN' and book='REAL'")]
ORDER = ["pos_stop", "pmcc_itm_expiry", "pmcc_take_profit", "pmcc_21", "pmcc_long_review", "pmcc_no_short"]
for pid in pm:
    lg = EF._legs(c, pid)
    rs = T.pmcc_rule_state(lg, EF._diag_rows(c, pid, lg), today=time.strftime("%Y-%m-%d"))
    first = next((k for k in ORDER if k in rs["firing"]), None)
    want = A.PMCC_RULE_VERB[first] if first else "HOLD"
    ok(rs and recs[pid]["verb"] == want, "%s: advice says %s, the card's rules say %s" % (pid, recs[pid]["verb"], want))
c.close()

# ---- C. the board, new engine vs old engine, same copy
OLD = W + "/oldengine"
shutil.rmtree(OLD, ignore_errors=True); os.makedirs(OLD + "/data")
shutil.copytree(NEW + "/helm", OLD + "/helm", ignore=shutil.ignore_patterns("*.bak-*", "__pycache__"))
for f in ("thesis.py", "advice.py"):
    shutil.copy(LIVE + "/helm/%s.bak-20261102-s148" % f, OLD + "/helm/" + f)
shutil.copy(NEW + "/helm.py", OLD + "/helm.py")
def board(engine, port):
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=engine, HELM_DB=COPY, PORT=str(port), HELM_PG_ENGINE="real",
               PYTHONPATH="/tmp/pylib", COLUMNS="200")
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=PG, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(80):
        try: urllib.request.urlopen("http://127.0.0.1:%d/today" % port, timeout=2); break
        except Exception: time.sleep(0.5)
    return p
def get(port, u):
    try: return urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, u), timeout=90).read().decode()
    except Exception as e: return "ERR %s" % e
cc = sqlite3.connect(COPY)
ids = [r[0] for r in cc.execute("select id from positions where book='REAL' and (status='OPEN' or strategy in "
                                "('DIAGONAL','PMCC') or closed_at >= '2026-10-01') order by id")]
cc.close()
pages = ["/today"] + ["/thesis/" + i for i in ids]
out = {}
for tag, eng, port in (("old", OLD, 8781), ("new", NEW, 8782)):
    p = board(eng, port)
    try:
        out[tag] = {u: get(port, u) for u in pages}
    finally:
        p.terminate(); log = p.communicate(timeout=10)[0].decode()
        ok("Traceback" not in log, "%s engine: no tracebacks in the board's log" % tag)
# the cards' charts draw clipPath ids from a random number; nothing else may differ
strip = lambda s: re.sub(r'\b(c[ab])\d+\b', r'\1N', s)
diff = [u for u in pages if u != "/thesis/" + (pm[0] if pm else "") and strip(out["old"][u]) != strip(out["new"][u])]
ok(not diff, "%d pages: Today and every card except the PMCC's byte-identical old vs new %s" % (len(pages), diff[:4]))
for pid in pm:
    t = out["new"]["/thesis/" + pid]
    txt = html.unescape(re.sub(r"<[^>]+>", " ", t))
    ok("· PMCC" in txt and "· Diagonal" not in txt, "%s: titled PMCC" % pid)
    ok(all(w in txt for w in ("Take the profit at 50%", "21 days left on the short", "Review the long", "In the money near expiry")),
       "%s: the PMCC rules are listed" % pid)
    ok(not re.search(r"(Breach|Worthless|Harvest at 25%|harvest at 25%)", txt), "%s: no breach, worthless or 25%% harvest" % pid)
    ok("could not be read" not in txt and "Traceback" not in t, "%s: no read error on the card" % pid)
    o = html.unescape(re.sub(r"<[^>]+>", " ", out["old"]["/thesis/" + pid]))
    ok("Harvest at 25%" in o, "(control: the old engine's card showed the diagonal rules)")
print("verify_pmcc_card: %d checks, %d failed" % (n[0], len(fails)))
sys.exit(1 if fails else 0)
