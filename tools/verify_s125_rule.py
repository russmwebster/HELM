"""s125 step 3 (diagonal card review): the diagonal rule's state on the card.
Replaces the paper-rule box on diagonals with the rule's mode, every exit that
governs the mode with its reading, what is firing, and each firing kind's flag
status; the header pill and The read come from it; the two belief blocks say why
they are grey, truthfully.

Renders every /thesis/<id> through PG's own app.test_client() from a VACUUM INTO
copy -- BASELINE (helm/thesis.py.bak-20261001-s125c, helm-pg engine_store.py and
templates/thesis.html .bak-20261001-s125c) and CANDIDATE (working trees) -- in
child processes with PYTHONHASHSEED=0, then checks:
  * every non-diagonal card byte-identical (static ?v= stamps stripped);
  * on each OPEN diagonal, the firing kinds and the mode shown equal an
    INDEPENDENT call of exit_flags.diag_series on the latest check;
  * on REAL diagonals each firing kind's status agrees with the exit_flags rows
    (open flag / kept / acted), and PAPER says it writes no flags;
  * the pill reads "<MODE> · N firing" / "nothing firing"; The read names the rule
    and never says "nothing is signalling" while a kind fires;
  * no diagonal card says "predates thesis capture" or "slice 5".
Usage: python3 tools/verify_s125_rule.py
       python3 tools/verify_s125_rule.py render HELM_TREE PG_TREE OUT.json COPY   (child)
Perturbation run 2026-10-01: the state read from the FIRST check instead of the latest -> FAIL.
"""
import html, json, os, re, shutil, site, sqlite3, subprocess, sys, tempfile, types
USERSITE = site.getusersitepackages()
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PGROOT = os.path.join(os.path.dirname(HERE), "helm-pg")
LIVE = os.path.join(HERE, "data", "helm.db")
TAG = "bak-20261001-s125c"
DIAG = ("DIAGONAL", "PMCC", "DIAGONAL_PUT")
MODE = sys.argv[1] if len(sys.argv) > 1 else "main"

def render(tree, pg, out, copy):
    sys.path.append(USERSITE)
    tmp = tempfile.mkdtemp()
    os.environ.update(HELM_DB=copy, HELM_ROOT=tree, HOME=tmp)
    open(tmp + "/.helm_profile", "w").write("fidelity_9e60c8")
    yf = types.ModuleType("yfinance"); yf.Ticker = lambda t: types.SimpleNamespace(
        fast_info=types.SimpleNamespace(last_price=None), options=(), history=lambda *a, **k: None, info={})
    sys.modules["yfinance"] = yf
    sys.path.insert(0, tree); sys.path.insert(0, pg)
    import app as A, helm.thesis as T
    ids = [r[0] for r in sqlite3.connect(copy).execute("select id from positions order by id")]
    cl = A.app.test_client(); res = {"thesis": T.__file__, "app": A.__file__, "cards": {}}
    for pid in ids:
        r = cl.get("/thesis/" + pid)
        res["cards"][pid] = {"status": r.status_code, "html": r.get_data(as_text=True)}
    json.dump(res, open(out, "w"))

def txt(s):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()

def check(old, new, copy, tree):
    sys.path.insert(0, tree); os.environ["HELM_ROOT"] = tree
    from helm import exit_flags as EF, thesis as T
    A = json.load(open(old)); B = json.load(open(new))
    print("baseline :", A["thesis"], A["app"]); print("candidate:", B["thesis"], B["app"])
    A, B = A["cards"], B["cards"]
    c = sqlite3.connect(copy); c.row_factory = sqlite3.Row
    meta = {r["id"]: dict(r) for r in c.execute("select * from positions")}
    P = F = 0
    def ok(n, cond, det=""):
        nonlocal P, F
        P += bool(cond); F += (not cond)
        if not cond: print("FAIL", n, det)
    strip = lambda h: re.sub(r"\?v=\d+", "", h)
    n_open = 0
    for p, m in meta.items():
        ok("200 " + p, B[p]["status"] == 200 and len(B[p]["html"]) > 2000)
        h = B[p]["html"]
        if m["strategy"] not in DIAG:
            ok("non-diagonal identical " + p, strip(A[p]["html"]) == strip(h)); continue
        ok("no 'predates' " + p, "predates thesis capture" not in h)
        ok("no 'slice 5' " + p, "is slice 5" not in h)
        if m["status"] != "OPEN":
            continue
        n_open += 1
        legs = EF._legs(c, p); ser = EF.diag_series(legs, EF._diag_rows(c, p, legs))
        r, kinds, info = ser[-1]
        box = re.search(r'<span class="rule-tag">diagonal rule</span>(.*?)<div class="dealbox">', h, re.S)
        ok("rule box " + p, box is not None and "paper rule" not in txt(box.group(1))[:40])
        if not box: continue
        bt = txt(box.group(1))
        ok("mode " + p, bt.startswith(info["state"]), (bt[:40], info["state"]))
        shown = [T._DIAG_LABEL[k] for k in kinds]
        fired = re.findall(r"<b>▲ ([^<]+)</b>", box.group(1))
        ok("firing kinds = rule " + p, sorted(fired) == sorted(shown), (fired, shown))
        pill = txt(re.search(r'<span class="stamp[^>]*>(.*?)</span>', h, re.S).group(1))
        want = "%s · %s" % (info["state"], ("%d firing" % len(kinds)) if kinds else "nothing firing")
        ok("pill " + p, pill == want, (pill, want))
        rd = txt(re.search(r'<div class="readbox">(.*?)</div>', h, re.S).group(1))
        ok("read names the rule " + p, "The diagonal rule (%s)" % info["state"] in rd, rd[:80])
        ok("read never 'nothing is signalling' " + p, "nothing is signalling" not in rd)
        fl = [dict(x) for x in c.execute("select * from exit_flags where position_id=?", (p,))]
        for k in kinds:
            lab = T._DIAG_LABEL[k]
            seg = re.search(r"<b>▲ %s</b>(.*?)</div>" % re.escape(lab), box.group(1), re.S)
            st = txt(seg.group(1)) if seg else ""
            if m["book"] == "PAPER":
                ok("paper status " + p + k, "the paper book writes no flags" in st, st); continue
            mine = [f for f in fl if f["kind"] == k]
            if any(f["disposition"] is None for f in mine):
                ok("open flag shown " + p + k, "flag open since" in st, st)
            elif mine:
                last = sorted(mine, key=lambda f: (f["decided_date"] or "", f["flag_date"]))[-1]
                w = "kept" if last["disposition"] == "KEEP" else "acted on"
                ok("disposition shown " + p + k, (w + " " + (last["decided_date"] or "")[5:10]) in st, (st, w))
            else:
                ok("no-flag shown " + p + k, "no flag yet" in st, st)
    print("open diagonals checked: %d" % n_open)
    print("PASS %d  FAIL %d" % (P, F))
    return F

if MODE == "render":
    render(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]); sys.exit(0)
if MODE == "check":
    sys.exit(1 if check(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]) else 0)
for f in (os.path.join(HERE, "helm", "thesis.py." + TAG), os.path.join(PGROOT, "engine_store.py." + TAG),
          os.path.join(PGROOT, "templates", "thesis.html." + TAG)):
    if not os.path.exists(f):
        sys.exit("baseline %s is gone (removed under the .bak rule) -- this harness cannot run" % f)
t = tempfile.mkdtemp(prefix="s125c_"); copy = t + "/copy.db"
l = sqlite3.connect("file:" + LIVE + "?mode=ro", uri=True); l.execute("VACUUM INTO ?", (copy,)); l.close()
old, oldpg = t + "/old", t + "/oldpg"
os.makedirs(old); shutil.copytree(os.path.join(HERE, "helm"), old + "/helm")
shutil.copy(os.path.join(HERE, "helm", "thesis.py." + TAG), old + "/helm/thesis.py")
shutil.copytree(PGROOT, oldpg, ignore=shutil.ignore_patterns("*.bak*", "_to_delete", "*.log"))
shutil.copy(os.path.join(PGROOT, "engine_store.py." + TAG), oldpg + "/engine_store.py")
shutil.copy(os.path.join(PGROOT, "templates", "thesis.html." + TAG), oldpg + "/templates/thesis.html")
env = dict(os.environ, PYTHONPATH=USERSITE, PYTHONHASHSEED="0")
me = os.path.abspath(__file__)
subprocess.run([sys.executable, me, "render", old, oldpg, t + "/old.json", copy], env=env, check=True)
subprocess.run([sys.executable, me, "render", HERE, PGROOT, t + "/new.json", copy], env=env, check=True)
sys.exit(1 if check(t + "/old.json", t + "/new.json", copy, HERE) else 0)
