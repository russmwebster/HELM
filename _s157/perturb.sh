#!/bin/bash
# s157 perturbations: each must make the self-test or the harness fail; the unperturbed control runs LAST and must pass.
cd "$HOME/mnt/helm"; export TMPDIR=/tmp PYTHONPATH=/tmp/pylib HELM_ROOT="$HOME/mnt/helm"
P=/tmp/s157p
run() { # name, python replacement (old, new)
  rm -rf $P; mkdir -p $P; cp -r helm helm.py $P/ 2>/dev/null; cp -r ../helm/helm/config.py $P/helm/ 2>/dev/null
  python3 - "$P/helm/advice.py" "$2" "$3" <<'PY'
import sys; p,o,n=sys.argv[1:]; s=open(p).read(); assert s.count(o)==1, o; open(p,"w").write(s.replace(o,n))
PY
  st=$(cd $P && HELM_ROOT=$P python3 -B helm.py advise --selftest 2>&1 | tail -1)
  h=$(python3 -B _s157/verify_coverage.py $P 2>&1 | tail -1)
  echo "$1 | selftest: $st | harness: $h"
}
run "1 slots judged before their window ends" "            if end > now:" "            if False:"
run "2 import booking time ignored" "                gone = bc         #" "                pass; bc         #"
run "3 window edge off by one (>=)" "            if end > now:" "            if end >= now:"
run "control (unperturbed)" "def coverage(conn, day=None, now=None):" "def coverage(conn, day=None, now=None):"
