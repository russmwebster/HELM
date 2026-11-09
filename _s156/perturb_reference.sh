#!/bin/bash
# s156: each perturbation must make verify_reference.py fail; the unperturbed control runs LAST and must pass.
export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
H=$HOME/mnt/helm; P=$HOME/mnt/helm-pg; W=/tmp/s156u
run() {  # name, file (eng:|pg:), python replace old, new
  rm -rf $W; mkdir -p $W
  python3 - "$H" "$P" "$W" <<'PY'
import shutil, sys, re
H, P, W = sys.argv[1:4]
for s, d in ((H, W + "/eng"), (P, W + "/pg")):
    shutil.copytree(s, d, ignore=shutil.ignore_patterns("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records"))
cfg = W + "/eng/helm/config.py"; c = open(cfg).read()
open(cfg, "w").write(re.sub(r"^HELM_PYTHON = .*$", "HELM_PYTHON = %r" % sys.executable, c, flags=re.M))
PY
  if [ -n "$2" ]; then
    python3 - "$W" "$2" "$3" "$4" <<'PY'
import sys
W, f, a, b = sys.argv[1:5]
side, rel = f.split(":", 1); path = W + ("/eng/" if side == "eng" else "/pg/") + rel
s = open(path).read(); assert s.count(a) >= 1, "perturbation text not found: " + a
open(path, "w").write(s.replace(a, b, 1))
PY
  fi
  if python3 -B $H/_s156/verify_reference.py $W/eng $W/pg > $W.log 2>&1; then r=PASS; else r=FAIL; fi
  echo "$1: $r ($(grep -c '  FAIL' $W.log) failed checks) $(grep '  FAIL' $W.log | head -2 | cut -c1-110)"
}
case "$1" in
1) run "a ? pointing at no entry" pg:templates/pmcc_card.html 'href="/reference#rent-cover"' 'href="/reference#rent-covers"';;
2) run "a constant typed into the page" pg:templates/reference.html '<dd>Near: {{ k.near }} of a limit' '<dd>Near: 85% of a limit';;
3) run "a constant not read from the engine" eng:helm/boardview.py '"near": pct(A.NEAR_FRAC)' '"near": pct(0.85)';;
4) run "no Reference link on Today" pg:templates/today.html '  <a href="/reference">Reference</a>' '';;
5) run "the reference page without its constants" pg:app.py '        K = boardview.reference_constants()' '        K = {}';;
6) run "a ? edit that changes a label" pg:templates/put_card.html '<span><b>Break-even' '<span><b>Break even';;
c) run "control (unperturbed)";;
esac
