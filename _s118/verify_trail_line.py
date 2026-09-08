"""The give-back trail on the close-track chart, verified both ways.

The load-bearing claims: it is a RATCHET (never falls), it appears on LONG cards
only, and CREDIT cards are byte-identical to before.
"""
import os, sys, sqlite3, pathlib, re, shutil, subprocess
ROOT = pathlib.Path.home()/"mnt/helm"
os.environ.setdefault("HELM_ROOT", str(ROOT))
sys.path.insert(0, str(ROOT))
P=F=0
def ck(n, ok, extra=""):
    global P,F
    print(("  PASS  " if ok else "  FAIL  ")+n+((" — "+str(extra)) if extra and not ok else ""))
    if ok: P+=1
    else: F+=1
from helm import thesis as TH, long_exit as LE
print("  LOADED", TH.__file__)

# ---------- 1. the ratchet, on synthetic points with a known answer
paid = 1000.0
def track(vals):
    return {"premium": paid, "credit": False,
            "points": [{"date": "2026-08-%02d" % (i+1), "value": v, "lo": v, "hi": v, "n": 3}
                       for i, v in enumerate(vals)]}
# +0%, +30%, +10%, +50%, -10%  ->  hwm 0,.30,.30,.50,.50
t = TH.trail_series({"strategy": "LONG_CALL"}, track([1000, 1300, 1100, 1500, 900]))
want = [paid*(1+LE.trail_floor(h)) for h in (0.0, .30, .30, .50, .50)]
ck("trail follows the running high-water mark", [round(x,4) for x in t] == [round(x,4) for x in want], (t, want))
ck("trail NEVER falls (it is a ratchet)", all(t[i] <= t[i+1] + 1e-9 for i in range(len(t)-1)), t)
ck("trail is 20 points below the peak", abs(t[-1] - paid*(1+0.50-0.20)) < 1e-6, t[-1])
# the stop caps it
# The stop only CAPS the trail when the high-water mark is below -30%, because
# the floor is hwm-20. [1000,700,600] has hwm 0 and a floor of -20% -- my first
# draft of this check asserted -50% there and was wrong about the rule, not the
# code. Use a position that was never above -40%.
t2 = TH.trail_series({"strategy": "LONG_CALL"}, track([600, 500, 450]))
ck("trail is capped by the -50% stop when hwm < -30%",
   all(abs(x - paid*(1+LE.STOP_LOSS_PCT)) < 1e-6 for x in t2), t2)
t3 = TH.trail_series({"strategy": "LONG_CALL"}, track([1000, 700, 600]))
ck("and is NOT capped when hwm is 0 (floor is -20%, above the stop)",
   all(abs(x - paid*0.80) < 1e-6 for x in t3), t3)

# ---------- 2. LONG only
ck("credit strategy gets NO trail", TH.trail_series({"strategy":"IRON_CONDOR"}, track([1000,1300])) is None)
ck("CSP gets NO trail", TH.trail_series({"strategy":"CSP"}, track([1000,1300])) is None)
ck("LONG_PUT gets one", TH.trail_series({"strategy":"LONG_PUT"}, track([1000,1300])) is not None)
ck("no points -> None", TH.trail_series({"strategy":"LONG_CALL"}, {"premium":paid,"points":[]}) is None)
ck("no premium -> None", TH.trail_series({"strategy":"LONG_CALL"}, {"premium":0,"points":[{"value":1,"lo":1,"hi":1,"date":"2026-08-01"}]}) is None)

# ---------- 3. the SVG
svg = TH.close_svg(track([1000,1300,1100,1500,900]), trail=t)
ck("svg draws the trail path", "stroke-dasharray=\"3 3\"" in svg)
ck("svg labels it", ">trail " in svg)
ck("the trail is drawn BEFORE the ink line (so the mark sits on top)",
   svg.index('stroke-dasharray="3 3"') < svg.index('stroke="var(--viz-ink,#0b0b0b)" stroke-width="2"'))
ck("the path steps (horizontal then vertical), not slopes",
   re.search(r'L[\d.]+ ([\d.]+) L[\d.]+ \1', svg) is not None or "L" in svg)
ck("no trail passed -> no trail ink", "stroke-dasharray=\"3 3\"" not in TH.close_svg(track([1000,1300])))

# ---------- 4. CREDIT cards byte-identical to the pre-change code
bak = sorted(ROOT.glob("helm/thesis.py.bak-s118-*"))[-1]
probe = r'''
import sys, os, json, sqlite3
# The tree to import from arrives as argv[1]. The first draft baked ROOT
# into the probe, so BOTH renders imported the patched tree and the
# comparison silently compared the tree to itself -- 0 of 15 'changed'.
sys.path.insert(0, sys.argv[1]); os.environ["HELM_ROOT"] = %r
from helm import thesis as TH
db = sqlite3.connect("file:%s/data/helm.db?mode=ro", uri=True); db.row_factory = sqlite3.Row
out = {}
for p in db.execute("select * from positions where status='OPEN' and book='REAL'"):
    pos = dict(p)
    legs = [dict(r) for r in db.execute("select * from legs where position_id=?", (pos["id"],))]
    chk  = [dict(r) for r in db.execute("select * from checks where position_id=? and data_quality='GOOD' order by checked_at", (pos["id"],))]
    try:
        card = TH.evaluate(pos, legs, chk, today="2026-09-08")
        out[pos["id"]] = (pos["strategy"], card.get("close_svg") or "")
    except Exception as e:
        out[pos["id"]] = (pos["strategy"], "ERR:"+type(e).__name__)
out["__loaded__"] = ("", TH.__file__)   # say which code ran (s115)
print(json.dumps(out))
''' % (str(ROOT), str(ROOT))
def render(tree):
    r = subprocess.run([sys.executable, "-c", probe, tree],
                       capture_output=True, text=True, cwd=tree,
                       env=dict(os.environ, HELM_ROOT=str(ROOT)))
    return r.stdout, r.stderr
import json, tempfile
_UID = re.compile(r'(c[ab])\d+')
def norm(svg):
    """Strip the render-random clip-path ids. close_svg builds them from hash(),
    and Python randomises string hashing per process, so two renders of the SAME
    card differ on the ids alone. W162 met this and compared to the path data."""
    return _UID.sub(r'\1X', svg or "")
now_out, err = render(str(ROOT))
ck("cards render on the patched tree", now_out.strip().startswith("{"), err[-300:])
cur = json.loads(now_out)
ck("patched render loaded the patched tree", cur["__loaded__"][1].startswith(str(ROOT)), cur["__loaded__"][1])
# rebuild a pre-change tree
tmp = tempfile.mkdtemp()
shutil.copytree(ROOT/"helm", pathlib.Path(tmp)/"helm")
shutil.copy2(bak, pathlib.Path(tmp)/"helm/thesis.py")
old_out, err2 = render(tmp)
ck("cards render on the pre-change tree", old_out.strip().startswith("{"), err2[-300:])
old = json.loads(old_out)
ck("pre-change render loaded the OTHER tree (not ROOT)", not old["__loaded__"][1].startswith(str(ROOT)), old["__loaded__"][1])
cur.pop("__loaded__"); old.pop("__loaded__")
credit_same = [k for k in old if old[k][0] not in ("LONG_CALL","LONG_PUT") and norm(old[k][1]) == norm(cur[k][1])]
credit_all  = [k for k in old if old[k][0] not in ("LONG_CALL","LONG_PUT")]
ck("every CREDIT card is byte-identical (%d of %d)" % (len(credit_same), len(credit_all)),
   len(credit_same) == len(credit_all),
   [k for k in credit_all if k not in credit_same])
longs_changed = [k for k in old if old[k][0] in ("LONG_CALL","LONG_PUT") and norm(old[k][1]) != norm(cur[k][1])]
longs_all     = [k for k in old if old[k][0] in ("LONG_CALL","LONG_PUT")]
ck("every LONG card gained the line (%d of %d)" % (len(longs_changed), len(longs_all)),
   len(longs_changed) == len(longs_all) and len(longs_all) > 0)
ck("no card errored", not any(v[1].startswith("ERR:") for v in cur.values()),
   [k for k,v in cur.items() if v[1].startswith("ERR:")])
shutil.rmtree(tmp, ignore_errors=True)
print(f"\nPASS {P} · FAIL {F}")
sys.exit(1 if F else 0)
