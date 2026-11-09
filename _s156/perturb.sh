#!/bin/bash
# s156: each perturbation must make verify_pmcc_card.py fail; the unperturbed control runs LAST and must pass.
export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
H=$HOME/mnt/helm; P=$HOME/mnt/helm-pg; W=/tmp/s156p
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
  if python3 -B $H/_s156/verify_pmcc_card.py $W/eng $W/pg > $W.log 2>&1; then r=PASS; else r=FAIL; fi
  echo "$1: $r ($(grep -c '  FAIL' $W.log) failed checks) $(grep '  FAIL' $W.log | head -2 | cut -c1-110)"
}
case "$1" in
1) run "21-day line from 20 days" eng:helm/advice.py 'minus(short["expiration"], TIME_EXIT_DTE)' 'minus(short["expiration"], TIME_EXIT_DTE - 1)';;
2) run "shorts net ignores closed shorts" eng:helm/decide.py 'net = sum((o or 0)' 'net = 0 * sum((o or 0)';;
3) run "rent cover from theta" eng:helm/decide.py 'out["rent_cover"] = PT.rent_cover(' 'out["rent_cover"] = (lambda *a: 2.7)(';;
4) run "PMCC not routed to the new card" pg:app.py 'if pm and not pm.get("error"):' 'if False:';;
5) run "PMCC rows not attached" eng:helm/advice.py '    if is_pmcc:     # s156' '    if False:     # s156';;
6) run "calls run never counts" eng:helm/decide.py 'counts = "already open" not in rej and' 'counts = False and';;
7) run "close at bid/ask uses the mid" eng:helm/decide.py 'lb, sa = long.get("bid")' 'lb, sa = long.get("mid")';;
c) run "control (unperturbed)";;
esac
