#!/bin/bash
# s153: break the new code four ways; each must make verify_put_fit.py fail. Control (unperturbed) LAST.
# Usage: bash _s153/perturb.sh N   (N = 1..4 a perturbation, 0 = control)
set -u; export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
H=$HOME/mnt/helm; P=/tmp/s153p; rm -rf $P; mkdir -p $P
rsync -a --exclude data --exclude archive --exclude '_s1*' --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude _records --exclude docs --exclude _to_delete $H/ $P/eng/
rsync -a --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude _to_delete $HOME/mnt/helm-pg/ $P/pg/
case "$1" in
 1) sed -i 's/return \[c for c in cands\[TOP_N:\] if c.get("size_n")\]\[:n\]/return [c for c in cands[TOP_N:]][:n]/' $P/eng/helm/puttool.py; echo "1: fitting ignores the sizing";;
 2) sed -i 's/return \[c for c in cands\[TOP_N:\] if c.get("size_n")\]\[:n\]/return [c for c in cands if c.get("size_n")][:n]/' $P/eng/helm/puttool.py; echo "2: fitting repeats top-5 names";;
 3) sed -i "s/{% if pu.top|rejectattr('size_n')|list %}/{% if true %}/" $P/pg/templates/today.html; echo "3: board shows the block even when the top 5 fits";;
 4) sed -i 's/for c in cands if not c.get("size_n"))/for c in cands if c.get("size_n"))/' $P/eng/helm/puttool.py; echo "4: declined count counts the wrong names";;
 0) echo "0: control, unperturbed";;
esac
diff -rq $H/helm $P/eng/helm 2>/dev/null | grep -v __pycache__ | grep -v bak- ; diff -q $HOME/mnt/helm-pg/templates/today.html $P/pg/templates/today.html
cd $H && timeout 170 python3 -B _s153/verify_put_fit.py $P/eng $P/pg 2>&1 | grep -E "FAIL|checks,"
