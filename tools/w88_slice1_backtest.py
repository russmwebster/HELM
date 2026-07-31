#!/usr/bin/env python3
"""W88 slice 1 - price the thesis-panel fray thresholds against the closed book.

Read-only. Replays the P3 belief ("it stays on my side of the short strike")
over every closed credit position that has journal history, and asks three
questions:

  1. Does a confirmed fray PRECEDE a bad outcome, or merely coincide with it?
  2. At what threshold - and does the design doc's candidate (3%, or half the
     entry buffer) carry signal?
  3. What would ACTING on it have done to the closed book?

Why it recomputes buffer instead of reading checks.buffer_pct:
`check_cmd._assess` only sets intrinsic_buffer when a position has EXACTLY ONE
short option leg (check_cmd.py ~590), so buffer_pct is NULL on 1,759 of 1,777
GOOD iron-condor rows. Condors are 79 of 250 closed positions and the book's
worst line, so the backtest reconstructs buffer from `legs` + `checks.spot_price`
for every structure. The reconstruction is validated against the production
column where production has one (--validate): max abs diff 0.005pp on 4,068 rows.

Usage:  python3 tools/w88_slice1_backtest.py [--db PATH] [--validate]
Default DB is a snapshot; point --db at a VACUUM INTO copy, never the live file.
"""
import argparse, json, random, sqlite3
from collections import defaultdict
from datetime import date

CREDIT = ("CSP", "IRON_CONDOR", "BEAR_CALL_SPREAD", "BULL_PUT_SPREAD", "COVERED_CALL")


def load(db):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    c.row_factory = sqlite3.Row
    legs = defaultdict(list)
    for r in c.execute("select * from legs"):
        legs[r["position_id"]].append(dict(r))

    def bufpct(pid, spot):
        """Worst (nearest) signed distance to any short strike, as % of spot."""
        best = None
        for l in legs.get(pid, []):
            if l["direction"] != "SHORT" or l["strike"] is None or not l["option_type"]:
                continue
            k = float(l["strike"])
            d = (spot - k) if l["option_type"] == "PUT" else (k - spot)
            v = d / spot * 100.0
            if best is None or v < best:
                best = v
        return best

    entry_spot = {}
    for r in c.execute("select position_id, spot_price from entry_snapshots "
                       "where spot_price is not null order by snapshot_at"):
        entry_spot.setdefault(r["position_id"], r["spot_price"])

    ck = defaultdict(list)
    # W53: GOOD rows only - the convention every reader that ACTS already uses.
    for r in c.execute("select position_id, checked_at, spot_price, pnl_unrealized "
                       "from checks where data_quality='GOOD' and spot_price is not null "
                       "order by checked_at"):
        ck[r["position_id"]].append(dict(r))

    out = []
    for p in c.execute("select * from positions where status='CLOSED' and strategy in %s" % (CREDIT,)):
        p = dict(p)
        rows = ck.get(p["id"], [])
        if not rows:
            continue
        byday = defaultdict(list)
        for r in rows:
            byday[r["checked_at"][:10]].append(r)
        days = []
        for d in sorted(byday):
            vals = [(bufpct(p["id"], r["spot_price"]), r) for r in byday[d]]
            vals = [v for v in vals if v[0] is not None]
            if not vals:
                continue
            # worst-of-day netting - evidenced by the real GE CSP touching
            # 0.10% buffer at 15:55 on 7/29 after a benign morning.
            worst = min(vals, key=lambda t: t[0])
            marks = [r["pnl_unrealized"] for _, r in vals if r["pnl_unrealized"] is not None]
            days.append({"d": d, "buf": worst[0], "pnl": marks[-1] if marks else None})
        if not days:
            continue
        es = entry_spot.get(p["id"])
        out.append({"id": p["id"], "tk": p["ticker"], "st": p["strategy"], "bk": p["book"],
                    "real": p["realized_pnl"], "prem": p["net_premium"], "exit": p["exit_reason"],
                    "eb": bufpct(p["id"], es) if es else None, "days": days,
                    "opened": p["opened_at"][:10], "closed": (p["closed_at"] or "")[:10]})
    return c, out, bufpct


def validate(c, bufpct):
    diffs = []
    for r in c.execute("""select k.position_id, k.spot_price, k.buffer_pct from checks k
                          join positions p on p.id=k.position_id
                          where k.data_quality='GOOD' and k.buffer_pct is not null
                            and k.spot_price is not null"""):
        v = bufpct(r["position_id"], r["spot_price"])
        if v is not None:
            diffs.append(abs(v - r["buffer_pct"]))
    diffs.sort()
    print("VALIDATION vs production buffer_pct: n=%d max=%.4fpp median=%.4fpp within-0.01pp=%d/%d"
          % (len(diffs), diffs[-1], diffs[len(diffs) // 2],
             sum(1 for d in diffs if d <= 0.01), len(diffs)))


def lag_days(a, b):
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except Exception:
        return None


def trigger(x, test, cd):
    """First day the belief has been outside its band for `cd` consecutive
    check-days - the existing 2-day confirmation discipline, journal-derived."""
    streak = 0
    for day in x["days"]:
        if test(x, day["buf"]):
            streak += 1
            if streak >= cd:
                return day
        else:
            streak = 0
    return None


def groups(P, test, cd, lead=0):
    tri, non = [], []
    for x in P:
        t = trigger(x, test, cd)
        ok = False
        if t and x["closed"]:
            lg = lag_days(t["d"], x["closed"])
            ok = lg is not None and lg >= lead
        (tri if ok else non).append((x, t))
    return tri, non


def loser(g):
    return sum(1 for x, _ in g if (x["real"] or 0) < 0) / len(g) if g else 0.0


def severe(g):
    """Loss worse than the whole credit - severity, which is what dominates
    when the edge is thin (W12 / W73)."""
    return sum(1 for x, _ in g if x["prem"] and (x["real"] or 0) < -abs(x["prem"])) / len(g) if g else 0.0


def report(tag, P, test, cd, lead):
    tri, non = groups(P, test, cd, lead)
    lt, ln = loser(tri), loser(non)
    print("%-28s cd%d L%-3s| tri %3d lose %4.0f%% sev %4.0f%% | non %3d lose %4.0f%% sev %4.0f%% | lift %s"
          % (tag, cd, lead, len(tri), 100 * lt, 100 * severe(tri),
             len(non), 100 * ln, 100 * severe(non),
             ("%.2fx" % (lt / ln)) if ln else "n/a"))


def counterfactual(tag, P, test, cd):
    """What acting on the belief would have done: close at the confirmation
    day's mark instead of where the position actually closed.
    Caveat: no closing cost or spread is modelled, which flatters the rule."""
    tri, _ = groups(P, test, cd, 0)
    ds = sorted(t["pnl"] - (x["real"] or 0.0) for x, t in tri if t["pnl"] is not None)
    if not ds:
        return
    n = len(ds)
    print("%-28s cd%d | n %3d | total %+8.0f | median/pos %+7.0f | ex-top5 %+8.0f | saves %2d whipsaws %2d"
          % (tag, cd, n, sum(ds), ds[n // 2], sum(ds[:-5]),
             sum(1 for d in ds if d > 0.01), sum(1 for d in ds if d < -0.01)))


def permutation_p(P, test, cd, lead, iters=20000, seed=11):
    random.seed(seed)
    tri, non = groups(P, test, cd, lead)
    k = len(tri)
    if not k:
        return None
    obs = loser(tri)
    pool = [1 if (x["real"] or 0) < 0 else 0 for x, _ in tri + non]
    hits = 0
    for _ in range(iters):
        random.shuffle(pool)
        if sum(pool[:k]) / k >= obs:
            hits += 1
    return hits / iters


def bootstrap_lift(P, test, cd, lead, iters=10000, seed=7):
    random.seed(seed)
    tri, non = groups(P, test, cd, lead)
    if not tri or not non or not loser(non):
        return None
    obs = loser(tri) / loser(non)
    lifts = []
    for _ in range(iters):
        a = [random.choice(tri) for _ in tri]
        b = [random.choice(non) for _ in non]
        if loser(b) > 0:
            lifts.append(loser(a) / loser(b))
    lifts.sort()
    return obs, lifts[int(len(lifts) * 0.05)], lifts[int(len(lifts) * 0.95)], \
        sum(1 for l in lifts if l > 1) / len(lifts)


ABS = lambda t: (lambda x, b: b < t)
REL = lambda f: (lambda x, b: x["eb"] is not None and x["eb"] > 0 and b < f * x["eb"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/helm-snap-s95-w88.db")
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args()
    c, S, bufpct = load(a.db)
    if a.validate:
        validate(c, bufpct)
        print()

    print("SAMPLE: %d closed credit positions with GOOD journal history" % len(S))
    print("  by strategy: %s" % {k: sum(1 for x in S if x["st"] == k) for k in sorted(set(x["st"] for x in S))})
    print("  by book:     %s" % {k: sum(1 for x in S if x["bk"] == k) for k in sorted(set(x["bk"] for x in S))})
    print("  only 1 check day (cannot confirm a 2-day streak): %d" % sum(1 for x in S if len(x["days"]) == 1))
    print()

    print("--- 1. THRESHOLD SWEEP (does the fray discriminate?), lead >= 7 days, cd2 ---")
    for t in (5, 3, 1, 0, -2):
        report("abs buffer < %d%%" % t, S, ABS(t), 2, 7)
    for f in (0.50, 0.33, 0.25):
        report("rel < %.0f%% of entry buffer" % (f * 100), [x for x in S if x["eb"] is not None], REL(f), 2, 7)
    print()

    print("--- 2. LEAD TIME (does it precede, or coincide?), breach = buffer < 0%, cd2 ---")
    for L in (0, 3, 7, 14, 21):
        report("breach", S, ABS(0), 2, L)
    print()

    print("--- 3. CONFIRMATION DAYS at breach ---")
    for cd in (1, 2, 3):
        report("breach", S, ABS(0), cd, 0)
    print()

    print("--- 4. BY STRATEGY, breach cd2 ---")
    for stg in sorted(set(x["st"] for x in S)):
        P = [x for x in S if x["st"] == stg]
        if len(P) < 5:
            continue
        report(stg, P, ABS(0), 2, 0)
        report(stg + " (lead>=7)", P, ABS(0), 2, 7)
    print()

    print("--- 5. WHAT ACTING WOULD HAVE DONE (close at the confirmation mark) ---")
    for t in (3, 0):
        counterfactual("close on abs<%d%%" % t, S, ABS(t), 2)
    for bk in sorted(set(x["bk"] for x in S)):
        counterfactual("  %s only, breach" % bk, [x for x in S if x["bk"] == bk], ABS(0), 2)
    print()

    print("--- 6. SIGNIFICANCE ---")
    for L in (0, 7, 14):
        p = permutation_p(S, ABS(0), 2, L)
        print("  permutation p (all, breach cd2, lead>=%2d): %.4f" % (L, p))
    b = bootstrap_lift([x for x in S if x["st"] == "CSP"], ABS(0), 2, 7)
    if b:
        print("  CSP breach lift (lead>=7, cd2): %.2fx  90%% CI [%.2f, %.2f]  P(lift>1)=%.3f" % b)


if __name__ == "__main__":
    main()
