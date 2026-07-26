#!/usr/bin/env python3
"""W9 — verify the portfolio pulse: one computation, correct denominators.

Two things under test:
  1. The arithmetic. portfolio_pulse() is exercised against a constructed book
     with known answers, so "a number appeared" is not mistaken for "the right
     number appeared".
  2. That the CLI and the web app now share it rather than each keeping a copy
     that can drift.

No database is required for the arithmetic checks: the DB read (account value,
earnings dates) is wrapped, and its absence degrades to None, which is itself
asserted.

  python3.12 verify_w9_portfolio_pulse.py [HELM_ROOT] [PG_ROOT]
"""
import datetime as dt
import importlib.util
import sys
import types

HELM = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/helm"
PG = sys.argv[2] if len(sys.argv) > 2 else "/mnt/user-data/uploads/helm-pg"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name}{(' — ' + detail) if detail else ''}")


# --- import check_cmd with its heavy deps stubbed --------------------------- #
for n, attrs in [("rich", {}), ("rich.console", {}), ("rich.table", {"Table": object}),
                 ("rich.panel", {"Panel": object}), ("rich.box", {}),
                 ("rich.columns", {"Columns": object}),
                 ("rich.prompt", {"Prompt": object, "Confirm": object}),
                 ("rich.text", {"Text": object}), ("rich.live", {"Live": object}),
                 ("rich.progress", {}), ("rich.align", {"Align": object}),
                 ("rich.rule", {"Rule": object})]:
    m = types.ModuleType(n)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(n, m)
sys.modules["rich.console"].Console = lambda *a, **k: types.SimpleNamespace(
    print=lambda *a, **k: None)
sys.modules["rich"].box = sys.modules["rich.box"]

sys.path.insert(0, HELM)
try:
    spec = importlib.util.spec_from_file_location("cc9", f"{HELM}/helm/cli/check_cmd.py")
    cc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cc)
    ok_import = True
except Exception as e:
    ok_import = False
    check("check_cmd imports", False, f"{type(e).__name__}: {e}")

if ok_import:
    check("check_cmd imports", True)
    check("portfolio_pulse exists (the shared computation)",
          hasattr(cc, "portfolio_pulse"))

if ok_import and hasattr(cc, "portfolio_pulse"):
    T = dt.date.today()

    def leg(strike=None, contracts=1, direction="SHORT", exp_days=40):
        return {"strike": strike, "contracts": contracts, "direction": direction,
                "expiration": (T + dt.timedelta(days=exp_days)).isoformat()}

    # --- risk basis, per family --------------------------------------------- #
    r = cc._position_risk({"strategy": "CSP"}, [leg(strike=100, contracts=3)])
    check("CSP risk = strike x 100 x contracts", r == 30000, f"got {r}")

    r = cc._position_risk({"strategy": "IRON_CONDOR", "max_loss": 6560}, [])
    check("defined risk uses max_loss", r == 6560, f"got {r}")

    r = cc._position_risk({"strategy": "LONG_CALL", "net_premium": -4942}, [])
    check("long premium uses the debit paid, unsigned", r == 4942, f"got {r}")

    r = cc._position_risk({"strategy": "CSP"}, [leg(strike=100, contracts=2),
                                               leg(strike=90, direction="LONG")])
    check("CSP counts only the SHORT leg's collateral", r == 20000, f"got {r}")

    r = cc._position_risk({"strategy": "CSP"}, [{"strike": None, "contracts": None,
                                                 "direction": "SHORT"}])
    check("a malformed leg degrades to 0 rather than raising", r == 0)

    # --- aggregate over a known book ---------------------------------------- #
    rows = [
        {"ticker": "AAA", "family": "CSP", "pnl": -1000,
         "pos": {"strategy": "CSP"}, "legs": [leg(strike=100, contracts=2)]},      # 20,000
        {"ticker": "BBB", "family": "IC", "pnl": -3000,
         "pos": {"strategy": "IRON_CONDOR", "max_loss": 10000}, "legs": [leg(exp_days=9)]},
        {"ticker": "CCC", "family": "LONG_CALL", "pnl": +500,
         "pos": {"strategy": "LONG_CALL", "net_premium": -5000}, "legs": [leg(exp_days=120)]},
    ]
    P = cc.portfolio_pulse(rows)

    check("capital at work sums the three bases",
          P["capital_at_work"] == 35000, f"got {P['capital_at_work']}")
    check("assigned value counts only CSP collateral",
          P["assigned_value"] == 20000, f"got {P['assigned_value']}")
    check("total p&l is the sum", P["total_pnl"] == -3500, f"got {P['total_pnl']}")
    check("p&l is expressed against capital at work",
          abs(P["pnl_pct_of_capital"] - (-3500 / 35000 * 100)) < 1e-9,
          str(P["pnl_pct_of_capital"]))
    check("position count carried", P["n"] == 3)

    # --- the manage window --------------------------------------------------- #
    check("a position inside 21 days lands in the manage window",
          [m["ticker"] for m in P["manage"]] == ["BBB"], str(P["manage"]))
    check("the manage window reports its DTE", P["manage"][0]["dte"] == 9)
    check("the soonest position OUTSIDE the window is the next to arrive",
          P["next_expiry"] and P["next_expiry"]["ticker"] == "AAA",
          str(P["next_expiry"]))

    # --- concentration ------------------------------------------------------- #
    c = P["concentration"]
    check("concentration picks the family holding most of the drawdown",
          c and c["family"] == "IC", str(c))
    check("drawdown share is computed against total drawdown, not total p&l",
          c and c["pct"] == 75, f"got {c and c['pct']}")   # 3000 of 4000
    check("capital share is reported ALONGSIDE it — the whole point of W9",
          c and c["capital_pct"] == 29, f"got {c and c.get('capital_pct')}")
    check("the two shares genuinely differ, which is why the label mattered",
          c and c["pct"] != c["capital_pct"])

    # a book with no drawdown must not invent one
    P2 = cc.portfolio_pulse([{"ticker": "X", "family": "CSP", "pnl": 10,
                              "pos": {"strategy": "CSP"},
                              "legs": [leg(strike=50)]}])
    check("no drawdown -> no concentration card", P2["concentration"] is None)

    # --- an empty book ------------------------------------------------------- #
    P3 = cc.portfolio_pulse([])
    check("an empty book returns zeros, not an exception",
          P3["n"] == 0 and P3["capital_at_work"] == 0 and P3["earnings"] == [])

# --- the two surfaces share it ---------------------------------------------- #
eng = open(f"{PG}/helm_engine.py", encoding="utf-8").read()
tpl = open(f"{PG}/templates/positions.html", encoding="utf-8").read()
cli = open(f"{HELM}/helm/cli/check_cmd.py", encoding="utf-8").read()

check("PG calls the shared computation", "portfolio_pulse as _pp" in eng)
check("PG no longer keeps its own drawdown mirror",
      "total_draw = sum(draw.values())" not in eng)
check("the CLI renders from the shared computation",
      "P = portfolio_pulse(rows)" in cli)

check("the up/down card is gone from the CLI", "card_pos" not in cli)
# Match the CODE, not the prose. The first version of this check looked for
# "up · " and matched the comment explaining that the card had been removed --
# a check that fails precisely because the work was documented.
check("the up/down card is gone from the web", "${p.up} up" not in tpl)
check("capital-at-work card exists on both",
      "capital at work" in cli and "capital at work" in tpl)
check("coming-up card exists on both", "coming up" in cli and "coming up" in tpl)
check("concentration is relabelled on both",
      "drawdown concentration" in cli and "drawdown concentration" in tpl)
check("delta carries a percentage on both",
      "net long" in cli and "net long" in tpl)
check("theta shows a monthly figure on both",
      "/mo" in cli and "/mo" in tpl)

print()
for line in PASS:
    print(f"  ok    {line}")
for line in FAIL:
    print(f"  FAIL  {line}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
