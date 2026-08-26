#!/usr/bin/env python3
"""W151 step 1 - prove the typed parser reproduces the frozen fixture.

READ-ONLY. Touches no database and writes nothing.

    python3 tools/w151_fill_parse.py            # parse and show the fixture
    python3 tools/w151_fill_parse.py --selftest # assert against expected.json

The fixture is worth trusting because it carries all five Fidelity action
forms, a partial close (20 -> 7 traded + 13 settled), an assignment, an
exercise, two expiries - and a trap: the 2026-06-02 open and 2026-06-15
close are a DIFFERENT LRCX position (a CSP, JUL 17 300) and must not be
mistaken for the condor.

Run --selftest before reading any number this tool prints.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helm.brokerfills import parse_activity_csv  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "fidelity_activity_lrcx_s108.csv")
EXPECTED = os.path.join(HERE, "fixtures", "fidelity_activity_lrcx_s108.expected.json")

# expected.json key -> Fill attribute. Only keys actually present are compared,
# so a group that records fewer fields asserts fewer things rather than failing.
FIELD_MAP = {
    "symbol": "symbol",
    "action": "action",
    "direction": "leg_direction",
    "type": "option_type",
    "strike": "strike",
    "qty": "qty",
    "price": "price",
    "commission": "commission",
    "fees": "fees",
    "amount": "amount",
    "date": "as_of",
}

results = []


def check(ok, label, got=None, want=None):
    results.append((bool(ok), label, got, want))


def same(got, want):
    if want is None or got is None:
        return got is want or got == want
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return round(float(got), 2) == round(float(want), 2)
    return str(got) == str(want)


def find(fills, spec):
    """Locate the one fill an expected row describes - symbol + action + qty."""
    for fill in fills:
        if fill.symbol != spec["symbol"] or fill.action != spec["action"]:
            continue
        if "qty" in spec and fill.qty != spec["qty"]:
            continue
        return fill
    return None


def compare_group(fills, rows, label):
    for spec in rows:
        fill = find(fills, spec)
        if fill is None:
            check(False, label + " " + spec["symbol"] + " " + spec["action"], "NOT FOUND")
            continue
        for key, attr in FIELD_MAP.items():
            if key not in spec:
                continue
            got = getattr(fill, attr)
            check(same(got, spec[key]), label + " " + spec["symbol"] + " " + key, got, spec[key])


def main():
    parsed = parse_activity_csv(FIXTURE)
    fills = parsed.fills
    selftest = "--selftest" in sys.argv

    if not selftest:
        print("rows parsed %d, non-transaction rows skipped %d" % (len(fills), parsed.skipped))
        for fill in sorted(fills, key=lambda f: (f.as_of, f.symbol)):
            print(
                "  %s %-16s %-16s %-5s %-7s qty %5d  px %s"
                % (
                    fill.as_of + ("*" if fill.dated_from_as_of else " "),
                    fill.symbol,
                    fill.action,
                    fill.leg_direction or "-",
                    fill.event,
                    fill.qty,
                    ("%.2f" % fill.price) if fill.price is not None else "-",
                )
            )
        print("  * dated from the as-of text, not Run Date")
        return 0

    want = json.load(open(EXPECTED))

    # The condor is defined by its four contracts, not by "every OPEN row".
    # Aggregating without this silently swallows the CSP the fixture plants.
    condor = {f.contract for f in fills if f.is_option and f.expiration == "2026-08-21"}

    # 1 - the disclaimer block is not data.
    check(len(fills) == 16, "16 transactions parsed", len(fills), 16)
    check(parsed.skipped > 0, "disclaimer rows skipped", parsed.skipped, "> 0")
    check(
        all(f.symbol and (f.is_option or f.ticker.isalpha()) for f in fills),
        "every parsed row carries a symbol",
    )

    # 2 - the entry, which is the whole point: broker fills, not scaled quotes.
    entry = want["entry_2026_06_29"]
    compare_group(fills, entry["legs"], "entry")
    opened = [f for f in fills if f.event == "OPEN" and f.contract in condor]
    check(len(opened) == 4, "four opening legs", len(opened), 4)
    gross = round(sum(f.gross_cash for f in opened), 2)
    check(same(gross, entry["gross_credit"]), "entry gross credit", gross, entry["gross_credit"])
    net = round(sum(f.amount for f in opened), 2)
    check(same(net, entry["net_cash_received"]), "entry net cash", net, entry["net_cash_received"])
    costs = round(sum(f.commission + f.fees for f in opened), 2)
    check(same(costs, entry["commissions_and_fees"]), "entry costs", costs, entry["commissions_and_fees"])

    # 3 - the partial. Seven of twenty: the reason quantity is carried and the
    #     reason direction is in the key.
    partial = want["partial_close_2026_08_20"]
    compare_group(fills, partial["legs"], "partial")
    closed = [f for f in fills if f.event == "CLOSE" and f.contract in condor]
    check(len(closed) == 4, "four closing legs", len(closed), 4)
    check(all(abs(f.qty) == 7 for f in closed), "partial is seven of twenty")
    net = round(sum(f.amount for f in closed), 2)
    check(same(net, partial["net_cash"]), "partial net cash", net, partial["net_cash"])
    gross = round(sum(f.gross_cash for f in closed), 2)
    check(same(gross, partial["gross"]), "partial gross", gross, partial["gross"])

    # 4 - the as-of date outranks Run Date.
    assign = want["assignment_2026_08_19"]
    compare_group(fills, [assign["option_row"], assign["share_row"]], "assignment")
    row = find(fills, assign["option_row"])
    check(row is not None and row.as_of == "2026-08-19", "assignment dated 08-19 not 08-20",
          row.as_of if row else None, "2026-08-19")
    check(row is not None and row.run_date == "2026-08-20", "assignment Run Date kept",
          row.run_date if row else None, "2026-08-20")
    check(row is not None and row.dated_from_as_of, "assignment flagged as as-of dated")

    expiry = want["expiry_2026_08_21"]
    compare_group(fills, expiry["rows"], "expiry")
    for spec in expiry["rows"]:
        row = find(fills, spec)
        check(row is not None and row.as_of == "2026-08-21",
              "expiry " + spec["symbol"] + " dated 08-21", row.as_of if row else None, "2026-08-21")

    # 5 - THE TRAP. A different LRCX position must not share the condor's identity.
    check(len(condor) == 4, "condor has four distinct contracts", len(condor), 4)
    compare_group(fills, want["must_not_match"], "trap")
    for spec in want["must_not_match"]:
        row = find(fills, spec)
        check(row is not None and row.contract not in condor,
              "trap " + spec["symbol"] + " is not a condor contract")

    # 6 - the ambiguity HELM-187 cannot see, and the widened key can.
    p360 = [f for f in fills if f.symbol == "-LRCX260821P360"]
    check(len(p360) == 3, "P360 appears three times", len(p360), 3)
    check(len({f.contract for f in p360}) == 1, "all three share one contract identity")
    check(len({f.key for f in p360}) == 3, "the widened key separates all three",
          len({f.key for f in p360}), 3)

    # 6b - the direction inversion on a close, pinned.
    # Added after a perturbation test: flipping BOUGHT/SOLD CLOSING to the
    # wrong leg direction left every other check green. A close must name the
    # same leg the open named, or the confirm write will correct the wrong leg.
    by_open = {f.contract: f.leg_direction for f in opened}
    for fill in closed:
        check(
            fill.leg_direction == by_open.get(fill.contract),
            "close names the leg its open named " + fill.symbol,
            fill.leg_direction,
            by_open.get(fill.contract),
        )
    shorts = {f.symbol for f in closed if f.leg_direction == "SHORT"}
    check(shorts == {"-LRCX260821P360", "-LRCX260821C560"},
          "a BOUGHT close closes the SHORT legs", sorted(shorts),
          ["-LRCX260821C560", "-LRCX260821P360"])

    failed = [r for r in results if not r[0]]
    for ok, label, got, wanted in results:
        if not ok:
            print("DRIFT  %-46s got %r want %r" % (label, got, wanted))
    print("%s  %d checks, %d drifted" % ("PASS" if not failed else "FAIL", len(results), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
