#!/bin/bash
# s148: each perturbation must make verify_strikewindow.py fail; the unperturbed control runs last.
# Usage: TMPDIR=/tmp bash _s148/perturb.sh COPY.db
REPO="$(cd "$(dirname "$0")/.." && pwd)"; DB="$1"; P=/tmp/s148_pert
run() {  # name file python-replacement
  rm -rf "$P"; mkdir -p "$P"; cp -r "$REPO/helm" "$P/helm"
  python3 - "$P/helm/$2" "$3" "$4" <<'PY'
import sys; p, a, b = sys.argv[1:4]; s = open(p).read(); assert s.count(a) == 1, a; open(p, "w").write(s.replace(a, b))
PY
  if CODE="$P" python3 -B "$REPO/_s148/verify_strikewindow.py" "$DB" > /tmp/s148_out.txt 2>&1; then
    echo "NOT CAUGHT: $1"; else echo "caught: $1 -- $(grep -m1 FAIL /tmp/s148_out.txt)"; fi
}
run "put tool ignores the range" puttool.py "spread_threshold=NET_SPREAD, strike_range=rng)" "spread_threshold=NET_SPREAD)"
run "call tool ignores the range" calltool.py "spread_threshold=P.NET_SPREAD, strike_range=rng)" "spread_threshold=P.NET_SPREAD)"
run "roll range drops the current strike" rolltool.py "rng = (min(lo, float(strike)), max(hi, float(strike)))" "rng = (lo, hi)"
run "put z sign flipped" strikewindow.py "return -n if right == \"CALL\" else n" "return -n"
run "IV read in percent" strikewindow.py "return (float(r[0]) / 100.0) if r and r[0] else None" "return float(r[0]) if r and r[0] else None"
rm -rf "$P"
if python3 -B "$REPO/_s148/verify_strikewindow.py" "$DB" > /tmp/s148_out.txt 2>&1; then echo "control passes: $(tail -1 /tmp/s148_out.txt)"; else echo "CONTROL FAILED"; fi
