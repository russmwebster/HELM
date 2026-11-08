#!/bin/bash
# s154b: undo each half of the unread-card fix on copies; the harness must fail on each. Control LAST.
H=$HOME/mnt/helm; P=$HOME/mnt/helm-pg; W=/tmp/s154bp; export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
prep() { rm -rf $W; mkdir -p $W
  rsync -a --exclude data --exclude archive --exclude '_s1*' --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude docs --exclude _records --exclude _to_delete $H/ $W/eng/
  rsync -a --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude _to_delete $P/ $W/pg/
  sed -i "s|^HELM_PYTHON = .*$|HELM_PYTHON = '$(which python3)'|" $W/eng/helm/config.py; }
run() { echo "$1 -> $(timeout 175 python3 -B $H/_s154/verify_unread_card.py $W/eng $W/pg 2>&1 | tail -1)"; }
case "$1" in
1) prep; cp $H/helm/decide.py.bak-20261108-s154 $W/eng/helm/decide.py; run "1 decide.py fix undone";;
2) prep; cp $P/templates/thesis.html.bak-20261108-s154b $W/pg/templates/thesis.html; run "2 thesis.html fix undone";;
c) prep; run "control (unperturbed)";;
esac
