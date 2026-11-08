#!/bin/bash
# s153c: break "Deciding from here" four ways; each must make verify_put_card.py fail. Control LAST.
# Usage: bash _s153/perturb_card.sh N   (1..4 a perturbation, 0 = control)
set -u; export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
H=$HOME/mnt/helm; P=/tmp/s153cp; rm -rf $P; mkdir -p $P
rsync -a --exclude data --exclude archive --exclude '_s1*' --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude _records --exclude docs --exclude _to_delete $H/ $P/eng/
rsync -a --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude _to_delete $HOME/mnt/helm-pg/ $P/pg/
sed -i "s|^HELM_PYTHON = .*$|HELM_PYTHON = '$(command -v python3)'|" $P/eng/helm/config.py
case "$1" in
 1) sed -i 's/(max(mid - out\["intrinsic"\], 0.0) if/(max(mid, 0.0) if/' $P/eng/helm/decide.py; echo "1: time value counts the intrinsic value";;
 2) sed -i 's/if c.get("size_n") and c\["ticker"\] != t), None)/if c["ticker"] != t), None)/' $P/eng/helm/decide.py; echo "2: the switch ignores the limits";;
 3) sed -i 's/{% if card.decide and not card.decide.error %}<div class="readbox">/{% if false %}<div class="readbox">/' $P/pg/templates/thesis.html; echo "3: the old verdict stays";;
 4) sed -i 's/"collateral": strike \* 100 \* n,/"collateral": strike * 100,/' $P/eng/helm/decide.py; echo "4: the assignment cash ignores the contract count";;
 0) echo "0: control, unperturbed";;
esac
diff -q $H/helm/decide.py $P/eng/helm/decide.py; diff -q $HOME/mnt/helm-pg/templates/thesis.html $P/pg/templates/thesis.html; diff -q $HOME/mnt/helm-pg/engine_store.py $P/pg/engine_store.py
cd $H && timeout 170 python3 -B _s153/verify_put_card.py $P/eng $P/pg 2>&1 | grep -E "FAIL|checks,"
