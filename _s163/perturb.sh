#!/bin/bash
# s163: break the change one way at a time; verify_s163.py must FAIL on each (1-8) and PASS on the control (0, run LAST).
# Needs /tmp/s163/old/{helm,helm-pg} (the trees before the change), /tmp/s163/new/{helm,helm-pg} (the changed trees)
# and /tmp/s163/base.db (a VACUUM INTO copy). Usage: bash _s163/perturb.sh N     (several can run side by side)
N="$1"; P=/tmp/s163p$N; rm -rf $P; mkdir -p $P; cp -a /tmp/s163/new/helm $P/eng; cp -a /tmp/s163/new/helm-pg $P/pg
E=$P/eng/helm; G=$P/pg
case "$N" in
  0) M="control: no change";;
  1) sed -i 's/d\["rich"\] += int(ivr >= IVR_MIN)/d["rich"] += int(ivr > IVR_MIN)/' $E/puttool.py; M="1: a name exactly at the bar is not counted rich";;
  2) sed -i 's/for d in (out, out\["funds" if g == "FUND" else "stocks"\]):/for d in (out, out["stocks" if g == "FUND" else "funds"]):/' $E/puttool.py; M="2: the stock and fund counts are swapped";;
  3) sed -i 's/"limits": limits, "premium_count": premium,/"limits": limits,/' $E/advice.py; M="3: advice.json does not carry the block";;
  4) sed -i 's/        out.append(dict(_row(conn, ch, best, spot, b, db), bar_met=0, note=None))/        out.append(dict(_row(conn, ch, best, spot, b, db), bar_met=0, note=None)); rejects.pop(t, None)/' $E/puttool.py; M="4: reading an index fund removes its reason from the list's rejects";;
  5) sed -i 's/return max(have, key=lambda x: x\["ann_return"\]) if have else None/return min(have, key=lambda x: x["ann_return"]) if have else None/' $E/puttool.py; M="5: the lowest-return index fund is shown";;
  6) sed -i 's/{% if pu.index %}<tr class="sub">/{% if false %}<tr class="sub">/' $G/templates/today.html; M="6: Today does not show the index line";;
  7) sed -i 's/        if not (why.startswith("IV rank ") and ", below " in why):/        if True:/' $E/puttool.py; M="7: a fund under the bar is not read";;
  8) sed -i 's/"index": (dict(row(ibest), iv_rank=ibest.get("iv_rank"), bar=P.IVR_MIN)/"index": (dict(row(ibest), iv_rank=None, bar=P.IVR_MIN)/' $E/todayview.py; M="8: the index line loses its IV rank";;
esac
D=$( (diff -r /tmp/s163/new/helm/helm $E -x __pycache__; diff -r /tmp/s163/new/helm-pg/templates $G/templates) | grep -c '^[<>]')
R=$(cd /tmp/s163 && S163W=/tmp/s163w$N S163PORT=$((9400 + N * 20)) TMPDIR=/tmp timeout 170 python3 -B "$(dirname "$0")/verify_s163.py" old/helm old/helm-pg $P/eng $P/pg 2>&1 | grep -E "^  FAIL|^verify_s163|Traceback|Error" | cut -c1-150 | head -5)
echo "[$M] changed lines: $D"; echo "$R"
