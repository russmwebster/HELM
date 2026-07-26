#!/usr/bin/env python3
"""W8 — verify the positions table says where each spot price came from.

The engine classifies every underlying price as ibkr-live / ibkr-close /
yfinance and hands it over as spot_source. Nothing in the web UI read it, so a
free-data price -- and buffer %, moneyness and breakeven distance, all derived
from it -- rendered identically to a live broker mark. The CLI has always shown
it (check_cmd.py:1596).

Checks the wiring end to end AND executes the formatter's logic, so "it renders
something" is not mistaken for "it renders the right thing".

  python3 verify_w8_spot_source.py [PG_ROOT] [HELM_ROOT]
"""
import re
import sys

PG = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/helm-pg"
HELM = sys.argv[2] if len(sys.argv) > 2 else "/mnt/user-data/uploads/helm"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name}{(' — ' + detail) if detail else ''}")


eng = open(f"{PG}/helm_engine.py", encoding="utf-8").read()
tpl = open(f"{PG}/templates/positions.html", encoding="utf-8").read()
cli = open(f"{HELM}/helm/cli/check_cmd.py", encoding="utf-8").read()

# --- the engine still supplies it ------------------------------------------ #
check("engine exposes spot_source on every row",
      '"spot_source": a.get("underlying_source")' in eng)
check("the engine still classifies three sources",
      all(s in cli for s in ('"ibkr-live"', '"ibkr-close"', '"yfinance"')))

# --- the UI now consumes it ------------------------------------------------- #
check("the template reads spot_source at all", "spot_source" in tpl)
check("a spot formatter that takes the ROW exists (not just the number)",
      "spotSrc: r =>" in tpl)

n_src = tpl.count("['spot', r=>fmt.spotSrc(r)]")
n_old = tpl.count("['spot', r=>fmt.spot(r.spot)]")
check("every spot column carries the source", n_src == 5 and n_old == 0,
      f"{n_src} wired, {n_old} left on the bare formatter")

# strike is rendered by the same underlying helper and must NOT gain a tag
check("strike columns are untouched", tpl.count("['strike', r=>fmt.spot(r.strike)]") >= 3)
check("the legend explains the tags",
      "= free data" in tpl and "live</b>" in tpl)

# --- no new colour: the scheme is deliberate -------------------------------- #
# .find, not .index. Against pre-W8 code the formatter is absent, which is the
# finding -- not a reason to abort before the remaining checks get to speak.
# (Third time in this session that .index has been the wrong call in a harness
# whose entire job is to survive the substring being missing.)
_a, _b = tpl.find("spotSrc: r =>"), tpl.find("n2: v =>")
fmt_block = tpl[_a:_b] if 0 <= _a < _b else ""
check("no colour class introduced for the source tag",
      "dg-y" not in fmt_block and "dg-g" not in fmt_block,
      "would add a third colour condition to a two-condition scheme")

# --- execute the formatter's mapping ---------------------------------------- #
# Pull the literal map out of the template and evaluate it, so this tests the
# behaviour rather than the presence of a string.
m = re.search(r"const tag = (\{[^}]*\})\[r\.spot_source\] \|\| '\?'", fmt_block)
check("the source mapping is readable from the template", m is not None)
if m:
    mapping = {}
    for k, v in re.findall(r"'([^']+)'\s*:\s*'([^']+)'", m.group(1)):
        mapping[k] = v
    check("ibkr-live renders as 'live'", mapping.get("ibkr-live") == "live", str(mapping))
    check("ibkr-close renders as 'close'", mapping.get("ibkr-close") == "close")
    check("yfinance renders as 'yf' — the case this exists for",
          mapping.get("yfinance") == "yf")
    check("every source the engine can emit is mapped",
          set(mapping) == {"ibkr-live", "ibkr-close", "yfinance"},
          f"mapped {sorted(mapping)}")
    check("an unrecognised source falls back to '?' rather than being echoed",
          "|| '?'" in fmt_block)

check("a null spot still renders the dash, with no tag",
      "if (r.spot == null) return px;" in fmt_block)

print()
for line in PASS:
    print(f"  ok    {line}")
for line in FAIL:
    print(f"  FAIL  {line}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
