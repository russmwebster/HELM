#!/bin/bash
# s154: break each part of the change on copies; the harness must fail on each. Control (unperturbed) LAST.
H=$HOME/mnt/helm; P=$HOME/mnt/helm-pg; W=/tmp/s154p
export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
run() {  # $1 label, $2 file (eng:|pg:), $3 python edit
  rm -rf $W; mkdir -p $W
  rsync -a --exclude data --exclude archive --exclude '_s1*' --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude docs --exclude _records --exclude _to_delete $H/ $W/eng/
  rsync -a --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude _to_delete $P/ $W/pg/
  sed -i "s|^HELM_PYTHON = .*$|HELM_PYTHON = '$(which python3)'|" $W/eng/helm/config.py
  if [ -n "$3" ]; then python3 -c "$3" || { echo "$1: EDIT FAILED"; return; }; fi
  out=$(timeout 175 python3 -B $H/_s154/verify_order.py $W/eng $W/pg 2>&1 | tail -1)
  echo "$1 -> $out"
}
ed() { echo "import sys;p='$1';s=open(p).read();o=$2;n=$3;assert s.count(o)==1;open(p,'w').write(s.replace(o,n))"; }
case "$1" in
1) run "1 cards walk reversed" "" "$(ed $W/eng/helm/advice.py '"return sorted(keys, key=keys.get)"' '"return sorted(keys, key=keys.get, reverse=True)"')";;
2) run "2 Today not reordered" "" "$(ed $W/pg/engine_store.py '"        recs = advice.in_order(recs, advice.position_keys(account_id=acct))\n"' '""')";;
3) run "3 Details table not reordered" "" "$(ed $W/pg/engine_store.py '"        v[\"positions\"] = advice.in_order"' '"        v[\"_x\"] = advice.in_order"')";;
4) run "4 resting orders not reordered" "" "$(ed $W/pg/engine_store.py '"        v[\"orders\"] = sorted("' '"        v[\"_y\"] = sorted("')";;
5) run "5 counter says on Today" "" "$(ed $W/pg/templates/thesis_diag.html '"of {{ nav.n }} open positions"' '"of {{ nav.n }} on Today"')";;
c) run "control (unperturbed)" "" "";;
esac
