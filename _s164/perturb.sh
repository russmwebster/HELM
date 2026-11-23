#!/bin/bash
# s164: break one piece of the change on a copy of the new trees and show the harness catches it (exit 1).
# Usage: bash perturb.sh N   (1-6 perturbations; 0 = the unperturbed control, run LAST, must pass)
set -u; S=/tmp/s164; P=$S/p; V="$(dirname "$0")/verify_s164.py"
rm -rf $P; mkdir -p $P; cp -r $S/eng-new $P/eng; cp -r $S/pg-new $P/pg
case "$1" in
 1) sed -i 's/^PREMIUM_HEADSUP_X = 2.0 /PREMIUM_HEADSUP_X = 3.0 /' $P/eng/helm/advice.py ;;          # the multiple
 2) sed -i 's/reached = mark is not None and mark >= line - 1e-9/reached = mark is not None and mark > line + 1e-9/' $P/eng/helm/advice.py ;;  # the edge
 3) sed -i 's/"state": "near" if reached else "next",/"state": "fired" if reached else "next",/' $P/eng/helm/advice.py ;;  # a rule instead of a heads-up
 4) sed -i "s/hot|rejectattr('key', 'in', BOOK + HEADS)/hot|rejectattr('key', 'in', BOOK)/" $P/pg/templates/put_card.html ;;  # hides next lines
 5) python3 -c "
p='$P/pg/templates/today.html'; s=open(p).read(); i=s.rfind('{% if r.heads %}'); j=s.find('{% endif %}', i)+len('{% endif %}')
open(p,'w').write(s[:i]+s[j:])" ;;                                                                    # Holding loses its mark
 6) python3 -c "
import re; p='$P/pg/templates/reference.html'; s=open(p).read(); open(p,'w').write(re.sub(r'<dt id=\"premium-2x\">.*?</dd>\n','',s,flags=re.S))" ;;
 0) ;;
esac
cd $S && TMPDIR=/tmp S164W=/tmp/s164pw S164PORT=$((9600 + 20 * $1)) python3 -B "$V" eng-old pg-old $P/eng $P/pg > $S/perturb-$1.log 2>&1; rc=$?
echo "perturbation $1: exit $rc  ($(grep -c '  FAIL ' $S/perturb-$1.log) failed checks)"; grep '  FAIL ' $S/perturb-$1.log | head -3
