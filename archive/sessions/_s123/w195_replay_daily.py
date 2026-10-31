"""_s123/w195_replay_daily.py -- W195 replay per trading day. READ-ONLY.

Russ, 2026-09-27: provisional bar 0.72 (review after a month of paper
results), max 2 per scan, no new long call in a name that already has an open
long call, and the 5% long-premium sleeve cap (W194). Last scan of each day.

Assumptions (stated with the output; change them here):
  HOLD      a replayed long call is held this many trading days (8 = median
            of the rule-exited paper long calls since 25 Jul; 13 = real)
  pricing   0.75-delta call, 120 DTE, Black-Scholes on the scan's spot and the
            row's iv_current, r 4.5%; contracts = floor($5,000 / one contract);
            one contract over $5,000 -> declined (W201)
  sleeve    no new long while sleeve >= 5% of $688,175.53 BEFORE the trade
  a diagonal's long leg counts as "an open long call in the name"
Book A (clean): only the replay's own picks. Book B (actual): Russ's real long
calls and diagonals, at their real open/close dates and long-leg cost, too.

    python3 _s123/w195_replay_daily.py [HOLD]
"""
import json, math, os, sqlite3, sys, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAR, MAX_PER_SCAN, CAP, ACCOUNT = 0.72, 2, 5000.0, 688175.53
SLEEVE_MAX = 0.05 * ACCOUNT
HOLD = int(sys.argv[1]) if len(sys.argv) > 1 else 8
LONGS = ("LONG_CALL", "DIAGONAL", "PMCC", "DIAGONAL_PUT")


def N(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def call_075(S, iv_pct, T=120 / 365, r=0.045):
    s = iv_pct / 100.0
    d1 = 0.6745                                   # N(d1) = 0.75
    K = S * math.exp(-(d1 * s * math.sqrt(T) - (r + s * s / 2) * T))
    d2 = d1 - s * math.sqrt(T)
    return S * N(d1) - K * math.exp(-r * T) * N(d2), K


def cheap(r): return 1.0 if r <= 0.70 else 0.0 if r >= 0.90 else (0.90 - r) / 0.20
def calm(h): return 1.0 if h <= 20 else 0.0 if h >= 40 else (40 - h) / 20


c = sqlite3.connect("file:" + os.path.join(ROOT, "data/helm.db") + "?mode=ro", uri=True)
c.row_factory = sqlite3.Row
last = {}
for (g,) in c.execute("SELECT DISTINCT generated_at FROM signals WHERE lc_screen_pass IS NOT NULL"):
    if g[:10] not in last or g > last[g[:10]]:
        last[g[:10]] = g
days = sorted(last)

# Russ's actual long positions (book B): name, open day, close day, long-leg cost
actual = []
for p in c.execute("SELECT id, ticker, strategy, opened_at, closed_at, status FROM positions "
                   "WHERE book='REAL' AND strategy IN (%s) AND coalesce(exit_reason,'')!='VOIDED'"
                   % ",".join("?" * len(LONGS)), LONGS):
    leg = c.execute("SELECT open_price, multiplier, contracts FROM legs WHERE position_id=? "
                    "AND direction='LONG' ORDER BY id LIMIT 1", (p["id"],)).fetchone()
    if not leg: continue
    actual.append(dict(t=p["ticker"], o=p["opened_at"][:10],
                       c=(p["closed_at"] or "9999")[:10] if p["status"] != "OPEN" else "9999",
                       cost=leg["open_price"] * (leg["multiplier"] or 100) * leg["contracts"]))


def candidates(gen):
    out = []
    for r in c.execute("SELECT ticker, spot_price, iv_current, hv_252, lc_gates_json FROM signals "
                       "WHERE generated_at=?", (gen,)):
        g = json.loads(r["lc_gates_json"]); ratio = g["g3"].get("iv_hv90_ratio")
        hv = r["hv_252"] if r["hv_252"] is not None else g["g5"].get("hv_252")
        if not (ratio is not None and ratio <= 0.90 and g["g4"].get("state") == "clear"
                and hv is not None and hv < 40):
            continue
        sc = 0.5 * calm(hv) + 0.5 * cheap(ratio)
        if sc < BAR: continue
        spot = r["spot_price"] or g["g1"].get("spot")
        iv = r["iv_current"] or (ratio * g["g3"]["hv_90"] if g["g3"].get("hv_90") else None)
        out.append(dict(t=r["ticker"], score=round(sc, 3), spot=spot, iv=iv))
    return sorted(out, key=lambda x: -x["score"])


def run(book_b):
    held = []                          # replay picks: dict(t, until_index, cost)
    rows = []
    for i, d in enumerate(days):
        held = [h for h in held if h["until"] > i]
        act = [a for a in actual if book_b and a["o"] <= d < a["c"]]
        sleeve = sum(h["cost"] for h in held) + sum(a["cost"] for a in act)
        names = {h["t"] for h in held} | {a["t"] for a in act}
        new, skipped = [], []
        for cnd in candidates(last[d])[:MAX_PER_SCAN]:
            if cnd["t"] in names:
                skipped.append("%s held" % cnd["t"]); continue
            if sleeve >= SLEEVE_MAX:
                skipped.append("%s sleeve %.1f%%" % (cnd["t"], 100 * sleeve / ACCOUNT)); continue
            if not cnd["spot"] or not cnd["iv"]:
                skipped.append("%s no price inputs" % cnd["t"]); continue
            px, K = call_075(cnd["spot"], cnd["iv"]); one = px * 100
            if one > CAP:
                skipped.append("%s W201 one contract $%s" % (cnd["t"], format(int(one), ","))); continue
            n = min(int(CAP // one), 20); cost = n * one
            held.append(dict(t=cnd["t"], until=i + HOLD, cost=cost))
            sleeve += cost; names.add(cnd["t"])
            new.append("%s x%d ~$%s" % (cnd["t"], n, format(int(cost), ",")))
        rows.append((d, 100 * sleeve / ACCOUNT, new, skipped))
    return rows


for label, b in (("A  clean book (only the replay's picks)", False),
                 ("B  actual book (your real longs count too)", True)):
    rows = run(b)
    total = sum(len(r[2]) for r in rows)
    print("\n== %s -- bar %.2f, max %d/day, hold %d trading days ==" % (label, BAR, MAX_PER_SCAN, HOLD))
    print("   %d new positions over %d trading days (%.2f/day); days with 0 / 1 / 2 new: %d / %d / %d"
          % (total, len(rows), total / len(rows), sum(1 for r in rows if not r[2]),
             sum(1 for r in rows if len(r[2]) == 1), sum(1 for r in rows if len(r[2]) == 2)))
    for d, sl, new, sk in rows:
        print("   %s  sleeve %4.1f%%  new: %-34s %s" % (d, sl, ", ".join(new) or "-",
                                                     ("skipped: " + "; ".join(sk)) if sk else ""))
