#!/bin/bash
# s159: break the change one way at a time; verify_market_cols.py must FAIL on each (1-6) and PASS on the control (0, run LAST).
# Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib bash _s159/perturb.sh N
set -e
N="$1"; P=/tmp/s159p; rm -rf $P; mkdir -p $P
cp -rp $HOME/mnt/helm/helm $P/helm-src; mkdir -p $P/eng; cp -p $HOME/mnt/helm/*.py $P/eng/ 2>/dev/null || true
mv $P/helm-src $P/eng/helm
rsync -a --exclude '*.bak-*' --exclude __pycache__ --exclude .git $HOME/mnt/helm-pg/ $P/pg/ 2>/dev/null || cp -rp $HOME/mnt/helm-pg $P/pg
E=$P/eng/helm; G=$P/pg
case "$N" in
  0) echo "control: no change";;
  1) sed -i 's/mkt = market_cell(conn, t, -abs(c.get("delta")/mkt = market_cell(conn, t, abs(c.get("delta")/' $E/todayview.py; echo "1: a covered call adds instead of subtracting";;
  2) sed -i 's/return float(delta_sh) \* float(spot) \* float(beta)/return float(delta_sh) * float(spot) * 1.0/' $E/bookview.py; echo "2: the multiple is not applied";;
  3) sed -i '0,/n = c.get("size_n") or 1/s//n = c.get("size_n") or 0/' $E/todayview.py; echo "3: a put sized at none counts zero contracts, not one";;
  4) sed -i 's/ - abs(c.get("short_delta") or 0.0)) \* 100 \* n/ - 0.0) * 100 * n/' $E/todayview.py; echo "4: a PMCC counts the long only";;
  5) sed -i "s/('amber' if m.state == 'near' else '')/('' if m.state == 'near' else '')/" $G/templates/today.html; echo "5: near the limit is not amber";;
  6) sed -i 's/book=v.get("figures"))     # s159/book=None)     # s159/' $G/engine_store.py; echo "6: the board does not pass the book";;
esac
diff -r $HOME/mnt/helm/helm $E -x __pycache__ -x '*.bak-*' | head -4 || true
diff $HOME/mnt/helm-pg/templates/today.html $G/templates/today.html | head -2 || true
diff $HOME/mnt/helm-pg/engine_store.py $G/engine_store.py | head -2 || true
cd $HOME/mnt/helm && timeout 170 python3 -B _s159/verify_market_cols.py $P/eng $P/pg 2>&1 | grep -E "FAIL|PASS|checks" | head -8
