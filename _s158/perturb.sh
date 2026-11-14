#!/bin/bash
# s158: break each part on purpose; verify_shares.py must fail. Control (unbroken) last.
# Usage: bash _s158/perturb.sh N   (1-5 = perturbation, 0 = control). Run with TMPDIR=/tmp PYTHONPATH=/tmp/pylib.
set -u
N=$1; H=$HOME/mnt/helm; P=$HOME/mnt/helm-pg; D=/tmp/s158p
rm -rf $D; mkdir -p $D
python3 -c "
import shutil, sys
ig = shutil.ignore_patterns('data', 'archive', '_s1*', '_to_delete', '*.bak-*', '__pycache__', '.git', 'docs', '_records', 'logs', 'Claude outputs')
shutil.copytree('$H', '$D/eng', ignore=ig)
shutil.copytree('$P', '$D/pg', ignore=shutil.ignore_patterns('*.bak-*', '__pycache__', '.git', '_to_delete'))"
E=$D/eng/helm
python3 - "$N" "$E" <<'PY'
import sys
n, e = sys.argv[1], sys.argv[2]
def sub(path, a, b):
    s = open(path).read(); assert s.count(a) >= 1, (path, a); open(path, "w").write(s.replace(a, b, 1)); print("perturbed:", path, "|", a.strip()[:60])
if n == "1": sub(e + "/bookview.py", "if sr and (not px or sr[1] > px[1]):", "if sr and (not px or sr[1] < px[1]):")
if n == "2": sub(e + "/bookview.py", "date.fromisoformat(r[\"as_of\"][:10])).days >= 1})", "date.fromisoformat(r[\"as_of\"][:10])).days >= 99})")
if n == "3": sub(e + "/cli/check_cmd.py", "    # attempted / journaled (the Book's retry reads those); never costs the snapshot.\n    _read_held_shares(args)\n",
                 "    # attempted / journaled (the Book's retry reads those); never costs the snapshot.\n")
if n == "4": sub(e + "/stockreads.py", "ORDER BY checked_at DESC LIMIT 1", "ORDER BY checked_at ASC LIMIT 1")
if n == "5": sub(e + "/cli/check_cmd.py", 'if "--paper" in args and "--all" not in args:\n        return\n    try:\n        from helm import stockreads',
                 'if False:\n        return\n    try:\n        from helm import stockreads')
if n == "0": print("control: no change")
PY
cd $H && python3 -B _s158/verify_shares.py $D/eng $D/pg > $D/out.txt 2>&1; rc=$?
grep -E "FAIL|checks," $D/out.txt | head -8
echo "perturbation $N: harness rc=$rc  (expect 1 for 1-5, 0 for control)"
