"""s156 step 3: the PMCC rule rows (helm/advice.py pmcc_rows), the card's data (helm/decide.py decide_pmcc), the
card's ruler (helm/thesis.py) and the open PMCC's new card (helm-pg pmcc_card.html, engine_store.py, app.py).

Board started as production starts it on a fresh DB copy, old code (.bak-20261108-s156) and new side by side from /tmp
copies. Expected numbers come from SQL on the copy, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s156/verify_pmcc_card.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s156v"
ENG_BAK = {"helm/advice.py": ".bak-20261108-s156", "helm/decide.py": ".bak-20261108-s156",
           "helm/thesis.py": ".bak-20261108-s156"}
PG_BAK = {"app.py": ".bak-20261108-s156", "engine_store.py": ".bak-20261108-s156"}
PG_NEW_ONLY = ["templates/pmcc_card.html"]
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
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for d, fs in ((NEW_ENG, list(ENG_BAK)), (NEW_PG, list(PG_BAK) + PG_NEW_ONLY)):
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
PORT = [8960]
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
paths = ["/thesis/" + i for i in PMCC + OTHER + CLOSED] + ["/today", "/book", "/watchlist"]
o, lo = render(OLD_ENG, OLD_PG, paths); nw, ln = render(NEW_ENG, NEW_PG, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "every page renders, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "no tracebacks in the board's log")
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
same = [p for p in paths if not p.startswith("/thesis/") or p[8:] not in PMCC]
diff = [p for p in same if norm(o[p][1]) != norm(nw[p][1])]
ok(not diff, "every page other than the open PMCCs' cards is identical old vs new (%d pages): %s" % (len(same), diff))
def text(b):
    b = re.sub(r"<style.*?</style>", " ", b, flags=re.S); b = re.sub(r"<svg.*?</svg>", " ", b, flags=re.S)
    b = re.sub(r"<[^>]+>", " ", b)
    return re.sub(r"\s+", " ", H.unescape(b)).strip()
T = date.today()
def money(x, sign=True): return ("−" if x < 0 else ("+" if sign and x > 0 else "")) + "$" + "{:,.0f}".format(abs(x))
def lc(leg_id):
    return cx.execute("select * from leg_checks where leg_id=? order by checked_at desc limit 1", (leg_id,)).fetchone()
for pid in PMCC:
    b = nw["/thesis/" + pid][1]; t = text(b); tk = pid.split("-")[0]
    ok('<div class="pc">' in b and "Your moves" not in t and "Where do I stand" not in t and "Every dollar" not in t
       and "The trade over time" not in t and "At a glance" not in t, "%s: the new card, none of the old sections" % tk)
    ok("Your moves" in text(o["/thesis/" + pid][1]), "%s: the old card had the old sections (control)" % tk)
    legs = [dict(r) for r in cx.execute("select * from legs where position_id=? and option_type in ('CALL','PUT')", (pid,))]
    L = next(l for l in legs if l["status"] == "OPEN" and l["direction"] == "LONG")
    Sh = next((l for l in legs if l["status"] == "OPEN" and l["direction"] == "SHORT"), None)
    rL = lc(L["id"]); rS = lc(Sh["id"]) if Sh else None
    asof = min(r["checked_at"] for r in (rL, rS) if r)[:16].replace("T", " ")
    ok(("Reading: %s" % asof) in t and t.count(asof) == 1, "%s: one clock, the legs' reading %s" % (tk, asof))
    long_paid = L["open_price"] * 100 * L["contracts"]
    net = sum(l["open_price"] * 100 * l["contracts"] - ((l["close_price"] or 0) * 100 * l["contracts"] if l["status"] != "OPEN" else 0)
              for l in legs if l["direction"] == "SHORT")
    close_mid = rL["current_price"] * 100 * L["contracts"] - (rS["current_price"] * 100 * Sh["contracts"] if Sh else 0)
    whole = -long_paid + net + close_mid
    chk = cx.execute("select pnl_unrealized from checks where position_id=? and data_quality='GOOD' order by checked_at desc limit 1", (pid,)).fetchone()[0]
    ok(abs(whole - chk) < 0.51, "%s: whole trade %s = the snapshot's own P&L %s" % (tk, money(whole), chk))
    ok("Long paid %s Shorts, net %s Close value (mid) %s Whole trade %s" % (money(-long_paid), money(net), money(close_mid), money(whole)) in t,
       "%s: money table from SQL" % tk)
    ok(("PMCC REAL %s" % money(whole)) in t, "%s: header whole trade" % tk)
    close_ba = rL["current_bid"] * 100 * L["contracts"] - (rS["current_ask"] * 100 * Sh["contracts"] if Sh else 0)
    dL = (date.fromisoformat(L["expiration"][:10]) - T).days
    ok(("Long +%d" % L["contracts"]) in t and (" %d $%.2f $%.2f" % (dL, L["open_price"], rL["current_price"])) in t,
       "%s: long leg row, %d days from today" % (tk, dL))
    even = L["strike"] + (long_paid - net) / (100 * L["contracts"])
    ok(("Even on the whole trade at $%.2f" % even) in t, "%s: even at the long's expiry $%.2f" % (tk, even))
    wfloor = L["strike"] + (long_paid - sum(l["open_price"] * 100 * l["contracts"] - (l["close_price"] or 0) * 100 * l["contracts"]
                                            for l in legs if l["direction"] == "SHORT" and l["status"] != "OPEN")) / (100 * L["contracts"])
    ok(("strike > $%.2f" % wfloor) in t, "%s: width floor $%.2f" % (tk, wfloor))
    ok("Book delta limit (half the sleeve) over" in t, "%s: the book's delta listed (over)" % tk)
    if Sh:
        dS = (date.fromisoformat(Sh["expiration"][:10]) - T).days
        bm, ba = -rS["current_price"] * 100 * Sh["contracts"], -rS["current_ask"] * 100 * Sh["contracts"]
        locked = (Sh["open_price"] - rS["current_price"]) * 100 * Sh["contracts"]
        ok(("Cash today (mid) $0 %s %s" % (money(bm), money(close_mid))) in t and
           ("Cash today (bid/ask) $0 %s %s" % (money(ba), money(close_ba))) in t and
           ("Locked in on the short (mid) — %s %s" % (money(locked), money(locked))) in t,
           "%s: choices at the mid and the bid/ask" % tk)
        tvL = rL["current_price"] - max(rL["current_price"] * 0 + (cx.execute("select spot_price from checks where position_id=? and data_quality='GOOD' and spot_price is not null order by checked_at desc limit 1", (pid,)).fetchone()[0] - L["strike"]), 0)
        rc = (rS["current_price"] / dS * 30) / (tvL / dL * 30)
        ok(("Rent cover (monthly, mid) %.1f×" % rc) in t, "%s: rent cover %.1f× (the short's monthly credit over the long's monthly time value)" % (tk, rc))
        sw, lw = -rS["theta"] * 700 * Sh["contracts"], rL["theta"] * 700 * L["contracts"]
        ok(("Short earns a week (theta) %s Long loses to time a week (theta) %s Net a week %s" % (money(sw), money(lw), money(sw + lw))) in t, "%s: rent vs decay a week" % tk)
        bb = Sh["open_price"] * 0.5
        ok("Next line" in t and ("buy back $%.2f" % (int(bb * 100 + 0.5 - 1e-9 + (0.5 if (bb * 100) % 1 >= 0.5 - 1e-9 else 0)) / 100) in t or "buy back $" in t),
           "%s: next lines listed" % tk)
        d21 = date.fromordinal(date.fromisoformat(Sh["expiration"][:10]).toordinal() - 21).isoformat()[5:]
        ok(("Short 21 days left %d days %s" % (dS, d21)) in t, "%s: 21-day line %s" % (tk, d21))
    else:
        ok("Short open none closed" in t and "Next short: width floor none passes" in t and "calls run 11-06 15:50" in t
           and "Next line" not in t, "%s: no short -> short open fired, width floor none passes (the 11-06 calls run)" % tk)
        ok(("Cash today (mid) — $0 %s" % money(close_mid)) in t and ("Cash today (bid/ask) — $0 %s" % money(close_ba)) in t,
           "%s: choices with no short" % tk)
        ok("Rent cover" not in t and "Short earns" not in t, "%s: no rent with no short" % tk)
    for word in (" you ", " would ", "HELM says", "HELM thinks", "HELM leans", "advises", " should "):
        ok(word not in " " + t + " ", "%s: no '%s' on the card" % (tk, word.strip()))
    ok("helm calls" not in t or not Sh, "%s: no command line with nothing to run" % tk)
# advice.json and helm advise unchanged
def adv(eng):
    env = dict(os.environ, HELM_ROOT=eng, HELM_DB=DB, PYTHONPATH=eng + ":/tmp/pylib", HOME=W + "/home", COLUMNS="200")
    j = subprocess.run([sys.executable, "-B", "-c", "import json;from helm import advice;d=advice.build();d.pop('generated_at',None);"
                        "print(json.dumps(d,sort_keys=True,default=str))"], cwd=eng, env=env, capture_output=True, text=True)
    c = subprocess.run([sys.executable, "-B", "helm.py", "advise"], cwd=eng, env=env, capture_output=True, text=True)
    return j.stdout, j.stderr, c.stdout
ja, ea, ca = adv(OLD_ENG); jb, eb, cb = adv(NEW_ENG)
ok(ja and ja == jb, "advice.json identical old vs new (%d bytes)%s" % (len(ja), (" " + ea[-300:] + eb[-300:]) if not ja else ""))
ok(ca and ca == cb, "`helm advise` identical old vs new (%d chars)" % len(ca))
ok(dump(DB) == before, "no table changed")
print("\n%d checks, %d failed" % (n[0], len(fails)))
for pid in PMCC:
    open(W + "/new-%s.html" % pid.split("-")[0], "w").write(nw["/thesis/" + pid][1])
sys.exit(1 if fails else 0)
