#!/bin/bash
# s153b: break the Today health line three ways; each must make verify_health_line.py fail. Control LAST.
# Usage: bash _s153/perturb_health.sh N   (1..3 a perturbation, 0 = control)
set -u; export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
H=$HOME/mnt/helm; P=/tmp/s153hp; rm -rf $P; mkdir -p $P
rsync -a --exclude data --exclude archive --exclude '_s1*' --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude _records --exclude docs --exclude _to_delete $H/ $P/eng/
rsync -a --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude _to_delete $HOME/mnt/helm-pg/ $P/pg/
sed -i "s|^HELM_PYTHON = .*$|HELM_PYTHON = '$(command -v python3)'|" $P/eng/helm/config.py
case "$1" in
 1) sed -i 's/    if not todo:\n        return None//; s/^    if not todo:$/    if False:/' $P/eng/helm/advice.py; echo "1: routine WARNs count as a problem";;
 2) sed -i 's/    return max(days) if days else None/    return min(days) if days else None/' $P/eng/helm/advice.py; echo "2: the oldest report, not the newest";;
 3) sed -i 's/{% if v.health %}<div class="tdy-health">/{% if true %}<div class="tdy-health">/' $P/pg/templates/today.html; echo "3: the line shown on a clean day";;
 0) echo "0: control, unperturbed";;
esac
diff -q $H/helm/advice.py $P/eng/helm/advice.py; diff -q $HOME/mnt/helm-pg/templates/today.html $P/pg/templates/today.html
cd $H && timeout 170 python3 -B _s153/verify_health_line.py $P/eng $P/pg 2>&1 | grep -E "FAIL|checks,"
