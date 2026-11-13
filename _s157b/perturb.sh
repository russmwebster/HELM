#!/bin/bash
# s157b: each perturbation must make verify_add_on.py fail; the unperturbed control runs LAST and must pass.
# Runs on a copy of the code at /tmp/code (never the Mac's working tree). Usage: perturb.sh N   (1-5, 0 = control)
set -u
LIVE="$1"; DL="$2"; N="$3"
rm -rf /tmp/code/helm/__pycache__ /tmp/code/helm/cli/__pycache__
cp -p "$HOME/mnt/helm/helm/fillimport.py" /tmp/code/helm/fillimport.py
F=/tmp/code/helm/fillimport.py
case "$N" in
 1) python3 - "$F" <<'P'
import sys; p=sys.argv[1]; s=open(p).read(); o='    if len(open_legs) != 1:\n        return None\n'
assert s.count(o)==1; open(p,'w').write(s.replace(o,'    return None\n'+o))
P
 ;;
 2) python3 - "$F" <<'P'
import sys; p=sys.argv[1]; s=open(p).read(); o='        if extra:            # s157b'
assert s.count(o)==1; open(p,'w').write(s.replace(o,'        extra = []\n'+o))
P
 ;;
 3) python3 - "$F" <<'P'
import sys; p=sys.argv[1]; s=open(p).read(); o='    new_price = np_new / (n * mult)\n'
assert s.count(o)==1; open(p,'w').write(s.replace(o,'    new_price = price\n'))
P
 ;;
 4) python3 - "$F" <<'P'
import sys; p=sys.argv[1]; s=open(p).read(); o='        if event != "OPEN" and any(l["id"] in added for l in cands):'
assert s.count(o)==1; open(p,'w').write(s.replace(o,'        if False:'))
P
 ;;
 5) python3 - "$F" <<'P'
import sys; p=sys.argv[1]; s=open(p).read(); o='        if free < 100 * q:'
assert s.count(o)==1; open(p,'w').write(s.replace(o,'        if False:'))
P
 ;;
 0) ;;
esac
cmp -s "$F" "$HOME/mnt/helm/helm/fillimport.py" && echo "code: unperturbed" || echo "code: perturbation $N"
cd /tmp/code && HELM_ROOT=/tmp/code PYTHONPATH=/tmp/pylib:/tmp/code TMPDIR=/tmp timeout 175 python3 -B _s157b/verify_add_on.py "$LIVE" "$DL" 2>&1 | grep -E "^FAIL|passed|failed|^code"
