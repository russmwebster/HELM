#!/bin/bash
# s155 step 2: each perturbation must make verify_put_card.py FAIL; the unperturbed control runs LAST and must pass.
set -u
export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
E="$HOME/mnt/helm"; P="$HOME/mnt/helm-pg"; W=/tmp/s155p
pert() {  # $1 label  $2 tree(eng|pg)  $3 file  $4 old  $5 new
  rm -rf $W; mkdir -p $W
  rsync -a --exclude data --exclude archive --exclude '_s1*' --exclude '*.bak-*' --exclude __pycache__ --exclude .git --exclude _records "$E/" $W/eng/
  rsync -a --exclude '*.bak-*' --exclude __pycache__ --exclude .git "$P/" $W/pg/
  # helm/config.py hard-codes the Mac's interpreter (s153): point it at this one, as the harness's own copies do
  sed -i "s|^HELM_PYTHON = .*$|HELM_PYTHON = '$(which python3)'|" $W/eng/helm/config.py
  if [ -n "$2" ]; then python3 - "$W/$2/$3" "$4" "$5" <<'PY'
import sys; p,a,b=sys.argv[1:4]; s=open(p).read(); assert s.count(a)>=1,a; open(p,"w").write(s.replace(a,b,1))
PY
  fi
  r=$(timeout 175 python3 -B "$E/_s155/verify_put_card.py" $W/eng $W/pg 2>&1 | tail -1); echo "$1: $r"
}
pert "1 route never uses the new card" pg app.py 'if dz and not dz.get("error"):' 'if False:'
pert "2 mid shows the ask" pg templates/put_card.html '{{ usd(-ch.close_mid if ch.close_mid is not none else none) }}' '{{ usd(-ch.close_ask if ch.close_ask is not none else none) }}'
pert "3 book rule hides the next lines" pg templates/put_card.html "{% set own_hot = hot|rejectattr('key', 'in', BOOK)|list %}" "{% set own_hot = hot %}"
pert "4 days left from the reading" pg templates/put_card.html '<span><b>Days left</b>{{ dz.dte }}</span>' '<span><b>Days left</b>{{ card.dte|int }}</span>'
pert "5 near at 95%" eng helm/advice.py 'NEAR_FRAC = 0.90' 'NEAR_FRAC = 0.95'
pert "control" "" "" "" ""
