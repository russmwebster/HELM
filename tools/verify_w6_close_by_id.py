#!/usr/bin/env python3
"""W6 — verify a close is identified by ID, not by place in a list.

The defect: `helm close TICKER` only prompts "which position?" when a ticker has
more than one open REAL position. PG answered that prompt from a snapshot up to
two minutes old. If the count dropped in between, the prompt never appeared and
the leading ordinal was consumed as LEG 1'S CLOSE PRICE -- every later price
shifted one leg and the close committed wrong realized P&L, reporting success.

So the property under test is behavioural, not structural: **when a position id
is supplied, the selection prompt must never be reached.** The harness proves it
by installing a Prompt.ask that raises if anything calls it.

No database, no network, no rich, no ib_insync -- the engine's imports are
stubbed. Run with python3.12 (the engine uses PEP 701 f-strings).

  python3.12 verify_w6_close_by_id.py [HELM_ROOT] [PG_ROOT]
"""
import sys
import types

HELM = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/helm"
PG = sys.argv[2] if len(sys.argv) > 2 else "/mnt/user-data/uploads/helm-pg"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name}{(' — ' + detail) if detail else ''}")


# --------------------------------------------------------------------------- #
# Stubs. The point of each is to make a wrong branch LOUD, not to emulate rich.
# --------------------------------------------------------------------------- #
class PromptWasReached(AssertionError):
    pass


class _Prompt:
    calls = []

    @staticmethod
    def ask(*a, **k):
        _Prompt.calls.append(a[0] if a else "")
        raise PromptWasReached(a[0] if a else "prompt")


class _Confirm:
    @staticmethod
    def ask(*a, **k):
        return True


def _install_stubs():
    con = types.ModuleType("rich.console")
    con.Console = lambda *a, **k: types.SimpleNamespace(print=lambda *a, **k: None)
    tab = types.ModuleType("rich.table"); tab.Table = object
    pan = types.ModuleType("rich.panel"); pan.Panel = object
    pro = types.ModuleType("rich.prompt"); pro.Prompt = _Prompt; pro.Confirm = _Confirm
    box = types.ModuleType("rich.box")
    rich = types.ModuleType("rich"); rich.box = box
    for n, m in [("rich", rich), ("rich.console", con), ("rich.table", tab),
                 ("rich.panel", pan), ("rich.prompt", pro), ("rich.box", box)]:
        sys.modules[n] = m

    helm = types.ModuleType("helm")
    cfg = types.ModuleType("helm.config"); cfg.get_active_account = lambda: "acct_1"
    models = types.ModuleType("helm.models")
    posm = types.ModuleType("helm.models.position")
    legm = types.ModuleType("helm.models.leg")
    dbm = types.ModuleType("helm.db")
    dbm.get_conn = lambda *a, **k: None
    dbm.transaction = lambda *a, **k: None

    class Position:
        store = []

        @staticmethod
        def by_ticker(ticker, status=None):
            return list(Position.store)

    class Leg:
        @staticmethod
        def for_position(pid):
            return [types.SimpleNamespace(id="L1", direction="SHORT", strike=100.0,
                                          option_type="PUT", leg_role="SHORT_PUT",
                                          expiration="2026-08-21", open_price=1.0,
                                          contracts=1, multiplier=100)]

    posm.Position = Position
    legm.Leg = Leg
    for n, m in [("helm", helm), ("helm.config", cfg), ("helm.models", models),
                 ("helm.models.position", posm), ("helm.models.leg", legm),
                 ("helm.db", dbm)]:
        sys.modules[n] = m
    return Position


Position = _install_stubs()
sys.path.insert(0, HELM)

import importlib.util
spec = importlib.util.spec_from_file_location("close_cmd", f"{HELM}/helm/cli/close_cmd.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

CLOSED = []
cc.close_position = lambda pos, legs, reason="manual": CLOSED.append(pos.id)


def pos(pid, strategy="CSP"):
    return types.SimpleNamespace(id=pid, ticker="AAPL", strategy=strategy,
                                 book="REAL", opened_at="2026-07-01T00:00:00",
                                 total_contracts=1)


def run_close(argv, positions):
    Position.store = positions
    CLOSED.clear()
    _Prompt.calls.clear()
    sys.argv = ["helm-close"] + argv
    reached = False
    try:
        cc.run()
    except PromptWasReached:
        reached = True
    return CLOSED[:], reached


THREE = [pos("AAPL-CSP-A"), pos("AAPL-CSP-B"), pos("AAPL-IC-C", "IRON_CONDOR")]

# --- THE property ---------------------------------------------------------- #
closed, prompted = run_close(["AAPL", "--position-id", "AAPL-CSP-B"], THREE)
check("id given, 3 open: the selection prompt is never reached", not prompted)
check("id given: the NAMED position is the one closed", closed == ["AAPL-CSP-B"],
      f"closed {closed}")

closed, prompted = run_close(["AAPL", "--position-id", "AAPL-IC-C"], THREE)
check("id given: works for any position, not just the first",
      closed == ["AAPL-IC-C"] and not prompted, f"closed {closed}")

# --- refusal beats guessing ------------------------------------------------ #
closed, prompted = run_close(["AAPL", "--position-id", "GONE-XYZ"], THREE)
check("unknown id: refuses rather than falling back to a guess",
      closed == [] and not prompted, f"closed {closed}")

# --- the legacy paths must be untouched ------------------------------------ #
closed, prompted = run_close(["AAPL"], THREE)
check("no id, 3 open: still prompts, exactly as before", prompted)

closed, prompted = run_close(["AAPL"], [pos("AAPL-CSP-A")])
check("no id, 1 open: no prompt, closes it (unchanged)",
      closed == ["AAPL-CSP-A"] and not prompted, f"closed {closed}")

closed, prompted = run_close(["AAPL"], [])
check("no open real position: closes nothing", closed == [] and not prompted)

# --- the flag must not corrupt the positional ------------------------------ #
closed, prompted = run_close(["aapl", "--position-id", "AAPL-CSP-A"], THREE)
check("flag does not leak into the ticker, and ticker still upper-cases",
      closed == ["AAPL-CSP-A"], f"closed {closed}")

# --------------------------------------------------------------------------- #
# PG side: what actually goes over stdin
# --------------------------------------------------------------------------- #
sys.path.insert(0, PG)
import helm_engine as eng

SENT = {}
eng._run_cli = lambda args, stdin_text=None, timeout=None: SENT.update(
    args=args, stdin=stdin_text) or {"ok": True, "output": ""}


def close(**kw):
    """Call close_trade, surviving a signature that does not accept the id yet.
    Against UNPATCHED code this raises TypeError; letting that escape would
    abort the run and hide every check below it, so it is recorded as a
    failure instead. A harness has to be able to say 'not fixed' out loud."""
    SENT.clear()
    try:
        eng.close_trade(**kw)
        return True
    except TypeError as e:
        SENT["error"] = str(e)
        return False


ok = close(ticker="aapl", prices=[1.5, 2.5], position_id="AAPL-CSP-B", pos_index=2)
check("PG's close_trade accepts a position id at all", ok,
      SENT.get("error", ""))
if not ok:
    SENT["args"], SENT["stdin"] = [], ""
check("PG passes --position-id in argv",
      "--position-id" in SENT.get("args",[]) and "AAPL-CSP-B" in SENT.get("args",[]),
      str(SENT.get("args",[])))
check("PG sends NO ordinal line when it has an id — the race is removed",
      SENT.get("stdin","").splitlines() == ["1.5", "2.5", "y"],
      repr(SENT["stdin"]))
check("id wins over a stale ordinal when both are supplied",
      SENT.get("stdin", "").splitlines()[:1] == ["1.5"],
      repr(SENT.get("stdin", "")))

close(ticker="aapl", prices=[1.5, 2.5], pos_index=2)
check("legacy ordinal path unchanged when no id is available",
      "--position-id" not in SENT.get("args",[])
      and SENT.get("stdin","").splitlines() == ["2", "1.5", "2.5", "y"],
      repr(SENT["stdin"]))

close(ticker="aapl", prices=[1.5], pos_index=None)
check("single-position path unchanged",
      SENT.get("stdin","").splitlines() == ["1.5", "y"], repr(SENT["stdin"]))

# --- the wiring in between -------------------------------------------------- #
app_src = open(f"{PG}/app.py", encoding="utf-8").read()
check("app.py forwards pos_id from the request", 'position_id=payload.get("pos_id")' in app_src)
store_src = open(f"{PG}/engine_store.py", encoding="utf-8").read()
check("engine_store forwards position_id", "position_id=position_id" in store_src)
tpl = open(f"{PG}/templates/positions.html", encoding="utf-8").read()
check("the close form sends pos_id", "pos_id: r.pos_id" in tpl)

# --------------------------------------------------------------------------- #
print()
for line in PASS:
    print(f"  ok    {line}")
for line in FAIL:
    print(f"  FAIL  {line}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
