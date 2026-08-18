#!/usr/bin/env python3
"""W16 / HELM-163 - the management-deadline sweep.

Answers: should the DTE deadline for CSP + IRON_CONDOR move off 21?

READ-ONLY. Opens the database mode=ro and writes nothing.

    python3 tools/w16_dte_sweep.py                 # live answer, all closed positions
    python3 tools/w16_dte_sweep.py --as-of DATE    # restrict to positions closed on/before DATE
    python3 tools/w16_dte_sweep.py --json
    python3 tools/w16_dte_sweep.py --selftest      # re-run the frozen fixture and diff

WHY THIS FILE EXISTS (s107, 2026-08-18). s101 ran this sweep ad hoc and never saved
the script. Rebuilding it from the register prose in s107 reproduced the published
figures only to within 1-9%, so a later re-run could not have told method drift apart
from a real change in the world. --selftest settles that in one command: it re-runs
the frozen universe and diffs against FIXTURE. A mismatch means THIS CODE changed.

WHAT s107 FOUND, so a later reader need not re-derive it:
  * As a P&L edge the 28-day deadline is NOT real. Ten positions of 204 carry the
    whole +$30k; drop them and it is +$210. Median position -$6; 70 helped, 75 hurt.
    "28 is a genuine interior optimum" was an artifact of summing a fat tail.
  * As a LARGE-LOSS control it is real and monotonic. Losses worse than -$2,000 go
    10 -> 2. Eight distinct positions cross that line favourably and ZERO cross back.
    Counts, unlike sums, cannot be manufactured by a single position.
  * Price: both tails shrink. About $1 of upside forfeited per $5 of loss avoided.
  * "Cut losers at 28, hold winners to 21" is WORSE than closing everything at 28,
    including on the winners. Over the 28->21 week, positions WINNING at 28 did worse
    (mean -$650, 26% improved) than those LOSING at 28 (mean -$314, 47% improved).
    So this is a DEADLINE question, not a stop-loss question - no conflict with W73.
  * Decision deferred (Russ, s107): leave the rule at 21, re-run when n >= 120.
    Moving the deadline to 28 stops the journal marking the 28->21 window, which
    would destroy the evidence needed to check the decision. One-way door.
"""
import argparse, json, sqlite3, statistics, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "helm.db"
DEADLINES = [21, 24, 28, 30]
STRATEGIES = ("CSP", "IRON_CONDOR")
BIG_LOSS = (-1000, -2000, -3000)
FIXTURE_AS_OF = "2026-08-17"
# Frozen 2026-08-18 (s107) by --freeze over the 2026-08-17 universe.
# Do not hand-edit. Regenerate with --freeze only if the METHOD deliberately
# changes, and say so in the commit message. A --selftest failure means THIS
# CODE moved, not the book.
FIXTURE = {
    'actual.cvar10': -3400,
    'actual.loss_sum': -75729,
    'actual.losses_under_1k': 12,
    'actual.losses_under_2k': 10,
    'actual.losses_under_3k': 8,
    'actual.total': -16746,
    'actual.win_sum': 58983,
    'actual.wins_over_1k': 19,
    'actual.worst': -9520,
    'as_of': '2026-08-17',
    'deadlines.21.cvar10': -2753,
    'deadlines.21.fires_on': 80,
    'deadlines.21.loss_sum': -65946,
    'deadlines.21.losses_under_1k': 13,
    'deadlines.21.losses_under_2k': 10,
    'deadlines.21.losses_under_3k': 7,
    'deadlines.21.total': -18028,
    'deadlines.21.win_sum': 47918,
    'deadlines.21.wins_over_1k': 16,
    'deadlines.21.worst': -8746,
    'deadlines.24.cvar10': -1594,
    'deadlines.24.fires_on': 133,
    'deadlines.24.loss_sum': -38696,
    'deadlines.24.losses_under_1k': 8,
    'deadlines.24.losses_under_2k': 4,
    'deadlines.24.losses_under_3k': 3,
    'deadlines.24.total': 2748,
    'deadlines.24.win_sum': 41444,
    'deadlines.24.wins_over_1k': 14,
    'deadlines.24.worst': -7060,
    'deadlines.28.cvar10': -1043,
    'deadlines.28.fires_on': 151,
    'deadlines.28.loss_sum': -28060,
    'deadlines.28.losses_under_1k': 7,
    'deadlines.28.losses_under_2k': 2,
    'deadlines.28.losses_under_3k': 1,
    'deadlines.28.total': 12197,
    'deadlines.28.win_sum': 40257,
    'deadlines.28.wins_over_1k': 13,
    'deadlines.28.worst': -5142,
    'deadlines.30.cvar10': -912,
    'deadlines.30.fires_on': 160,
    'deadlines.30.loss_sum': -25579,
    'deadlines.30.losses_under_1k': 4,
    'deadlines.30.losses_under_2k': 1,
    'deadlines.30.losses_under_3k': 1,
    'deadlines.30.total': 9256,
    'deadlines.30.win_sum': 34835,
    'deadlines.30.wins_over_1k': 12,
    'deadlines.30.worst': -4332,
    'delta_21_to_28.crossings_adverse': '',
    'delta_21_to_28.crossings_favourable': 'AMAT,IONQ,IREN,LIN,META,OKLO,RKLB,WELL',
    'delta_21_to_28.helped': 70,
    'delta_21_to_28.hurt': 75,
    'delta_21_to_28.median': -6,
    'delta_21_to_28.n_differing': 145,
    'delta_21_to_28.sum': 30225,
    'delta_21_to_28.sum_drop_top_10': 210,
    'n': 204,
    'rules.A_21_only.cvar10': -2753,
    'rules.A_21_only.loss_sum': -65946,
    'rules.A_21_only.losses_under_1k': 13,
    'rules.A_21_only.losses_under_2k': 10,
    'rules.A_21_only.losses_under_3k': 7,
    'rules.A_21_only.total': -18028,
    'rules.A_21_only.win_sum': 47918,
    'rules.A_21_only.wins_over_1k': 16,
    'rules.A_21_only.worst': -8746,
    'rules.B_28_only.cvar10': -1043,
    'rules.B_28_only.loss_sum': -28060,
    'rules.B_28_only.losses_under_1k': 7,
    'rules.B_28_only.losses_under_2k': 2,
    'rules.B_28_only.losses_under_3k': 1,
    'rules.B_28_only.total': 12197,
    'rules.B_28_only.win_sum': 40257,
    'rules.B_28_only.wins_over_1k': 13,
    'rules.B_28_only.worst': -5142,
    'rules.C_28_if_losing_else_21.cvar10': -1511,
    'rules.C_28_if_losing_else_21.loss_sum': -40081,
    'rules.C_28_if_losing_else_21.losses_under_1k': 10,
    'rules.C_28_if_losing_else_21.losses_under_2k': 4,
    'rules.C_28_if_losing_else_21.losses_under_3k': 2,
    'rules.C_28_if_losing_else_21.total': -546,
    'rules.C_28_if_losing_else_21.win_sum': 39535,
    'rules.C_28_if_losing_else_21.wins_over_1k': 14,
    'rules.C_28_if_losing_else_21.worst': -6120,
    'rules.D_28_if_below_500_else_21.cvar10': -1826,
    'rules.D_28_if_below_500_else_21.loss_sum': -49023,
    'rules.D_28_if_below_500_else_21.losses_under_1k': 13,
    'rules.D_28_if_below_500_else_21.losses_under_2k': 6,
    'rules.D_28_if_below_500_else_21.losses_under_3k': 3,
    'rules.D_28_if_below_500_else_21.total': -4148,
    'rules.D_28_if_below_500_else_21.win_sum': 44875,
    'rules.D_28_if_below_500_else_21.wins_over_1k': 15,
    'rules.D_28_if_below_500_else_21.worst': -6120,
    'week_28_to_21.all.improved': 33,
    'week_28_to_21.all.mean': -411,
    'week_28_to_21.all.median': -16,
    'week_28_to_21.all.n': 80,
    'week_28_to_21.all.sum': -32870,
    'week_28_to_21.losing_at_28.improved': 27,
    'week_28_to_21.losing_at_28.mean': -314,
    'week_28_to_21.losing_at_28.median': 0,
    'week_28_to_21.losing_at_28.n': 57,
    'week_28_to_21.losing_at_28.sum': -17919,
    'week_28_to_21.winning_at_28.improved': 6,
    'week_28_to_21.winning_at_28.mean': -650,
    'week_28_to_21.winning_at_28.median': -45,
    'week_28_to_21.winning_at_28.n': 23,
    'week_28_to_21.winning_at_28.sum': -14951,
}


def connect(path):
    c = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    c.row_factory = sqlite3.Row
    return c


def universe(c, as_of):
    """Closed CSP/condors with at least one GOOD journal mark carrying dte_now.

    The GOOD filter matches HELM-117: every reader that ACTS filters to GOOD, so a
    study of what a rule would have done must read the rows the rule would see.
    """
    q = ("select p.id, p.ticker, p.strategy, p.book, p.realized_pnl, "
         "substr(p.opened_at,1,7) entry_month "
         "from positions p where p.status = 'CLOSED' "
         "and p.strategy in (%s) " % ",".join("?" * len(STRATEGIES)) +
         "and exists (select 1 from checks ch where ch.position_id = p.id "
         "  and ch.dte_now is not null and ch.data_quality = 'GOOD' "
         "  and ch.pnl_unrealized is not null)")
    args = list(STRATEGIES)
    if as_of:
        q += " and date(p.closed_at) <= ?"
        args.append(as_of)
    return c.execute(q, args).fetchall()


_MARKS = {}


def mark(c, pid, n):
    """First GOOD journalled P&L at or inside n DTE. None if never reached."""
    key = (pid, n)
    if key not in _MARKS:
        r = c.execute(
            "select pnl_unrealized from checks where position_id = ? "
            "and dte_now is not null and dte_now <= ? and data_quality = 'GOOD' "
            "and pnl_unrealized is not null order by checked_at limit 1",
            (pid, n)).fetchone()
        _MARKS[key] = r[0] if r else None
    return _MARKS[key]


def outcome(c, pid, n, realized):
    m = mark(c, pid, n)
    return (m, True) if m is not None else ((realized or 0.0), False)


def tail_stats(vals):
    losses = [v for v in vals if v < 0]
    wins = [v for v in vals if v > 0]
    k = max(1, len(vals) // 10)
    out = {"total": round(sum(vals)),
           "loss_sum": round(sum(losses)),
           "win_sum": round(sum(wins)),
           "worst": round(min(vals)) if vals else 0,
           "cvar10": round(statistics.mean(sorted(vals)[:k])) if vals else 0,
           "wins_over_1k": len([v for v in wins if v > 1000])}
    for t in BIG_LOSS:
        out["losses_under_%dk" % (abs(t) // 1000)] = len([v for v in vals if v < t])
    return out


def analyse(c, as_of):
    rows = universe(c, as_of)
    res = {"as_of": as_of or "", "n": len(rows), "deadlines": {}, "rules": {}}
    for n in DEADLINES:
        vals, fired = [], 0
        for r in rows:
            v, f = outcome(c, r["id"], n, r["realized_pnl"])
            vals.append(v)
            fired += 1 if f else 0
        s = tail_stats(vals)
        s["fires_on"] = fired
        res["deadlines"][str(n)] = s
    res["actual"] = tail_stats([(r["realized_pnl"] or 0.0) for r in rows])
    deltas, fav, adv = [], [], []
    for r in rows:
        a = outcome(c, r["id"], 21, r["realized_pnl"])[0]
        b = outcome(c, r["id"], 28, r["realized_pnl"])[0]
        deltas.append(b - a)
        if a < -2000 <= b:
            fav.append(r["ticker"])
        elif b < -2000 <= a:
            adv.append(r["ticker"])
    nz = [d for d in deltas if abs(d) > 0.5]
    srt = sorted(nz, key=lambda d: -abs(d))
    res["delta_21_to_28"] = {
        "n_differing": len(nz),
        "helped": len([d for d in nz if d > 0]),
        "hurt": len([d for d in nz if d < 0]),
        "median": round(statistics.median(nz)) if nz else 0,
        "sum": round(sum(nz)),
        "sum_drop_top_10": round(sum(srt[10:])),
        "crossings_favourable": sorted(fav),
        "crossings_adverse": sorted(adv)}
    win, los = [], []
    for r in rows:
        a, b = mark(c, r["id"], 28), mark(c, r["id"], 21)
        if a is None or b is None:
            continue
        (win if a > 0 else los).append(b - a)

    def wk(v):
        if not v:
            return {"n": 0}
        return {"n": len(v), "mean": round(statistics.mean(v)),
                "median": round(statistics.median(v)),
                "improved": len([x for x in v if x > 0]), "sum": round(sum(v))}
    res["week_28_to_21"] = {"winning_at_28": wk(win), "losing_at_28": wk(los),
                            "all": wk(win + los)}
    rules = (("A_21_only", lambda a, b, m: a),
             ("B_28_only", lambda a, b, m: b),
             ("C_28_if_losing_else_21", lambda a, b, m: b if (m is not None and m < 0) else a),
             ("D_28_if_below_500_else_21", lambda a, b, m: b if (m is not None and m < -500) else a))
    for label, fn in rules:
        vals = []
        for r in rows:
            a = outcome(c, r["id"], 21, r["realized_pnl"])[0]
            b = outcome(c, r["id"], 28, r["realized_pnl"])[0]
            vals.append(fn(a, b, mark(c, r["id"], 28)))
        res["rules"][label] = tail_stats(vals)
    return res


def render(r):
    print("W16 deadline sweep - n=%d  as-of %s" % (r["n"], r["as_of"] or "(all)"))
    print()
    hdr = "deadline |  total   | fires | <-1k <-2k <-3k | loss sum | worst  | CVaR10 | win>1k | win sum"
    print(hdr)
    print("-" * len(hdr))
    for k in [str(n) for n in DEADLINES] + ["actual"]:
        s = r["deadlines"][k] if k in r["deadlines"] else r["actual"]
        print("%-8s | %+8d | %5s | %4d %4d %4d | %+8d | %+6d | %+6d | %6d | %+8d" % (
            k, s["total"], s.get("fires_on", "-"), s["losses_under_1k"],
            s["losses_under_2k"], s["losses_under_3k"], s["loss_sum"], s["worst"],
            s["cvar10"], s["wins_over_1k"], s["win_sum"]))
    d = r["delta_21_to_28"]
    print()
    print("21 -> 28 at position level: %d differ, %d helped / %d hurt, median $%+d" % (
        d["n_differing"], d["helped"], d["hurt"], d["median"]))
    print("   sum $%+d   BUT dropping the 10 largest leaves $%+d   <- the tail test" % (
        d["sum"], d["sum_drop_top_10"]))
    print("   cross -2000 favourably: %d %s" % (len(d["crossings_favourable"]), d["crossings_favourable"]))
    print("   cross -2000 adversely : %d %s" % (len(d["crossings_adverse"]), d["crossings_adverse"]))
    w = r["week_28_to_21"]
    print()
    print("the 28->21 week itself:")
    for k in ("winning_at_28", "losing_at_28", "all"):
        s = w[k]
        if s["n"]:
            print("   %-14s n=%-3d mean $%+6d  median $%+5d  improved %d" % (
                k, s["n"], s["mean"], s["median"], s["improved"]))
    print()
    print("candidate rules:")
    for k in r["rules"]:
        s = r["rules"][k]
        print("   %-28s total $%+8d  <-2k %2d  worst $%+7d  win sum $%+8d" % (
            k, s["total"], s["losses_under_2k"], s["worst"], s["win_sum"]))


def flatten(d, prefix=""):
    out = {}
    for k in d:
        v = d[k]
        key = prefix + k
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        elif isinstance(v, list):
            out[key] = ",".join(map(str, v))
        else:
            out[key] = v
    return out


def selftest(c):
    if FIXTURE is None:
        print("NO FIXTURE FROZEN. Run --freeze and paste the result into FIXTURE.")
        return 2
    got = flatten(analyse(c, FIXTURE_AS_OF))
    diffs = [(k, FIXTURE[k], got.get(k)) for k in FIXTURE if got.get(k) != FIXTURE[k]]
    print("selftest against frozen %s universe: %d keys" % (FIXTURE_AS_OF, len(FIXTURE)))
    if not diffs:
        print("PASS - method unchanged. Any difference in a live run is the BOOK, not the code.")
        return 0
    print("DRIFT - %d keys differ. THE CODE CHANGED; runs are not comparable." % len(diffs))
    for k, want, have in diffs[:25]:
        print("   %-44s frozen %-12s now %s" % (k, want, have))
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--as-of", dest="as_of", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--freeze", action="store_true")
    a = ap.parse_args()
    c = connect(a.db)
    if a.selftest:
        sys.exit(selftest(c))
    if a.freeze:
        print(json.dumps(flatten(analyse(c, FIXTURE_AS_OF)), sort_keys=True))
        return
    r = analyse(c, a.as_of)
    print(json.dumps(r, indent=2, sort_keys=True)) if a.json else render(r)


if __name__ == "__main__":
    main()