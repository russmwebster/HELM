#!/bin/bash
# s157b: perturb the Held column on copies (never the Mac's tree); each must fail verify_held.py; control (0) LAST.
N="$1"; E=/tmp/s157h/pert-eng; P=/tmp/s157h/pert-pg
rm -rf $E $P; cp -r /tmp/s157h/eng-new $E; cp -r /tmp/s157h/pg-new $P
case "$N" in
 1) sed -i 's/out.append("%s × same put" % n)/out.append("%s × %s %g P" % (n, _md(exp), k))/' $E/helm/todayview.py ;;
 2) sed -i 's/names = \[t for t in {ticker} | {x for g in S.SAME_COMPANY if ticker in g for x in g}\]/names = [ticker]/' $E/helm/todayview.py ;;
 3) sed -i "s/<td class=\"t\">{{ c.held or '—' }}<\/td>/<td class=\"t\">—<\/td>/" $P/templates/today.html ;;
 4) sed -i 's/AND l.direction=.SHORT. AND p.strategy IN (.CSP.,.COVERED_CALL.)/AND p.strategy IN ('"'"'CSP'"'"','"'"'COVERED_CALL'"'"','"'"'PMCC'"'"')/' $E/helm/todayview.py ;;
esac
diff -q $E/helm/todayview.py /tmp/s157h/eng-new/helm/todayview.py >/dev/null && diff -q $P/templates/today.html /tmp/s157h/pg-new/templates/today.html >/dev/null && echo "code: unperturbed" || echo "code: perturbation $N"
cd $HOME/mnt/helm && TMPDIR=/tmp PYTHONPATH=/tmp/pylib timeout 175 python3 -B _s157b/verify_held.py $E $P 2>&1 | grep -E "FAIL|checks"
