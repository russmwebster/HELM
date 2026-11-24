#!/bin/bash
# s165: break the change one way at a time; verify_s165.py must FAIL on each (1-9) and PASS on the control (0, run LAST).
# Needs /tmp/s165/old (the engine before), /tmp/s165/new (after patch_s165.py), /tmp/s165/pg, /tmp/s165/base.db.
# Usage: bash _s165/perturb.sh N
HERE=$(cd "$(dirname "$0")" && pwd); N="$1"; P=/tmp/s165p$N; rm -rf $P; mkdir -p $P; cp -a /tmp/s165/new $P/eng; E=$P/eng/helm
case "$N" in
  0) M="control: no change";;
  1) sed -i 's/    if s\["gap_pct"\] is None or s\["gap_pct"\] >= GAP_MAX_PCT:/    if s["gap_pct"] is None or s["gap_pct"] > GAP_MAX_PCT + 0.5:/' $E/spreadtool.py; M="1: a market a little over 10% passes";;
  2) sed -i 's/    below = \[c for c in puts if c\["expiration"\] == short\["expiration"\] and c\["strike"\]/    below = [c for c in puts if c["strike"]/' $E/spreadtool.py; M="2: the bought put may be on another expiry";;
  3) sed -i 's/    n, binding, note = S.size(STRATEGY, t, s\["max_loss"\], db=db, b=b, ceiling=CEILING)/    n, binding, note = S.size(STRATEGY, t, s["short"]["strike"] * 100.0, db=db, b=b, ceiling=CEILING)/' $E/spreadtool.py; M="3: sized by the stock if assigned, not the most it can lose";;
  4) sed -i 's/            if not (why.startswith("IV rank ") and ", below " in why):/            if True:/' $E/spreadtool.py; M="4: an index fund under the bar is not read";;
  5) sed -i 's/"long_bid": lg\["bid"\], /"long_bid": lg["mid"], /' $E/spreadtool.py; M="5: the bought leg's bid is not recorded as read";;
  6) sed -i 's/        if k in firsts:/        if False:/' $E/spreadtool.py; M="6: two spreads on one company";;
  7) sed -i 's/    return sorted(rows, key=lambda r: (-r\["ann_return"\], r\["ticker"\]))/    return sorted(rows, key=lambda r: (r["ann_return"], r["ticker"]))/' $E/spreadtool.py; M="7: ranked lowest return first";;
  8) sed -i 's/    target = short\["strike"\] - width_pct \* spot/    target = short["strike"] - width_pct * short["strike"]/' $E/spreadtool.py; M="8: the width taken from the strike, not the stock's price";;
  9) sed -i 's/    what = {"CSP": "secures", "BULL_PUT_SPREAD": "risks"}.get((strategy or "").upper(), "costs")   # s165/    what = {"CSP": "secures", "BULL_PUT_SPREAD": "risks", "PMCC": "risks"}.get((strategy or "").upper(), "costs")   # s165/' $E/sleeve.py; M="9: the sizing wording changes for another strategy";;
  10) sed -i 's/_usd(c\["stock_if_assigned"\] \* c\["size_n"\]) if c.get("size_n") else "—"/_usd(c["stock_if_assigned"]) if c.get("size_n") else "—"/' $P/eng/helm/cli/spreads_cmd.py; M="10: If all assigned shows one contract";;
esac
D=$(diff -r /tmp/s165/new $P/eng -x __pycache__ | grep -c '^[<>]')
R=$(cd /tmp/s165 && S165W=/tmp/s165w$N S165PORT=$((9600 + N * 20)) TMPDIR=/tmp timeout 175 python3 -B "$HERE/verify_s165.py" old $P/eng pg 2>&1 | grep -E "^  FAIL|^verify_s165|Traceback|Error" | cut -c1-140 | head -4)
echo "[$M] changed lines: $D"; echo "$R"
rm -rf /tmp/s165w$N $P
