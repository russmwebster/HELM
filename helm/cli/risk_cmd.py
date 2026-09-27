"""helm risk -- W160 ($5,000/trade risk cap) and W194 (long-premium sleeve).

    helm risk                 # the open REAL book: per-trade breaches + sleeve
    helm risk --book PAPER
    helm risk --json

GATES NOTHING (HELM-193): on the real book HELM informs, it does not act.
This reads the book exactly as booked -- it does not re-fetch live prices --
so it answers "what did this trade's risk look like at entry", the same
question `helm exposure` answers for concentration. See helm/risk_cap.py.
"""
import json
import sys

from helm.config import get_active_account
from helm import risk_cap as R


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def run():
    book = (_arg("--book", "REAL") or "REAL").upper()
    as_json = "--json" in sys.argv
    account_id = get_active_account()

    rv = R.risk_view(book)
    sv = R.sleeve_view(account_id, book) if account_id else {"error": "no active account"}

    if as_json:
        print(json.dumps({"risk": rv, "sleeve": sv}, indent=2))
        return 0

    if rv.get("error"):
        print("risk_view error: %s" % rv["error"])
    else:
        print("PER-TRADE RISK  |  book %s  |  %d open position(s)  |  cap $%s/trade (W160)"
              % (book, len(rv["positions"]), format(int(rv["cap"]), ",d")))
        print("gates nothing -- HELM-193: on the real book HELM informs, it does not act.")
        print()
        rows = sorted(rv["positions"], key=lambda p: -(p["risk"] or -1))
        print("%-8s %-14s %4s %10s  %s" % ("ticker", "strategy", "n", "risk", "formula"))
        for p in rows:
            risk_str = ("$" + format(int(p["risk"]), ",d")) if p["risk"] is not None else "--"
            flag = "  <<< BREACH" if p["breach"] else ""
            print("%-8s %-14s %4s %10s  %s%s" % (
                p["ticker"], p["strategy"], p["contracts"], risk_str, p["formula"], flag))
        print()
        if rv["breaches"]:
            print("%d position(s) over the $%s/trade cap:" %
                  (len(rv["breaches"]), format(int(rv["cap"]), ",d")))
            for b in sorted(rv["breaches"], key=lambda p: -p["risk"]):
                print("  %-8s %-14s $%s" % (b["ticker"], b["strategy"], format(int(b["risk"]), ",d")))
        else:
            print("No breaches of the $%s/trade cap." % format(int(rv["cap"]), ",d"))
        if rv["unmeasured"]:
            print()
            print("%d position(s) have no W160 formula or the data to run it:" % len(rv["unmeasured"]))
            for u in rv["unmeasured"]:
                print("  %-8s %-14s -- %s" % (u["ticker"], u["strategy"], u["formula"]))

    print()
    if sv.get("error"):
        print("sleeve_view error: %s" % sv["error"])
    elif sv.get("pct") is None:
        print("LONG-PREMIUM SLEEVE (W194): no account value on file -- cannot compute.")
    else:
        over = " -- OVER THE %.0f%% CAP, no new longs (W194)" % sv["cap_pct"] if sv["over_cap"] else ""
        print("LONG-PREMIUM SLEEVE (W194)  |  book %s  |  $%s of $%s  |  %.2f%% (cap %.0f%%)%s"
              % (book, format(int(sv["value"]), ",d"), format(int(sv["account_value"]), ",d"),
                 sv["pct"], sv["cap_pct"], over))
        for p in sorted(sv["positions"], key=lambda x: -x["cost"]):
            print("  %-8s %-14s $%s" % (p["ticker"], p["strategy"], format(int(p["cost"]), ",d")))
    return 0
