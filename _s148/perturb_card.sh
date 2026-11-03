#!/bin/bash
# s148: each perturbation must make verify_pmcc_card.py fail; the unperturbed control runs last.
REPO="$(cd "$(dirname "$0")/.." && pwd)"; PGL="$REPO/../helm-pg"; E=/tmp/s148_pe; G=/tmp/s148_pg
mk() { rm -rf $E $G; mkdir -p $E $G; cp -r "$REPO/helm" $E/helm; cp "$REPO/helm.py" $E/; (cd "$PGL" && tar --exclude=.git -cf - .) | (cd $G && tar xf -); }
ed() { python3 - "$1" "$2" "$3" <<'PY'
import sys; p, a, b = sys.argv[1:4]; s = open(p).read(); assert s.count(a) == 1, a; open(p, "w").write(s.replace(a, b))
PY
}
run() { if timeout 170 python3 -B "$REPO/_s148/verify_pmcc_card.py" $E $G > /tmp/s148_pc.txt 2>&1; then echo "NOT CAUGHT: $1"; else echo "caught: $1 -- $(grep -m1 FAIL /tmp/s148_pc.txt)"; fi; }
case "$1" in
 1) mk; ed $E/helm/advice.py "short_dte is not None and short_dte <= TIME_EXIT_DTE, \"short\")" "short_dte is not None and short_dte < TIME_EXIT_DTE, \"short\")"; run "21 days read as under 21";;
 2) mk; ed $E/helm/thesis.py 'is_pmcc = str(pos.get("strategy") or "").upper() == "PMCC"' 'is_pmcc = False'; run "the card ignores PMCC";;
 3) mk; ed $G/templates/thesis_diag.html "{{ 'PMCC' if card.strategy == 'PMCC' else 'Diagonal' }}</h2>" "Diagonal</h2>"; run "title hard-coded";;
 c) if timeout 170 python3 -B "$REPO/_s148/verify_pmcc_card.py" > /tmp/s148_pc.txt 2>&1; then echo "control passes: $(tail -1 /tmp/s148_pc.txt)"; else echo "CONTROL FAILED"; fi;;
esac
