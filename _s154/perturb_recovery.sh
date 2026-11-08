#!/bin/bash
# s154c: break the recovery build on copies; the harness must fail on each. Control (unperturbed) LAST.
H=$HOME/mnt/helm; P=$HOME/mnt/helm-pg; W=/tmp/s154cp; export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
prep() { rm -rf $W; mkdir -p $W
  rsync -a --exclude data --exclude archive --exclude '_s1*' --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude docs --exclude _records --exclude _to_delete $H/ $W/eng/
  mkdir -p $W/eng/_s122 && cp -r $H/_s122/px $W/eng/_s122/
  rsync -a --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude _to_delete $P/ $W/pg/
  sed -i "s|^HELM_PYTHON = .*$|HELM_PYTHON = '$(which python3)'|" $W/eng/helm/config.py; }
ed() { python3 -c "import sys;p='$1';s=open(p).read();o=$2;n=$3;assert s.count(o)==1,'anchor';open(p,'w').write(s.replace(o,n))" || echo EDIT-FAILED; }
run() { echo "$1 -> $(timeout 175 python3 -B $H/_s154/verify_recovery.py $W/eng $W/pg 2>&1 | tail -1)"; }
case "$1" in
1) prep; ed $W/eng/helm/decide.py '"i90, iy = H.index(90), H.index(365)"' '"i90, iy = H.index(60), H.index(180)"'; run "1 card reads the wrong horizons";;
2) prep; ed $W/eng/helm/decide.py '"\"stale\": day != today.isoformat()"' '"\"stale\": False"'; run "2 an old run not labelled";;
3) prep; ed $W/eng/helm/recovery.py '"    fresh = reading_day is None or"' '"    fresh = True or reading_day is None or"'; run "3 stale closes used as if fresh";;
4) prep; ed $W/eng/helm/cli/recovery_cmd.py '"            RC.record(conn, out, n, note)"' '"            pass"'; run "4 run not recorded";;
c) prep; run "control (unperturbed)";;
esac
