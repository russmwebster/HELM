#!/usr/bin/env python3
"""verify_w88s5 -- the diagonal card's two panels (W88 slice 5 / W180 step 4) and
W204 (a re-sold short is live on its booking day only from its booking time).

Runs on a VACUUM INTO copy; blocks IB and stubs yfinance before importing helm;
its own HOME holding the profile id; PG through app.test_client(), the module
production starts. The baseline is the .bak-20260930-w88s5 files, loaded in a
separate process from a temp tree. Checks:
  1. W204: against the .bak rule, only rows before a re-sell's booking time
     change; no position's latest-row kinds and no latest-day new flag change.
  2. diag_panels on EVERY diagonal (open and closed): one short segment per short
     leg; no point outside its leg's live window; AA's 09-25 short starts at the
     15:16 check (mark 0.69), not at the long's $2.65; AA/EQT/UNH first shorts
     are lines, not bands; a fixture short with no marks draws "no readings".
  3. PG /thesis: 200 + body first; a real diagonal carries the panels with two
     "sold" labels per two shorts; with the new block removed the diagonal page
     equals the baseline page; open LONG_CALL, open CSP and a closed LONG_CALL
     pages are byte-identical to the baseline.
  4. Perturb: W204 switched off -> the EQT re-sell check fails (it was booked
     after 09-25's last check); restore; the unperturbed control runs LAST.
Usage: python3 tools/verify_w88s5.py
"""
import glob, os, re, shutil, site, sqlite3, subprocess, sys, tempfile, types, json
USERSITE = site.getusersitepackages()   # before HOME moves: flask lives here

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
PG = os.path.join(os.path.dirname(ROOT), "helm-pg")
TAG = "bak-20260930-w88s5"
SRC = os.path.join(ROOT, "data", "helm.db")
TMP = tempfile.mkdtemp(prefix="w88s5_"); COPY = os.path.join(TMP, "copy.db")
MODE = sys.argv[1] if len(sys.argv) > 1 else "main"

if MODE == "main":
    live = sqlite3.connect("file:" + SRC + "?mode=ro", uri=True)
    flags_before = live.execute("select count(*), group_concat(id||':'||coalesce(disposition,''), ',') from exit_flags").fetchone()
    live.execute("VACUUM INTO ?", (COPY,)); live.close()
else:
    COPY = sys.argv[2]

def _env(root):
    os.environ["HELM_DB"] = COPY; os.environ["HELM_ROOT"] = root
    os.environ["HOME"] = TMP
    open(os.path.join(TMP, ".helm_profile"), "w").write("fidelity_9e60c8")
    _yf = types.ModuleType("yfinance")
    _yf.Ticker = lambda t: types.SimpleNamespace(fast_info=types.SimpleNamespace(last_price=None),
                                                 options=(), history=lambda *a, **k: None, info={})
    sys.modules["yfinance"] = _yf
    try:
        import ib_insync
        def _no(*a, **k):
            raise ConnectionRefusedError("blocked by verify_w88s5")
        ib_insync.IB.connect = _no
    except Exception:
        pass

def render(pg_dir, ids):
    sys.path.insert(0, pg_dir)
    import app as _app
    cl = _app.app.test_client()
    out = {}
    for pid in ids:
        r = cl.get("/thesis/" + pid)
        out[pid] = (r.status_code, r.get_data(as_text=True))
    return out, _app.__file__

# ---- child: render with the BASELINE tree -------------------------------------
if MODE == "old":
    oldroot, oldpg, ids = sys.argv[3], sys.argv[4], json.loads(sys.argv[5])
    _env(oldroot); sys.path.insert(0, oldroot)
    out, f = render(oldpg, ids)
    import helm.thesis as _t
    print(json.dumps({"out": out, "app": f, "thesis": _t.__file__}))
    sys.exit(0)

_env(ROOT); sys.path.insert(0, ROOT)
P = F = 0
def ok(name, cond, detail=""):
    global P, F
    P += bool(cond); F += (not cond)
    print(("  PASS " if cond else "  FAIL ") + name + ("" if cond else "   -> %r" % (detail,)))

from importlib.machinery import SourceFileLoader
from helm import exit_flags as EF, thesis as T
OLD = SourceFileLoader("ef_old", os.path.join(ROOT, "helm", "exit_flags.py." + TAG)).load_module()
print("loaded: %s\n        %s\nbaseline: *.%s\nDB copy: %s" % (EF.__file__, T.__file__, TAG, COPY))
c = sqlite3.connect(COPY)
DIAG = "strategy in ('DIAGONAL','PMCC','DIAGONAL_PUT')"
pids = [r[0] for r in c.execute("select id from positions where " + DIAG + " order by id")]
def pid_of(tk, book="REAL", status="OPEN"):
    r = c.execute("select id from positions where ticker=? and book=? and status=? and " + DIAG,
                  (tk, book, status)).fetchone()
    return r[0] if r else None

# ---- 1. W204 --------------------------------------------------------------
print("\n-- 1. W204 against the .bak rule (%d diagonals)" % len(pids))
bad_rows, latest_diff, flag_diff, changed = [], [], [], 0
for pid in pids:
    ln, lo = EF._legs(c, pid), OLD._legs(c, pid)
    lf = {l["id"]: l["live_from"] for l in ln if l.get("live_from")}
    rn, ro = EF._diag_rows(c, pid, ln), OLD._diag_rows(c, pid, lo)
    sn, so = EF.diag_series(ln, rn), OLD.diag_series(lo, ro)
    for (a, ka, _), (b, kb, _) in zip(sn, so):
        if ka != kb or a["marks"] != b["marks"]:
            changed += 1
            t = a["checked_at"]
            gone = set(b["marks"]) - set(a["marks"])
            if not gone or any(not (g in lf and lf[g][:10] == t[:10] and t < lf[g]) for g in gone) \
                    or set(a["marks"]) - set(b["marks"]):
                bad_rows.append((pid, t))
    if sn and sn[-1][1] != so[-1][1]:
        latest_diff.append(pid)
    op, af = EF._state(c, pid)
    only = rn[-1]["checked_at"][:10] if rn else None
    fn = [(k, r["checked_at"]) for k, r, i in EF.diag_new_flags(ln, rn, op, af, only_day=only)]
    fo = [(k, r["checked_at"]) for k, r, i in OLD.diag_new_flags(lo, ro, op, af, only_day=only)]
    if fn != fo:
        flag_diff.append(pid)
ok("rows changed: %d, every one a check before a re-sell's booking time" % changed,
   changed > 0 and not bad_rows, bad_rows[:5])
ok("control: rows DID change (a zero here would mean W204 is not loaded)", changed > 0)
ok("no position's latest-row kinds changed", not latest_diff, latest_diff)
ok("no latest-day new flag changed", not flag_diff, flag_diff)
ok("a leg booked WITH its position gets no live_from (AA's s111 re-create keeps 09-01 15:16)",
   not any(l.get("live_from") for l in EF._legs(c, pid_of("AA")) if l["id"].endswith("66A4") or l["direction"] == "LONG"))

# ---- 2. panels ------------------------------------------------------------
print("\n-- 2. diag_panels on every diagonal")
seg_bad, win_bad, crash = [], [], []
for pid in pids:
    try:
        legs = EF._legs(c, pid); pn = T.diag_panels(legs, EF._diag_rows(c, pid, legs))
        if pn is None:
            continue
        T.diag_panels_svg(pn); T.diag_panel_lines(pn)
    except Exception as e:
        crash.append((pid, repr(e))); continue
    nsh = sum(1 for l in legs if EF._is_opt(l) and l["direction"] == "SHORT")
    if len(pn["shorts"]) != nsh:
        seg_bad.append((pid, len(pn["shorts"]), nsh))
    byid = {l["id"]: l for l in legs}
    for s in pn["shorts"] + pn["longs"]:
        l = byid[s["leg"]]
        od = str(l.get("open_date") or "")[:10]; cd = str(l.get("close_date") or "")
        for p in s["points"]:
            if (od and p["date"] < od) or (cd and l["status"] != "OPEN" and p["date"] + "T" + p["time"] >= cd[:16] and p["date"] > cd[:10]):
                win_bad.append((s["leg"], p["date"]))
ok("no diagonal raised (%d built)" % len(pids), not crash, crash[:3])
ok("one short segment per short leg, every diagonal", not seg_bad, seg_bad[:5])
ok("no point outside its leg's live window", not win_bad, win_bad[:5])

def aa_new_short_ok():
    pid = pid_of("AA"); legs = EF._legs(c, pid)
    pn = T.diag_panels(legs, EF._diag_rows(c, pid, legs))
    s = [x for x in pn["shorts"] if x["open_date"] == "2026-09-25"][0]
    p0 = s["points"][0]
    return p0["date"] == "2026-09-25" and p0["time"] == "15:16" and abs(p0["mark"] - 0.69) < 1e-9, p0
r, p0 = aa_new_short_ok()
ok("AA 09-25 short starts at 15:16 on mark 0.69 (not the long's 2.65)", r, p0)

def eqt_new_short_ok():
    # EQT's re-sell was booked 15:20 on 09-25, AFTER that day's last check (15:18).
    # The chart plots the last check per day, so this is where W204 shows: the
    # new short's first point must be 09-28, not a 09-25 point on the long's mark.
    pid = pid_of("EQT"); legs = EF._legs(c, pid)
    pn = T.diag_panels(legs, EF._diag_rows(c, pid, legs))
    s = [x for x in pn["shorts"] if x["open_date"] == "2026-09-25"][0]
    return s["points"][0]["date"] == "2026-09-28", s["points"][0]
r, p0 = eqt_new_short_ok()
ok("EQT 09-25 short (booked 15:20) starts 09-28, not on the long's 09-25 mark", r, p0)
for tk, want in (("AA", 10), ("EQT", 10), ("UNH", 8)):
    pid = pid_of(tk); legs = EF._legs(c, pid); pn = T.diag_panels(legs, EF._diag_rows(c, pid, legs))
    first = pn["shorts"][0]
    ok("%s first short drawn as a line (%d days, not a band)" % (tk, len(first["points"])),
       not first["no_readings"] and len(first["points"]) >= want, len(first["points"]))
# fixture: a short with NO reading at all
pid = pid_of("B"); legs = EF._legs(c, pid); rows = EF._diag_rows(c, pid, legs)
ghost = dict([l for l in legs if l["direction"] == "SHORT"][0]); ghost["id"] = "FIXTURE-SH-NONE"
pn = T.diag_panels(legs + [ghost], rows)
svg = T.diag_panels_svg(pn)
ok("a short with no readings is a band labelled 'no readings'",
   any(s["no_readings"] for s in pn["shorts"]) and svg.count(">no readings<") == 1)
ok("control: the real B card has no band", ">no readings<" not in T.diag_panels_svg(T.diag_panels(legs, rows)))
dvn = pid_of("DVN", "PAPER"); legs = EF._legs(c, dvn); pn = T.diag_panels(legs, EF._diag_rows(c, dvn, legs))
ok("DVN paper short past expiry says 'not yet settled'",
   any("not yet settled" in l for l in T.diag_panel_lines(pn)) and ">expired 09-25 — not yet settled<" in T.diag_panels_svg(pn))

# ---- 3. PG ----------------------------------------------------------------
print("\n-- 3. PG /thesis via app.test_client()")
def one(sql):
    r = c.execute(sql).fetchone(); return r[0] if r else None
ids = {"diag": pid_of("AA"), "b": pid_of("B"),
       "lc": one("select id from positions where strategy='LONG_CALL' and status='OPEN' order by id limit 1"),
       "csp": one("select id from positions where strategy='CSP' and status='OPEN' order by id limit 1"),
       "lc_closed": one("select id from positions where strategy='LONG_CALL' and status='CLOSED' order by closed_at desc limit 1")}
new, appf = render(PG, list(ids.values()))
print("  app:", appf)
for k, pid in ids.items():
    code, body = new[pid]
    ok("%s %s -> 200 with a body" % (k, pid), code == 200 and "<html" in body.lower() and len(body) > 2000, (code, len(body)))
d = new[ids["diag"]][1]
ok("AA diagonal page carries the panels", "The two legs, apart" in d and d.count("aria-label=\"Two panels") == 1)
ok("AA: two 'sold' labels for two shorts", len(re.findall(r">sold C [0-9.]+ · ", d)) == 2,
   re.findall(r">sold [^<]*<", d))
ok("AA: the long's stop and trail lines are named", "stop -50%" in d and ">trail " in d)
ok("no panel error shown", "could not be drawn" not in d)

# baseline pages, in a separate process, from a temp tree holding the .bak files
oldroot = os.path.join(TMP, "oldroot"); oldpg = os.path.join(TMP, "oldpg")
shutil.copytree(os.path.join(ROOT, "helm"), os.path.join(oldroot, "helm"),
                ignore=shutil.ignore_patterns("__pycache__", "*.bak*"))
for f in ("exit_flags.py", "thesis.py"):
    shutil.copy(os.path.join(ROOT, "helm", f + "." + TAG), os.path.join(oldroot, "helm", f))
shutil.copytree(PG, oldpg, ignore=shutil.ignore_patterns(".git", "__pycache__", "_to_delete", "*.log", "*.bak*"))
shutil.copy(os.path.join(PG, "engine_store.py." + TAG), os.path.join(oldpg, "engine_store.py"))
shutil.copy(os.path.join(PG, "templates", "thesis.html." + TAG), os.path.join(oldpg, "templates", "thesis.html"))
# Both trees render in a child process with ONE hash seed: close_svg names its
# clip paths from hash(), which Python randomizes per process (pre-existing).
_cenv = dict(os.environ, PYTHONPATH=USERSITE, PYTHONHASHSEED="0")
def _child(root, pg):
    res = subprocess.run([sys.executable, os.path.abspath(__file__), "old", COPY, root, pg,
                          json.dumps(list(ids.values()))], capture_output=True, text=True,
                         timeout=150, env=_cenv)
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])
    except Exception:
        print(res.stdout[-800:], res.stderr[-1500:]); return None
old = _child(oldroot, oldpg)
cur = _child(ROOT, PG)
ok("current tree re-rendered in a child with the same seed", cur is not None and cur["thesis"].startswith(ROOT), cur and cur["thesis"])
if cur:
    new = {k: tuple(v) for k, v in cur["out"].items()}
ok("baseline process ran from the temp tree", old is not None and old["thesis"].startswith(oldroot), old and old["thesis"])
if old:
    o = old["out"]
    css = ("\n  .ct-diag { margin-top:14px; padding-top:10px; border-top:1px solid var(--border); }"
           "\n  .ct-diag-lines { margin:6px 0 6px 1.1em; padding:0; }\n  .ct-diag-lines li { margin:2px 0; }")
    for k in ("lc", "csp", "lc_closed"):
        ok("%s page carries the three new CSS lines once" % k, new[ids[k]][1].count(css) == 1)
    for k in ("lc", "csp", "lc_closed"):
        _a, _b = o[ids[k]][1], new[ids[k]][1].replace(css, "")
        _i = next((i for i, (x, y) in enumerate(zip(_a, _b)) if x != y), None)
        ok("%s page byte-identical to the baseline apart from those CSS lines" % k, _a == _b,
           None if _i is None else (_a[_i-60:_i+40], _b[_i-60:_i+40]))
    for k in ("diag", "b"):
        stripped = re.sub(r"\n      <div class=\"ct-diag\">.*?\n      </div>\n", "\n", new[ids[k]][1], count=1, flags=re.S).replace(css, "")
        _i = next((i for i, (a, b) in enumerate(zip(stripped, o[ids[k]][1])) if a != b), None)
        ok("%s diagonal page == baseline once the new block is removed" % k, stripped == o[ids[k]][1],
           None if _i is None else (o[ids[k]][1][_i-60:_i+40], stripped[_i-60:_i+40]))
        ok("control: %s baseline page has no panels" % k, "The two legs, apart" not in o[ids[k]][1])

# ---- 4. perturb, restore, control last ---------------------------------------
print("\n-- 4. perturb / restore / control")
_orig = EF._live_at
EF._live_at = OLD._live_at
r, p0 = eqt_new_short_ok()
ok("PERTURBED (W204 off): the EQT check now FAILS, as it must (%s %s)" % (p0["date"], p0["mark"]), not r, p0)
EF._live_at = _orig
r, p0 = eqt_new_short_ok()
ok("restored: the EQT check passes again", r, p0)
r, p0 = aa_new_short_ok()
ok("control, last: the AA check passes", r, p0)

live = sqlite3.connect("file:" + SRC + "?mode=ro", uri=True)
ok("live DB exit_flags unchanged", live.execute("select count(*), group_concat(id||':'||coalesce(disposition,''), ',') from exit_flags").fetchone() == flags_before)
print("\n%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
