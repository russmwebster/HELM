#!/bin/bash
# s150: each perturbation on a fresh copy of the new code; the harness must FAIL on each. Control (code on disk) last.
H=$HOME/mnt/helm; PG=$HOME/mnt/helm-pg; P=/tmp/s150p; N=${1:-all}
mk(){ rm -rf $P; mkdir -p $P; cp -rp /tmp/s150/eng-new $P/eng; cp -rp /tmp/s150/pg-new $P/pg; }   # the harness's own copies (run the control once first)
run(){ echo "== $1"; TMPDIR=/tmp timeout 170 python3 -B $H/_s150/verify_today_calls.py $P/eng $P/pg 2>&1 | grep -E "FAIL|checks," | head -6; }
case $N in
 rolls) mk; sed -i 's/if r.get("position_id") and A._roll_hint/if False and A._roll_hint/' $P/pg/engine_store.py; grep -c "if False and A._roll_hint" $P/pg/engine_store.py; run "roll boxes never chosen";;
 counts) mk; sed -i 's/row\["run_counts"\] = bool(run) and "already open" not in rej and (/row["run_counts"] = bool(run) or (/' $P/pg/engine_store.py; grep -c 'bool(run) or (' $P/pg/engine_store.py; run "a run read while the short was open counts";;
 day21) mk; sed -i 's/timedelta(days=A.TIME_EXIT_DTE)/timedelta(days=A.TIME_EXIT_DTE - 1)/' $P/pg/engine_store.py; grep -c "TIME_EXIT_DTE - 1" $P/pg/engine_store.py; run "21-day date off by one";;
 never) mk; sed -i 's/fetchone() is None$/fetchone() is None and False/' $P/eng/helm/advice.py; grep -c "is None and False" $P/eng/helm/advice.py; run "never-read wording off";;
 control) echo "== control (code on disk)"; TMPDIR=/tmp timeout 170 python3 -B $H/_s150/verify_today_calls.py 2>&1 | grep -E "FAIL|checks," | head -6;;
esac
