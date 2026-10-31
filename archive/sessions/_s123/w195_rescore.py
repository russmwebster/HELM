"""_s123/w195_rescore.py -- W195: re-score every recorded long-call scan with
the new rank and find the bar that passes ~1 name per scan (max 2). READ-ONLY.

Decisions (Russ, 2026-09-27): G1 (bias + MA stack) is no longer a gate but is
still recorded; the S&P > 200-day filter stays; rank = 50% calmness + 50%
cheapness, no RSI penalty; a fixed rank bar, at most 2 names per scan.

Replayed from what each scan STORED (signals.lc_gates_json + hv_252), so it is
the board as it was, not re-fetched: G3 ratio <= 0.90, G4 state 'clear',
G5 HV252 < 40. Calmness: 1 at HV252 <= 20, 0 at 40, linear. Cheapness: the
existing vol_cheapness (1 at ratio <= 0.70, 0 at 0.90). S&P filter: SPY close
vs its 200-day average on the scan date (_s122/px/SPY.csv; SPY as the index).

    python3 _s123/w195_rescore.py
"""
import csv, json, os, sqlite3, statistics as st
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "helm.db")


def cheap(r):
    if r is None: return None
    if r <= 0.70: return 1.0
    if r >= 0.90: return 0.0
    return (0.90 - r) / 0.20


def calm(h):
    if h is None: return None
    if h <= 20: return 1.0
    if h >= 40: return 0.0
    return (40 - h) / 20


# SPY 200-day by date
px = [(r["Date"], float(r["Close"])) for r in csv.DictReader(open(os.path.join(ROOT, "_s122/px/SPY.csv")))]
spy_ok = {}
for i in range(199, len(px)):
    d, c = px[i]
    spy_ok[d] = c > sum(x for _, x in px[i-199:i+1]) / 200
def sp_above(date):
    ks = [d for d in spy_ok if d <= date]
    return spy_ok[max(ks)] if ks else None

c = sqlite3.connect("file:" + DB + "?mode=ro", uri=True); c.row_factory = sqlite3.Row
rows = c.execute("SELECT generated_at, ticker, hv_252, lc_screen_pass, lc_gates_json "
                 "FROM signals WHERE lc_screen_pass IS NOT NULL ORDER BY generated_at").fetchall()
scans = defaultdict(list)
for r in rows:
    g = json.loads(r["lc_gates_json"])
    ratio = g["g3"].get("iv_hv90_ratio")
    hv = r["hv_252"] if r["hv_252"] is not None else g["g5"].get("hv_252")
    ok = (ratio is not None and ratio <= 0.90 and g["g4"].get("state") == "clear"
          and hv is not None and hv < 40)
    score = round(0.5 * calm(hv) + 0.5 * cheap(ratio), 4) if ok else None
    scans[r["generated_at"]].append(dict(t=r["ticker"], ok=ok, score=score,
                                         old_pass=r["lc_screen_pass"], g1=bool(g["g1"].get("bias_ok") and g["g1"].get("stack_ok"))))

keys = sorted(scans)
days = sorted({k[:10] for k in keys})
spx = {k: sp_above(k[:10]) for k in keys}
print("scans %d (%s -> %s) on %d trading days; S&P above its 200-day on %d of %d scans"
      % (len(keys), keys[0][:16], keys[-1][:16], len(days), sum(1 for v in spx.values() if v), len(keys)))
old = [sum(x["old_pass"] for x in scans[k]) for k in keys]
new_surv = [sum(x["ok"] for x in scans[k]) for k in keys]
print("survivors per scan -- old screen (with G1): median %s mean %.1f | without G1: median %s mean %.1f"
      % (st.median(old), st.mean(old), st.median(new_surv), st.mean(new_surv)))

def at_bar(bar):
    per = []
    for k in keys:
        n = sum(1 for x in scans[k] if x["ok"] and x["score"] >= bar) if spx[k] else 0
        per.append(n)
    capped = [min(n, 2) for n in per]
    return per, capped

print("\n bar   mean/scan (cap 2)  uncapped  scans with 0 / 1 / 2")
table = {}
for b100 in range(40, 91):
    bar = b100 / 100
    per, cap = at_bar(bar)
    cnt = Counter(cap)
    table[bar] = (st.mean(cap), st.mean(per), cnt)
    if b100 % 5 == 0 or 0.6 <= bar <= 0.8:
        print(" %.2f      %.2f           %.2f      %2d / %2d / %2d" % (bar, st.mean(cap), st.mean(per), cnt[0], cnt[1], cnt[2]))

best = min(table, key=lambda b: (abs(table[b][0] - 1.0), -b))
per, cap = at_bar(best)
print("\nPROPOSED BAR %.2f: %.2f names/scan on average (cap 2), %.2f uncapped" % (best, st.mean(cap), st.mean(per)))
half = len(keys) // 2
print("  first half (%s -> %s): %.2f/scan   second half (%s -> %s): %.2f/scan"
      % (keys[0][:10], keys[half-1][:10], st.mean(cap[:half]), keys[half][:10], keys[-1][:10], st.mean(cap[half:])))
byday = defaultdict(list)
for k, n in zip(keys, cap): byday[k[:10]].append(n)
print("  per trading day (latest scan of the day): mean %.2f" % st.mean([v[-1] for v in byday.values()]))
names = Counter()
for k in keys:
    if not spx[k]: continue
    ranked = sorted((x for x in scans[k] if x["ok"] and x["score"] >= best), key=lambda x: -x["score"])[:2]
    for x in ranked: names[x["t"]] += 1
print("  names that would pass (scans passed, of %d): %s" % (len(keys), ", ".join("%s %d" % kv for kv in names.most_common(15))))
g1_share = [x["g1"] for k in keys for x in sorted((y for y in scans[k] if y["ok"] and y["score"] >= best), key=lambda y: -y["score"])[:2]]
print("  of those passes, %d of %d would also have passed the old G1 trend gate" % (sum(g1_share), len(g1_share)))
