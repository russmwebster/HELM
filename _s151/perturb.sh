#!/bin/bash
# s151: break the change five ways, each must make verify_glance.py fail; run the control (unbroken) last.
# Usage: TMPDIR=/tmp bash _s151/perturb.sh N   (N = 1..5, or 0 for the control)
set -u; E=/tmp/s151/eng-new; P=/tmp/s151/pg-new; X=/tmp/s151p
rm -rf $X; mkdir -p $X; cp -rp $E $X/eng; cp -rp $P $X/pg
case "$1" in
 1) sed -i 's/c = (sk - sp) \/ sp \* 100.0/c = (lk - sp) \/ sp * 100.0/' $X/eng/helm/thesis.py ;;      # cushion from the wrong strike
 2) sed -i 's/timedelta(days=day21)).isoformat()/timedelta(days=day21 - 1)).isoformat()/' $X/eng/helm/thesis.py ;;  # 21 days off by one
 3) sed -i 's/(_lk + _fl) if _fl is not None else None/_fl/' $X/eng/helm/thesis.py ;;            # the floor without the long's strike
 4) sed -i 's/open_s\[0\]\["strike"\] if open_s else None,/None,/' $X/pg/engine_store.py ;;      # Today's ruler loses the short
 5) sed -i 's/{{ g_stand }}{{ g_next }}{{ g_dates }}    <\/div><\/details>/{{ g_stand }}{{ g_dates }}    <\/div><\/details>/' $X/pg/templates/thesis_diag.html ;;  # a row of words dropped
 0) echo "control: unbroken" ;;
esac
diff -rq $E $X/eng | grep -v pycache; diff -rq $P $X/pg | grep -v pycache
cd ~/mnt/helm && python3 -B _s151/verify_glance.py $X/eng $X/pg > $X/out.txt 2>&1; rc=$?
grep -E "FAIL|checks," $X/out.txt | head -8; echo "perturbation $1: exit $rc"
