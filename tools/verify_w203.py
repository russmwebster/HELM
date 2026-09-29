#!/usr/bin/env python3
"""verify_w203 -- preset hold reasons in the board's keep box, and the diagonal
rule box that no longer reads "would hold".

Runs on a VACUUM INTO copy; blocks IB and stubs yfinance before importing helm;
never POSTs a keep (the keep path itself is unchanged). Checks:
  1. rule_read.explain: diagonal no-fire headline/lines are the W203 wording;
     a firing diagonal and every other family are byte-identical to the .bak.
  2. PG /thesis/<real diagonal> shows the new headline, not "would hold".
  3. PG /positions carries the preset code, no window.prompt keep, and every
     <script> passes `node --check`; the preset lists per kind are as specified.
  4. The live DB's exit_flags dispositions are unchanged.
Usage: python3 tools/verify_w203.py
"""
import glob, os, re, sqlite3, subprocess, sys, tempfile, types, json

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
PG = os.path.join(os.path.dirname(ROOT), "helm-pg")
sys.path.insert(0, ROOT)
SRC = os.path.join(ROOT, "data", "helm.db")
TMP = tempfile.mkdtemp(prefix="w203_"); COPY = os.path.join(TMP, "copy.db")
live = sqlite3.connect("file:" + SRC + "?mode=ro", uri=True)
disp_before = live.execute("select count(*), group_concat(id||':'||coalesce(disposition,''), ',') from exit_flags").fetchone()
live.execute("VACUUM INTO ?", (COPY,)); live.close()
os.environ["HELM_DB"] = COPY; os.environ["HELM_ROOT"] = ROOT
# The board needs an active account (~/.helm_profile). Give this run its own HOME
# holding the live profile id, so nothing reads or writes the real home.
os.environ["HOME"] = TMP
open(os.path.join(TMP, ".helm_profile"), "w").write("fidelity_9e60c8")

_yf = types.ModuleType("yfinance")
_yf.Ticker = lambda t: types.SimpleNamespace(fast_info=types.SimpleNamespace(last_price=None),
                                             options=(), history=lambda *a, **k: None, info={})
sys.modules["yfinance"] = _yf
IB = []
try:
    import ib_insync
    def _no(*a, **k):
        IB.append(1); raise ConnectionRefusedError("blocked by verify_w203")
    ib_insync.IB.connect = _no
except Exception:
    pass

P = F = 0
def ok(name, cond, detail=""):
    global P, F
    P += bool(cond); F += (not cond)
    print(("  PASS " if cond else "  FAIL ") + name + ("" if cond else "   -> %r" % (detail,)))

from helm import rule_read as NEW
from importlib.machinery import SourceFileLoader
baks = sorted(glob.glob(os.path.join(ROOT, "helm", "rule_read.py.bak-w203-*")))
print("loaded: %s\nbaseline: %s\nDB copy: %s" % (NEW.__file__, baks[-1] if baks else None, COPY))
OLD = SourceFileLoader("rule_read_old", baks[-1]).load_module()

print("\n-- 1. rule_read.explain")
d = NEW.explain(None, "DIAGONAL", 0.12, 20, target_pct=0.5, dte_exit=21)
ok("diagonal no-fire headline", d["headline"] == "No paper rule for diagonals yet — this is not a hold signal.", d["headline"])
ok("diagonal no-fire does not fire", d["fires"] is False)
ok("diagonal lines name W180 step 7 and the 21-DTE calendar",
   any("W180 step 7" in l and "21 DTE" in l for l in d["lines"]), d["lines"])
ok("diagonal lines point at the exit flags", any("exit flags" in l for l in d["lines"]), d["lines"])
ok("control: the OLD module said 'would hold' for the same diagonal",
   OLD.explain(None, "DIAGONAL", 0.12, 20, target_pct=0.5, dte_exit=21)["headline"] == "The paper rule would hold this.")
diff = []
for strat in ("CSP", "COVERED_CALL", "BULL_PUT_SPREAD", "IRON_CONDOR", "BULL_CALL_SPREAD",
              "LONG_CALL", "LONG_PUT", "LONG_STRADDLE", "DIAGONAL", "PMCC", "DIAGONAL_PUT"):
    for reason in (None, "PROFIT_TARGET", "DTE_MANAGE", "EXPIRY", "GIVE_BACK", "STOP_LOSS", "DTE_21", "DTE_7"):
        if reason is None and strat in ("DIAGONAL", "PMCC", "DIAGONAL_PUT"):
            continue
        for pct in (None, -0.3, 0.2, 0.6):
            args = (reason, strat, pct, 25)
            kw = dict(arms={"give_back": {"hwm": 0.3, "floor": 0.1}, "v3": {}}, target_pct=0.5, dte_exit=21)
            if NEW.explain(*args, **kw) != OLD.explain(*args, **kw):
                diff.append(args)
ok("every other case byte-identical to the .bak (%s)" % ("0 differ" if not diff else len(diff)), not diff, diff[:5])
for s in ("PMCC", "DIAGONAL_PUT"):
    ok("%s gets the diagonal wording too" % s,
       NEW.explain(None, s, 0.1, 20, target_pct=0.5, dte_exit=21)["headline"].startswith("No paper rule for diagonals"))

print("\n-- 2. PG thesis card")
sys.path.insert(0, PG)
import app as pgapp
cl = pgapp.app.test_client()
db = sqlite3.connect(COPY)
dpid = db.execute("select id from positions where book='REAL' and status='OPEN' and strategy='DIAGONAL' order by opened_at limit 1").fetchone()
cpid = db.execute("select id from positions where book='REAL' and status='OPEN' and strategy='CSP' order by opened_at limit 1").fetchone()
if dpid:
    rr = cl.get("/thesis/" + dpid[0]); h = rr.get_data(as_text=True)
    ok("diagonal card returns 200 with a body", rr.status_code == 200 and len(h) > 2000, (rr.status_code, len(h)))
    ok("diagonal card (%s) shows the W203 headline" % dpid[0], "No paper rule for diagonals yet" in h)
    ok("diagonal card no longer says 'would hold this'", "The paper rule would hold this." not in h)
else:
    ok("an open real diagonal exists to test", False)
if cpid:
    rr = cl.get("/thesis/" + cpid[0]); h = rr.get_data(as_text=True)
    ok("control: CSP card returns 200", rr.status_code == 200 and len(h) > 2000, (rr.status_code, len(h)))
    ok("control: CSP card still has its rule box", "paper rule" in h)
    ok("control: CSP card (%s) has no diagonal wording" % cpid[0], "No paper rule for diagonals" not in h)

print("\n-- 3. PG positions page")
rr = cl.get("/positions"); h = rr.get_data(as_text=True)
ok("/positions renders (200)", rr.status_code == 200 and "renderPending" in h, (rr.status_code, len(h)))
ok("presets present", "KEEP_PRESETS" in h and "openKeepForm" in h)
ok("keep no longer uses window.prompt", 'window.prompt("Holding' not in h)
ok("still posts to /api/pending/keep", "/api/pending/keep" in h)
scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", h, re.S)
js = os.path.join(TMP, "page.js"); open(js, "w").write("\n;\n".join(scripts))
r = subprocess.run(["node", "--check", js], capture_output=True, text=True)
ok("all inline scripts pass node --check (%d blocks)" % len(scripts), r.returncode == 0, r.stderr[:300])
m = re.search(r"(var KEEP_PRESETS = .*?\nfunction keepPresets\(kind\)\{.*?\n\})", h, re.S)
probe = os.path.join(TMP, "probe.js")
open(probe, "w").write((m.group(1) if m else "") + """
console.log(JSON.stringify({h25: keepPresets('harvest25'), h50: keepPresets('harvest50'),
  ls: keepPresets('long_stop'), th: keepPresets('thesis'), br: keepPresets('breach'), tg: keepPresets('target')}));""")
r = subprocess.run(["node", probe], capture_output=True, text=True)
try:
    got = json.loads(r.stdout)
except Exception:
    got = {}
ANY = ["Unsure; holding by default", "Following HELM's lean; no view of my own"]
ok("harvest25 presets", got.get("h25") == ["Waiting for 50%", "Little premium left; letting it expire",
   "Short still protects the long; keeping it on"] + ANY, got.get("h25"))
ok("harvest50 drops 'Waiting for 50%'", got.get("h50") and "Waiting for 50%" not in got["h50"] and len(got["h50"]) == 4, got.get("h50"))
ok("long_stop presets", got.get("ls") == ["Still believe in the stock; giving it time",
   "Earnings or event coming; waiting for it", "Recovered since the flag"] + ANY, got.get("ls"))
ok("thesis uses the loss set", got.get("th") == got.get("ls"))
ok("other kinds (breach, target) get the universal pair only", got.get("br") == ANY and got.get("tg") == ANY, (got.get("br"), got.get("tg")))

print("\n-- 4. isolation")
live = sqlite3.connect("file:" + SRC + "?mode=ro", uri=True)
disp_after = live.execute("select count(*), group_concat(id||':'||coalesce(disposition,''), ',') from exit_flags").fetchone()
ok("live exit_flags unchanged", disp_before == disp_after)
ok("no IB connect attempted", not IB, len(IB))
print("\n%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
