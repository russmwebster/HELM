#!/bin/bash
# s155 step 1: each perturbation must make the advice self-test FAIL; the control (unperturbed) runs LAST and must PASS.
set -u
SRC="$HOME/mnt/helm"; W=/tmp/s155/pt; export HELM_ROOT="$SRC" TMPDIR=/tmp PYTHONPATH=/tmp/pylib
run() {  # $1 label, $2 python replacement (old|||new) or empty for control
  rm -rf $W; mkdir -p $W; cp -r $SRC/helm $W/helm
  if [ -n "$2" ]; then python3 - "$W/helm/advice.py" "$2" <<'PY'
import sys; p,spec=sys.argv[1],sys.argv[2]; a,b=spec.split("|||"); s=open(p).read(); assert s.count(a)>=1,a; open(p,"w").write(s.replace(a,b,1))
PY
  fi
  out=$(cd $W && python3 -B -m helm.advice --selftest 2>&1 | tail -1); echo "$1: $out"
}
run "1 rows' 21-day uses <" '    if dte is not None and dte <= TIME_EXIT_DTE:
        add("time_21"|||    if dte is not None and dte < 20:
        add("time_21"'
run "2 near at 95%" 'NEAR_FRAC = 0.90|||NEAR_FRAC = 0.95'
run "3 tested by price uses strict" '    by_price = (spot >= k) if is_cc else (spot <= k)|||    by_price = (spot >= k) if is_cc else (spot < k - 5)'
run "4 negative kept as a percentage" 'if kept >= 0
              else|||if True
              else'
run "control" ""
