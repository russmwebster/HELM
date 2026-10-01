"""s125 step 1 (diagonal card review A4): open cards read only OPEN legs.
Renders /thesis/<id> through PG's own app.test_client() from a VACUUM INTO copy,
once with a BASELINE helm tree and once with the CANDIDATE tree, and compares.
Usage (device VM, HELM_ROOT layout as in the standing facts):
  python3 tools/verify_s125_legs.py            baseline = helm/thesis.py.bak-20261001-s125a,
                                               candidate = the working tree; both rendered
                                               in child processes with PYTHONHASHSEED=0
  python3 tools/verify_s125_legs.py render TREE OUT.json [OFFSET COUNT]   (child)
  python3 tools/verify_s125_legs.py compare OLD.json NEW.json               (asserts)
Perturbation used 2026-10-01: contract_line(pos, legs) restored -> 21 FAIL.
"""
import sys as _sys
if __name__ == "__main__" and (len(_sys.argv) < 2 or _sys.argv[1] == "main"):
    print("tools/verify_s125_legs.py: SUPERSEDED-s125e -- diagonals render thesis_diag.html since 2026-10-01; this harness checked the old diagonal card (step 1). Run tools/verify_s125_card.py and tools/verify_s125_loss_limit.py instead. Exiting without checking.")
    _sys.exit(0)

import hashlib, json, os, re, site, sqlite3, sys, tempfile, types
USERSITE = site.getusersitepackages()
MODE = sys.argv[1] if len(sys.argv) > 1 else "main"
PG = os.path.expanduser("~/mnt/helm-pg"); LIVE = os.path.expanduser("~/mnt/helm/data/helm.db")

def render(tree, out, off=0, cnt=10**6):
    sys.path.append(USERSITE)
    tmp = tempfile.mkdtemp(); copy = os.path.join(tmp, "c.db")
    l = sqlite3.connect("file:" + LIVE + "?mode=ro", uri=True); l.execute("VACUUM INTO ?", (copy,))
    ids = [r[0] for r in l.execute("select id from positions order by id limit ? offset ?", (cnt, off))]; l.close()
    os.environ.update(HELM_DB=copy, HELM_ROOT=tree, HOME=tmp)
    open(tmp + "/.helm_profile", "w").write("fidelity_9e60c8")
    yf = types.ModuleType("yfinance"); yf.Ticker = lambda t: types.SimpleNamespace(
        fast_info=types.SimpleNamespace(last_price=None), options=(), history=lambda *a, **k: None, info={})
    sys.modules["yfinance"] = yf
    sys.path.insert(0, tree); sys.path.insert(0, PG)
    import app as A, helm.thesis as T
    cl = A.app.test_client(); res = {"thesis": T.__file__, "cards": {}}
    for pid in ids:
        r = cl.get("/thesis/" + pid); h = r.get_data(as_text=True)
        res["cards"][pid] = {"status": r.status_code, "html": h}
    json.dump(res, open(out, "w"))
    print("rendered", len(ids), "with", T.__file__)

def _txt(h, cls):
    m = re.search(r'class="%s"[^>]*>(.*?)</' % cls, h, re.S); return m.group(1).strip() if m else None

def compare(a, b):
    A = {}; B = {}; ta = tb = None
    for f in a.split(","):
        d = json.load(open(f)); A.update(d["cards"]); ta = d["thesis"]
    for f in b.split(","):
        d = json.load(open(f)); B.update(d["cards"]); tb = d["thesis"]
    print("baseline:", ta, "\ncandidate:", tb)
    c = sqlite3.connect("file:" + LIVE + "?mode=ro", uri=True)
    meta = {r[0]: r[1:] for r in c.execute("select id,strategy,status,book from positions")}
    legs = {}
    for pid, st, k, exp in c.execute("select position_id,status,strike,expiration from legs"):
        legs.setdefault(pid, []).append((st, k, exp[:10] if exp else None))
    P = F = 0
    def ok(n, cond, det=""):
        nonlocal P, F
        P += bool(cond); F += (not cond)
        if not cond: print("FAIL", n, det)
    ok("same ids", set(A) == set(B), len(A) ^ len(B) if False else "")
    diag = ("DIAGONAL", "PMCC", "DIAGONAL_PUT")
    changed = [p for p in A if A[p]["html"] != B[p]["html"]]
    for p in A:
        ok("200 " + p, B[p]["status"] == 200 and len(B[p]["html"]) > 2000, B[p]["status"])
        if meta[p][0] not in diag:
            ok("non-diagonal byte-identical " + p, A[p]["html"] == B[p]["html"])
    for p, (strat, st, bk) in meta.items():
        if strat not in diag:
            continue
        h = B[p]["html"]
        # step 2 (s125b) restores a break-even on LONG-ONLY diagonals, from the basis
        ok("no break-even on diagonal " + p, "<b>Break-even</b>" not in h or st != "OPEN"
           or sum(1 for s_, k, e in legs[p] if s_ == "OPEN") == 1)
        ok("no expiry caveat on diagonal " + p, "a caveat on the quotes" not in h or st != "OPEN"
           or sum(1 for s_, k, e in legs[p] if s_ == "OPEN") == 1)
        if st != "OPEN":
            continue
        open_exps = {e for s, k, e in legs[p] if s == "OPEN"}
        closed = [(k, e) for s, k, e in legs[p] if s != "OPEN"]
        ok("Expires = open legs " + p, all(e in h for e in open_exps))
        cm = re.search(r'class="contract"[^>]*>(.*?)</', h, re.S)
        n_open = sum(1 for s_, k, e in legs[p] if s_ == "OPEN")
        n_shown = len(cm.group(1).split(" / ")) if cm else 0
        ok("contract line = open legs " + p, n_shown == n_open, (n_shown, n_open, cm and cm.group(1)))
        m = re.search(r"<b>Expires</b>(.*?)</span>", h, re.S)
        shown = set(re.findall(r"\d{4}-\d{2}-\d{2}", m.group(1))) if m else set()
        ok("Expires exactly the open legs " + p, shown == open_exps, (shown, open_exps))
        # every leg still drawn in the two-leg panels (they count closed shorts)
        ok("panels kept " + p, "The two legs, apart" in h)
        ok("panel lines unchanged " + p,
           re.findall(r"<li>(?:Long|Short) [^<]*</li>", A[p]["html"]) == re.findall(r"<li>(?:Long|Short) [^<]*</li>", h))
    print("changed cards:", len(changed), "of", len(A), "· diagonal:", sum(meta[p][0] in diag for p in changed))
    print("PASS %d  FAIL %d" % (P, F))

if MODE == "main":
    import shutil, subprocess
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bak = os.path.join(here, "helm", "thesis.py.bak-20261001-s125a")
    if not os.path.exists(bak):
        sys.exit("baseline %s is gone (removed under the .bak rule) -- this harness cannot run" % bak)
    t = tempfile.mkdtemp(prefix="s125_")
    old = os.path.join(t, "old"); os.makedirs(old)
    shutil.copytree(os.path.join(here, "helm"), os.path.join(old, "helm"))
    shutil.copy(bak, os.path.join(old, "helm", "thesis.py"))
    env = dict(os.environ, PYTHONPATH=USERSITE, PYTHONHASHSEED="0")
    for tree, out in ((old, t + "/old.json"), (here, t + "/new.json")):
        subprocess.run([sys.executable, os.path.abspath(__file__), "render", tree, out], env=env, check=True)
    compare(t + "/old.json", t + "/new.json")
elif MODE == "render":
    render(sys.argv[2], sys.argv[3], *(int(x) for x in sys.argv[4:6]))
else:
    compare(sys.argv[2], sys.argv[3])
