"""W170 - the close-track chart states its direction in words.

Renders every OPEN REAL card on the patched tree and on the pre-change tree
(the s119 backup), and asserts the difference is EXACTLY the three intended
additions and nothing else. Clip-path ids are normalised, per W162/s118.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/Projects/helm"))
bak = sorted(ROOT.glob("helm/thesis.py.bak-s119-*"))[-1]
P = F = 0
def ck(label, cond, extra=""):
    global P, F
    if cond: P += 1; print("  PASS  " + label)
    else:    F += 1; print("  FAIL  %s — %s" % (label, extra))

probe = r'''
import sys, os, json, sqlite3
sys.path.insert(0, sys.argv[1]); os.environ["HELM_ROOT"] = %r
from helm import thesis as TH
db = sqlite3.connect("file:%s/data/helm.db?mode=ro", uri=True); db.row_factory = sqlite3.Row
out = {}
for p in db.execute("select * from positions where status='OPEN' and book='REAL'"):
    pos = dict(p)
    legs = [dict(r) for r in db.execute("select * from legs where position_id=?", (pos["id"],))]
    chk  = [dict(r) for r in db.execute("select * from checks where position_id=? and data_quality='GOOD' order by checked_at", (pos["id"],))]
    try:
        card = TH.evaluate(pos, legs, chk, today="2026-09-09")
        out[pos["id"]] = (pos["strategy"], card.get("close_svg") or "",
                          bool((card.get("close_track") or {}).get("credit")))
    except Exception as e:
        out[pos["id"]] = (pos["strategy"], "ERR:"+type(e).__name__, None)
out["__loaded__"] = ("", TH.__file__, None)
print(json.dumps(out))
''' % (str(ROOT), str(ROOT))

def render(tree):
    r = subprocess.run([sys.executable, "-c", probe, tree], capture_output=True,
                       text=True, cwd=tree, env=dict(os.environ, HELM_ROOT=str(ROOT)))
    return r.stdout, r.stderr

_UID = re.compile(r'(c[ab])\d+')
norm = lambda s: _UID.sub(r'\1X', s or "")

now_out, err = render(str(ROOT))
ck("cards render on the patched tree", now_out.strip().startswith("{"), err[-300:])
cur = json.loads(now_out)
ck("patched render loaded the patched tree", cur["__loaded__"][1].startswith(str(ROOT)), cur["__loaded__"][1])

tmp = tempfile.mkdtemp()
shutil.copytree(ROOT/"helm", pathlib.Path(tmp)/"helm")
shutil.copy2(bak, pathlib.Path(tmp)/"helm/thesis.py")
old_out, err2 = render(tmp)
ck("cards render on the pre-change tree", old_out.strip().startswith("{"), err2[-300:])
old = json.loads(old_out)
ck("pre-change render loaded the OTHER tree", not old["__loaded__"][1].startswith(str(ROOT)), old["__loaded__"][1])
cur.pop("__loaded__"); old.pop("__loaded__")

ck("no card errors after the change", not [k for k in cur if cur[k][1].startswith("ERR:")],
   [k for k in cur if cur[k][1].startswith("ERR:")])
ck("no card errored before either (control)", not [k for k in old if old[k][1].startswith("ERR:")],
   [k for k in old if old[k][1].startswith("ERR:")])

CRED_CAP = "↓ better — costs less to buy back than you took in"
DEBIT_CAP = "↑ better — sells back for more than you paid"
LONGS = ("LONG_CALL", "LONG_PUT")

withsvg = [k for k in cur if cur[k][1] and not cur[k][1].startswith("ERR:")]
credit = [k for k in withsvg if cur[k][2]]
longs  = [k for k in withsvg if not cur[k][2]]
ck("there are credit cards to test (%d)" % len(credit), len(credit) > 0)
ck("there are debit cards to test (%d)" % len(longs), len(longs) > 0)

bad = [k for k in credit if CRED_CAP not in cur[k][1]]
ck("every credit card carries the DOWN-is-better caption (%d of %d)" % (len(credit)-len(bad), len(credit)), not bad, bad)
bad = [k for k in longs if DEBIT_CAP not in cur[k][1]]
ck("every debit card carries the UP-is-better caption (%d of %d)" % (len(longs)-len(bad), len(longs)), not bad, bad)
bad = [k for k in credit if DEBIT_CAP in cur[k][1]] + [k for k in longs if CRED_CAP in cur[k][1]]
ck("no card carries the WRONG caption for its family", not bad, bad)

bad = [k for k in credit if 'aria-label="What it would cost to close, on each check day -- lower is better"' not in cur[k][1]]
ck("credit aria-label states the direction", not bad, bad)
bad = [k for k in longs if 'aria-label="What closing would pay, on each check day -- higher is better"' not in cur[k][1]]
ck("debit aria-label states the direction", not bad, bad)

# the chip, where one is drawn, carries a word beside the glyph
chipped = [k for k in withsvg if "▼" in cur[k][1] or "▲" in cur[k][1]]
WORDS = ("cheaper", "dearer", "fetching less", "fetching more")
bad = [k for k in chipped if not any(w in cur[k][1] for w in WORDS)]
ck("every delta chip carries its word (%d chips)" % len(chipped), not bad, bad)
bad = [k for k in chipped if cur[k][2] and not any(w in cur[k][1] for w in ("cheaper", "dearer"))]
ck("credit chips use the credit vocabulary", not bad, bad)
bad = [k for k in chipped if not cur[k][2] and not any(w in cur[k][1] for w in ("fetching less", "fetching more"))]
ck("debit chips use the debit vocabulary", not bad, bad)

# THE LOAD-BEARING ONE: nothing but the three additions moved.
def strip_additions(svg):
    s = svg
    s = re.sub(r'<text[^>]*>(?:↓|↑) better[^<]*</text>', '', s)
    s = re.sub(r'aria-label="[^"]*"', 'aria-label="X"', s)
    for w in WORDS:
        s = s.replace(" " + w + "</text>", "</text>")
    return norm(s)

moved = [k for k in withsvg if strip_additions(cur[k][1]) != strip_additions(old[k][1])]
ck("with the additions stripped, EVERY card is byte-identical (%d of %d)"
   % (len(withsvg)-len(moved), len(withsvg)), not moved, moved[:6])

# and prove that check can fail
ck("the strip-and-compare check is capable of failing",
   strip_additions(cur[credit[0]][1]) != strip_additions(cur[credit[0]][1].replace("stroke-width=\"2\"", "stroke-width=\"3\"", 1)))

print("\nPASS %d · FAIL %d" % (P, F))
sys.exit(1 if F else 0)
