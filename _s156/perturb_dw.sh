#!/bin/bash
# s156: each perturbation must make verify_details_watchlist.py fail; the unperturbed control runs LAST and must pass.
export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
H=$HOME/mnt/helm; P=$HOME/mnt/helm-pg; W=/tmp/s156r
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
  if python3 -B $H/_s156/verify_details_watchlist.py $W/eng $W/pg > $W.log 2>&1; then r=PASS; else r=FAIL; fi
  echo "$1: $r ($(grep -c '  FAIL' $W.log) failed checks) $(grep '  FAIL' $W.log | head -2 | cut -c1-110)"
}
case "$1" in
1) run "no decimal near 90%" eng:helm/boardview.py '    if (x < 90 <= r) or (x < 100 <= r) or (x > 100 >= r and x != 100):' '    if False:';;
2) run "near from 85% on Details" eng:helm/boardview.py '"state": r["state"]}' '"state": "near" if used >= 0.85 * cap else r["state"]}';;
3) run "past report dates kept as next" eng:helm/boardview.py 'nxt = ne if (ne and ne >= today.isoformat()) else None' 'nxt = ne';;
4) run "shares not counted as held" eng:helm/boardview.py 'held.setdefault(r["ticker"], []).append("shares")' 'pass';;
5) run "Greeks table shows unweighted delta" pg:templates/book.html '{{ usd(r.bw_delta, True) }}{% if r.beta_assumed %}' '{{ usd(r.usd_delta, True) }}{% if r.beta_assumed %}';;
6) run "live watchlist not routed to the new page" pg:app.py '    if REAL and not firstrun:' '    if False:';;
7) run "funds not offered" eng:helm/boardview.py '"offered": g in A.PASSING' '"offered": g in ("A", "B", "C")';;
c) run "control (unperturbed)";;
esac
