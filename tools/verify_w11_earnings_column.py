#!/usr/bin/env python3
"""W11 — verify the earnings display: one source, and no shifted columns.

Three things under test:

  1. `_earnings_state` / `_earn_cell` — the states are distinguished. "No date
     on file", "the date we had has passed", "print due before expiry" and
     "print due after expiry" are four different facts and must not render the
     same. Nothing may render blank.

  2. The SOURCE. The pulse card used to read watchlist.next_earnings while the
     row-level field is positions.earnings_date. The test runs with NO database
     reachable, so anything appearing in P["earnings"] can only have come from
     the position row. A harness that ran against the live DB would pass either
     way and prove nothing.

  3. COLUMN ALIGNMENT — the real regression risk. Adding a column to a table
     means adding a cell to add_row, in the same slot. Miss one and every value
     to the right of it shifts one column left, silently, and the table still
     renders. So each renderer is driven with a constructed position through a
     recording Table, and the declared column count is compared against the
     cell count of every row it emits.

  python3.12 verify_w11_earnings_column.py [HELM_ROOT]
"""
import datetime as dt
import importlib.util
import sys
import types

HELM = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/helm"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name}{(' — ' + detail) if detail else ''}")


# --- a recording Table: counts columns declared vs cells added -------------- #
class RecTable:
    def __init__(self, *a, **k):
        self.cols, self.rows = [], []

    def add_column(self, name="", **kw):
        self.cols.append(name)

    def add_row(self, *cells, **kw):
        self.rows.append(cells)


PRINTED = []

for n, attrs in [("rich", {}), ("rich.console", {}),
                 ("rich.box", {"SIMPLE_HEAD": object(), "SIMPLE": object(),
                               "ROUNDED": object(), "MINIMAL": object(),
                               "HEAVY_HEAD": object(), "SQUARE": object()}),
                 ("rich.panel", {"Panel": lambda *a, **k: None}),
                 ("rich.columns", {"Columns": lambda *a, **k: None}),
                 ("rich.prompt", {"Prompt": object, "Confirm": object}),
                 ("rich.text", {"Text": object}), ("rich.live", {"Live": object}),
                 ("rich.progress", {}), ("rich.align", {"Align": object}),
                 ("rich.rule", {"Rule": object})]:
    m = types.ModuleType(n)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(n, m)
_tm = types.ModuleType("rich.table")
_tm.Table = RecTable
sys.modules["rich.table"] = _tm
sys.modules["rich.console"].Console = lambda *a, **k: types.SimpleNamespace(
    print=lambda *a, **k: PRINTED.append(a[0] if a else ""))
sys.modules["rich"].box = sys.modules["rich.box"]

sys.path.insert(0, HELM)
try:
    spec = importlib.util.spec_from_file_location("cc11", f"{HELM}/helm/cli/check_cmd.py")
    cc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cc)
    ok = True
    check("check_cmd imports", True)
except Exception as e:
    ok = False
    check("check_cmd imports", False, f"{type(e).__name__}: {e}")

T = dt.date.today()
D = lambda n: (T + dt.timedelta(days=n)).isoformat()


def leg(days=30, strike=100, direction="SHORT", opt="PUT", contracts=1, lid=1):
    return {"id": lid, "strike": strike, "contracts": contracts,
            "direction": direction, "option_type": opt, "expiration": D(days)}


# ============ 1. the four states are distinguished ========================== #
if ok:
    check("_earnings_state exists", hasattr(cc, "_earnings_state"))
if ok and hasattr(cc, "_earnings_state"):
    S = cc._earnings_state

    ed, d, before = S({"earnings_date": D(3)}, [leg(days=30)])
    check("a print before expiry is flagged before expiry", before is True)
    check("days-to-earnings is the gap from today", d == 3, f"got {d}")

    ed, d, before = S({"earnings_date": D(40)}, [leg(days=30)])
    check("a print AFTER expiry is not flagged", before is False, str(before))
    check("but its date is still reported", d == 40 and ed == D(40))

    ed, d, before = S({"earnings_date": D(-5)}, [leg(days=30)])
    check("a passed date reports a negative gap, not None", d == -5, str(d))
    check("a passed date is not an upcoming exposure", before is False)

    check("no stored date returns the empty state",
          S({"earnings_date": None}, [leg()]) == (None, None, None))
    check("an unparseable date degrades, it does not raise",
          S({"earnings_date": "not-a-date"}, [leg()]) == (None, None, None))
    check("a missing pos degrades", S(None, None) == (None, None, None))

    # None vs False is load-bearing: "no expiry to compare with" is not
    # "compared, and the print falls outside".
    ed, d, before = S({"earnings_date": D(3)}, [])
    check("no expiry -> before_expiry is None, NOT False", before is None, str(before))

    # the nearest leg governs a multi-leg structure
    _, _, before = S({"earnings_date": D(20)},
                     [leg(days=14, lid=1), leg(days=45, lid=2)])
    check("the NEAREST leg expiry governs, so a 20d print beats a 14d wing",
          before is False, str(before))
    _, _, before = S({"earnings_date": D(10)},
                     [leg(days=14, lid=1), leg(days=45, lid=2)])
    check("a print inside the nearest leg is flagged", before is True)

# ============ 2. the cell never lies and never blanks ====================== #
if ok:
    check("_earn_cell exists", hasattr(cc, "_earn_cell"))
if ok and hasattr(cc, "_earn_cell"):
    C = cc._earn_cell
    before_c = C({"earnings_date": D(3)}, [leg(days=30)])
    after_c = C({"earnings_date": D(40)}, [leg(days=30)])
    none_c = C({"earnings_date": None}, [leg(days=30)])
    past_c = C({"earnings_date": D(-5)}, [leg(days=30)])

    for label, cell in [("before", before_c), ("after", after_c),
                        ("no date", none_c), ("passed", past_c)]:
        check(f"the {label} cell is never blank", bool(cell.strip()), repr(cell))
    check("the four states render four different cells",
          len({before_c, after_c, none_c, past_c}) == 4)
    check("a print before expiry carries the marker", "!" in before_c, repr(before_c))
    check("a print after expiry carries NO marker", "!" not in after_c, repr(after_c))
    check("the date is shown as MM-DD", D(3)[5:] in before_c, repr(before_c))
    check("days-to-print is shown alongside it", "(3d)" in before_c, repr(before_c))
    check("a missing date says so in words", "no date" in none_c, repr(none_c))
    check("a passed date says passed rather than showing a stale countdown",
          "passed" in past_c and "-5" not in past_c, repr(past_c))

    # Colour discipline (the W8 rule): the scheme is white + dim, with colour
    # reserved for the kept%/DTE warnings. A new hue here would erode those.
    joined = before_c + after_c + none_c + past_c
    for hue in ("red", "green", "yellow", "cyan", "magenta", "blue"):
        check(f"no {hue} introduced — distinction is carried by weight",
              hue not in joined, repr(joined))
    check("the row that matters is undimmed, the rest are dim",
          "[dim]" not in before_c and "[dim]" in after_c, repr(before_c))

# ============ 3. the pulse reads the POSITION ROW, with no DB ============== #
if ok and hasattr(cc, "portfolio_pulse"):
    rows = [
        # print before expiry -> must appear, and can ONLY have come from pos
        {"ticker": "META", "family": "CSP", "pnl": -100,
         "pos": {"strategy": "CSP", "earnings_date": D(3)},
         "legs": [leg(days=26, strike=600)]},
        # print after expiry -> must not appear as exposure
        {"ticker": "NVDA", "family": "CSP", "pnl": 50,
         "pos": {"strategy": "CSP", "earnings_date": D(45)},
         "legs": [leg(days=26, strike=170)]},
        # no date -> unknown, not silence
        {"ticker": "DHI", "family": "CSP", "pnl": -20,
         "pos": {"strategy": "CSP", "earnings_date": None},
         "legs": [leg(days=26, strike=140)]},
        # stored date has gone by -> also unknown
        {"ticker": "GOOG", "family": "LONG_CALL", "pnl": -30,
         "pos": {"strategy": "LONG_CALL", "net_premium": -500,
                 "earnings_date": D(-4)},
         "legs": [leg(days=26, direction="LONG", opt="CALL")]},
        # a second position on an unknown name -> deduped in the count
        {"ticker": "DHI", "family": "CREDIT_SPREAD", "pnl": -10,
         "pos": {"strategy": "BEAR_CALL_SPREAD", "max_loss": 500,
                 "earnings_date": None},
         "legs": [leg(days=26, strike=150)]},
    ]
    P = cc.portfolio_pulse(rows)

    check("the pulse still returns without a database", isinstance(P, dict))
    check("a print before expiry reaches the card FROM THE POSITION ROW",
          [e["ticker"] for e in P["earnings"]] == ["META"], str(P["earnings"]))
    check("the date carried is the row's date",
          P["earnings"] and P["earnings"][0]["date"] == D(3))
    check("a print after expiry is not counted as exposure",
          "NVDA" not in [e["ticker"] for e in P["earnings"]])
    # .get(), not [...]: on the pre-change module this key does not exist, and
    # the control run has to report that as a failure rather than die on a
    # KeyError. A harness that crashes proves nothing about what it crashed on.
    _unk = P.get("earnings_unknown")
    check("the pulse reports which positions have no usable date",
          _unk is not None, "key absent")
    _unk = _unk or []
    check("positions with no usable date are counted, not dropped",
          _unk == ["DHI", "GOOG"], str(_unk))
    check("two positions on one unknown name count once", _unk.count("DHI") == 1)
    # Every position is accounted for: exposed, unknown, or measured-and-clear.
    # NVDA is the only one that should fall in the third bucket, and silence
    # about it is then a real statement rather than an absence of data.
    _named = {e["ticker"] for e in P["earnings"]} | set(_unk)
    _unaccounted = {r["ticker"] for r in rows} - _named
    check("every position is exposed, unknown, or measured-and-clear",
          _unaccounted == {"NVDA"}, str(_unaccounted))

    # W9's other numbers must not have moved. CSPs by collateral, the spread by
    # max_loss, the long call by the debit paid.
    check("capital at work is unchanged by this work",
          P["capital_at_work"] == 60000 + 17000 + 14000 + 500 + 500,
          str(P["capital_at_work"]))
    check("the concentration card still computes", P["concentration"] is not None)
    check("an empty book still returns zeros",
          cc.portfolio_pulse([]).get("earnings_unknown") == [])

# --- the retired source is really gone ------------------------------------- #
src = open(f"{HELM}/helm/cli/check_cmd.py", encoding="utf-8").read()
check("the pulse no longer queries watchlist.next_earnings",
      "next_earnings FROM watchlist" not in src)
check("the position row is the source",
      'get("earnings_date")' in src)

# ============ 4. no renderer has a shifted column ========================== #
# The failure this guards against: a column declared but no cell added (or the
# reverse). The table still renders; every value right of the gap is simply
# wrong. Structural greps cannot see it — only counting can.
if ok:
    def mkrow(family, strategy, legs, **extra):
        a = {"underlying_price": 100.0, "pnl_mtm": -125.0, "pnl_pct": -12.5,
             "primary_leg": legs[0], "opt_data": {"delta": -0.28, "iv": 31.0},
             "leg_greeks": {}, "mark_confidence": "live"}
        a.update(extra.pop("a", {}))
        return {"ticker": "TEST", "family": family, "pnl": -125.0,
                "pos": {"id": 1, "ticker": "TEST", "strategy": strategy,
                        "net_premium": 250.0, "max_loss": 500.0,
                        "total_contracts": 1, "earnings_date": D(3),
                        "breakeven_low": 95.0, "breakeven_high": 105.0,
                        "spread_width": 5.0},
                "legs": legs, "a": a, "_dte": 26, "_delta": -0.28,
                "ivr": 47.0, "beta": 1.0, **extra}

    ic_legs = [leg(days=26, strike=90, opt="PUT", direction="LONG", lid=1),
               leg(days=26, strike=95, opt="PUT", direction="SHORT", lid=2),
               leg(days=26, strike=105, opt="CALL", direction="SHORT", lid=3),
               leg(days=26, strike=110, opt="CALL", direction="LONG", lid=4)]

    cases = [
        ("CSP", "_render_csp", mkrow("CSP", "CSP", [leg(days=26)])),
        ("credit spread", "_render_credit",
         mkrow("CREDIT_SPREAD", "BEAR_CALL_SPREAD",
               [leg(days=26, strike=105, opt="CALL", direction="SHORT", lid=1),
                leg(days=26, strike=110, opt="CALL", direction="LONG", lid=2)])),
        ("iron condor", "_render_ic", mkrow("IC", "IRON_CONDOR", ic_legs)),
        ("long call", "_render_longcall",
         mkrow("LONG_CALL", "LONG_CALL",
               [leg(days=26, strike=95, opt="CALL", direction="LONG")])),
        ("other", "_render_other",
         mkrow("OTHER", "DIAGONAL", [leg(days=26, strike=95, opt="CALL")])),
    ]

    for label, fname in [(c[0], c[1]) for c in cases]:
        check(f"{label} renderer exists", hasattr(cc, fname))

    for label, fname, row in cases:
        if not hasattr(cc, fname):
            continue
        PRINTED.clear()
        try:
            getattr(cc, fname)([row])
        except Exception as e:
            check(f"{label} table renders", False, f"{type(e).__name__}: {e}")
            continue
        tables = [p for p in PRINTED if isinstance(p, RecTable)]
        if not tables:
            check(f"{label} table renders", False, "no table printed")
            continue
        t = tables[0]
        check(f"{label} table renders", True)
        check(f"{label} declares an earnings column",
              "earnings" in t.cols, str(t.cols))
        for cells in t.rows:
            check(f"{label}: {len(t.cols)} columns, {len(cells)} cells — aligned",
                  len(cells) == len(t.cols),
                  f"cols={t.cols} cells={cells}")
        # and the earnings cell must sit in the earnings slot
        if "earnings" in t.cols and t.rows and len(t.rows[0]) == len(t.cols):
            i = t.cols.index("earnings")
            cell = t.rows[0][i]
            check(f"{label}: the earnings slot holds the earnings value",
                  D(3)[5:] in str(cell), f"slot {i} held {cell!r}")

print()
for line in PASS:
    print(f"  ok    {line}")
for line in FAIL:
    print(f"  FAIL  {line}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
