#!/usr/bin/env python3
"""W7 — verify an open books the contract that was CLICKED, not a rank.

`helm open T S --confirm` re-pulls the chain after the trader has chosen, so a
rank computed against the displayed board can address a different strike or
expiry in the fresh pull. --strike/--expiry name the contract instead.

The property under test: **with a pin, the rank prompt is never reached, and a
contract that has moved is REFUSED rather than substituted.** Proven with a
Prompt.ask that raises, and by asserting nothing was booked.

No database, no network, no rich. python3.12 (PEP 701 f-strings in the engine).

  python3.12 verify_w7_pin_contract.py [HELM_ROOT] [PG_ROOT]
"""
import sys
import types

HELM = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/helm"
PG = sys.argv[2] if len(sys.argv) > 2 else "/mnt/user-data/uploads/helm-pg"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name}{(' — ' + detail) if detail else ''}")


class PromptWasReached(AssertionError):
    pass


class _Prompt:
    @staticmethod
    def ask(*a, **k):
        raise PromptWasReached(a[0] if a else "prompt")


class _Confirm:
    @staticmethod
    def ask(*a, **k):
        return True


def _stub():
    for n, attrs in [
        ("rich", {}), ("rich.console", {}), ("rich.table", {"Table": object}),
        ("rich.panel", {"Panel": object}), ("rich.box", {}),
        ("rich.prompt", {"Prompt": _Prompt, "Confirm": _Confirm}),
    ]:
        m = types.ModuleType(n)
        for k, val in attrs.items():
            setattr(m, k, val)
        sys.modules[n] = m
    sys.modules["rich.console"].Console = lambda *a, **k: types.SimpleNamespace(
        print=lambda *a, **k: None)
    sys.modules["rich.panel"].Panel = types.SimpleNamespace(fit=lambda *a, **k: None)
    sys.modules["rich"].box = sys.modules["rich.box"]

    # NOTE: do NOT stub the `helm` package itself here. open_cmd imports
    # helm.config/helm.db/helm.strategies at module level and needs the real
    # package on sys.path; a placeholder module shadows it and nothing loads.
    # The one leaf that must be faked (entry_snapshot) is registered later,
    # once the real package has been imported.


_stub()
sys.path.insert(0, HELM)

import importlib.util
spec = importlib.util.spec_from_file_location("open_cmd", f"{HELM}/helm/cli/open_cmd.py")
oc = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(oc)
    LOADED = True
except Exception as e:                       # missing optional deps, etc.
    LOADED = False
    check("open_cmd imports for testing", False, f"{type(e).__name__}: {e}")

if LOADED and not hasattr(oc, "_pin_pick"):
    # Against pre-W7 code the helper does not exist. Report that as the failure
    # it is instead of dying on an AttributeError and hiding every check below.
    check("open_cmd exposes the contract matcher (_pin_pick)", False,
          "not present — this is pre-W7 code")
    LOADED = False

if LOADED:
    check("open_cmd imports for testing", True)

    CHAIN = [
        {"strike": 195.0, "expiration": "2026-08-21", "mid": 2.10},
        {"strike": 190.0, "expiration": "2026-08-21", "mid": 1.40},
        {"strike": 195.0, "expiration": "2026-09-18", "mid": 3.30},
    ]
    SPREADS = [
        {"short_strike": 250.0, "long_strike": 255.0, "expiration": "2026-08-21"},
        {"short_strike": 250.0, "long_strike": 260.0, "expiration": "2026-08-21"},
        {"short_strike": 245.0, "long_strike": 250.0, "expiration": "2026-08-21"},
    ]

    # --- the matcher itself ------------------------------------------------- #
    got, err = oc._pin_pick(CHAIN, 195.0, "2026-08-21")
    check("names the exact strike+expiry, not the first strike match",
          got is CHAIN[0], f"err={err}")

    got, err = oc._pin_pick(CHAIN, 195.0, "2026-09-18")
    check("same strike, later expiry resolves to the other contract",
          got is CHAIN[2], f"err={err}")

    got, err = oc._pin_pick(CHAIN, 197.5, "2026-08-21")
    check("a strike that is NOT in the chain refuses — no nearest-match",
          got is None and "no contract" in (err or ""), f"err={err}")

    got, err = oc._pin_pick(CHAIN, 195.0, "2026-10-16")
    check("right strike, wrong expiry refuses", got is None, f"err={err}")

    got, err = oc._pin_pick(CHAIN, 195.004, "2026-08-21")
    check("float tolerance is tight but real (195.004 == 195.0)", got is CHAIN[0])

    got, err = oc._pin_pick(CHAIN, 195.5, "2026-08-21")
    check("tolerance does not stretch to the next strike", got is None)

    # spreads: ambiguity must refuse, not pick
    got, err = oc._pin_pick(SPREADS, 250.0, "2026-08-21", strike_key="short_strike",
                            long_key="long_strike")
    check("two widths sharing a short strike: refuses as ambiguous",
          got is None and "--long-strike" in (err or ""), f"err={err}")

    got, err = oc._pin_pick(SPREADS, 250.0, "2026-08-21", strike_key="short_strike",
                            long_key="long_strike", pin_long=260.0)
    check("--long-strike disambiguates", got is SPREADS[1], f"err={err}")

    got, err = oc._pin_pick([], 195.0, "2026-08-21")
    check("empty chain refuses cleanly", got is None and err)

    # --- the property: a pin must not reach the prompt ---------------------- #
    BOOKED = []

    # confirm_and_log does `from helm.cli.entry_snapshot import ...` at call
    # time. Registering the stub in sys.modules NOW -- after the real package
    # has loaded -- means the function finds it and never touches the writer or
    # its dependencies. On the first run of this file the import blew up before
    # the pin logic and both prompt checks failed loudly; had they been written
    # the other way round they would have passed vacuously instead.
    _es = types.ModuleType("helm.cli.entry_snapshot")
    _es.open_position_with_snapshot = lambda *a, **k: BOOKED.append(a) or {"ok": True}
    sys.modules["helm.cli.entry_snapshot"] = _es

    def run_confirm(pin_s, pin_e, chain=CHAIN):
        BOOKED.clear()
        try:
            oc.confirm_and_log("AAPL", "CSP", chain, {}, 200.0, None,
                               pin_strike=pin_s, pin_expiry=pin_e)
            return "returned"
        except PromptWasReached:
            return "prompted"
        except Exception as e:
            return f"{type(e).__name__}"

    r = run_confirm(197.5, "2026-08-21")
    check("moved contract: refuses WITHOUT reaching the rank prompt",
          r == "returned", f"got {r}")
    check("moved contract: nothing was booked", not BOOKED)

    r = run_confirm(None, None)
    check("no pin: the rank prompt still runs, exactly as before",
          r == "prompted", f"got {r}")

    # --- argument hygiene in run() ------------------------------------------ #
    src = open(f"{HELM}/helm/cli/open_cmd.py", encoding="utf-8").read()
    check("--strike/--expiry parsed in run()",
          '"--strike"' in src and '"--expiry"' in src)
    check("half a pin is refused rather than silently ignored",
          "(pin_strike is None) != (pin_expiry is None)" in src)
    check("condors refuse a pin instead of accepting and ignoring it",
          "not wired for iron " in src)
    check("both confirm paths receive the pin",
          "pin_strike=pin_strike" in src and src.count("pin_strike=pin_strike") >= 2)

# --------------------------------------------------------------------------- #
# PG side: what reaches argv and stdin
# --------------------------------------------------------------------------- #
sys.path.insert(0, PG)
import helm_engine as eng

SENT = {}
eng._run_cli = lambda args, stdin_text=None, timeout=None: SENT.update(
    args=args, stdin=stdin_text) or {"ok": True, "output": ""}


def log(**kw):
    SENT.clear()
    try:
        return eng.log_open(**kw), None
    except TypeError as e:
        return None, str(e)


res, err = log(ticker="aapl", strategy="CSP", family="single", rank=2,
               contracts=3, price=2.1, strike=195.0, expiry="2026-08-21")
check("PG's log_open accepts a contract pin at all", err is None, err or "")
check("PG passes --strike and --expiry",
      "--strike" in SENT.get("args", []) and "--expiry" in SENT.get("args", []),
      str(SENT.get("args", [])))
check("expiry is trimmed to a date",
      "2026-08-21" in SENT.get("args", []))

res, err = log(ticker="aapl", strategy="BEAR_CALL_SPREAD", family="spread", rank=1,
               contracts=1, price=1.0, strike=250.0, expiry="2026-08-21T00:00:00",
               long_strike=260.0)
check("spreads pin the short leg and pass the long strike",
      "--long-strike" in SENT.get("args", []) and "2026-08-21" in SENT.get("args", []),
      str(SENT.get("args", [])))

res, err = log(ticker="aapl", strategy="IRON_CONDOR", family="condor", rank=1,
               contracts=1, price=1.0, strike=250.0, expiry="2026-08-21")
check("condors are NOT given a pin PG knows the engine would refuse",
      "--strike" not in SENT.get("args", []), str(SENT.get("args", [])))

# stdin coercion — a newline in a price must not shift the prompt sequence
res, err = log(ticker="aapl", strategy="CSP", family="single", rank=1,
               contracts=1, price="1.20\n50")
check("a price carrying a newline is rejected, not fed to stdin",
      res is not None and res.get("ok") is False, str(res))

res, err = log(ticker="aapl", strategy="CSP", family="single", rank=1,
               contracts="3\ny", price=1.0)
check("a contract count carrying a newline is rejected",
      res is not None and res.get("ok") is False, str(res))

res, err = log(ticker="aapl", strategy="CSP", family="single", rank=1,
               contracts=2, price=1.25)
check("a normal feed is unchanged (rank, price, contracts, confirm)",
      SENT.get("stdin", "").splitlines() == ["1", "1.25", "2", "y"],
      repr(SENT.get("stdin", "")))

# --- wiring ---------------------------------------------------------------- #
app_src = open(f"{PG}/app.py", encoding="utf-8").read()
check("app.py forwards strike/expiry", 'strike=payload.get("strike")' in app_src
      and 'expiry=payload.get("expiry")' in app_src)
store_src = open(f"{PG}/engine_store.py", encoding="utf-8").read()
check("engine_store forwards the pin", "strike=strike" in store_src)
tpl = open(f"{PG}/templates/open.html", encoding="utf-8").read()
check("the open form sends the contract it displayed",
      "strike: v(row,'strike','short_strike')" in tpl and "expiry: v(row," in tpl)
check("a deliberate 0 is no longer swallowed by `|| null`",
      "numOrNull" in tpl and "parseFloat(document.getElementById('lfPrice').value) || null" not in tpl)

# --------------------------------------------------------------------------- #
print()
for line in PASS:
    print(f"  ok    {line}")
for line in FAIL:
    print(f"  FAIL  {line}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
