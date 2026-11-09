#!/bin/bash
# s156: each perturbation must make verify_closed.py fail; the unperturbed control runs LAST and must pass.
export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
H=$HOME/mnt/helm; P=$HOME/mnt/helm-pg; W=/tmp/s156s
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
  if python3 -B $H/_s156/verify_closed.py $W/eng $W/pg > $W.log 2>&1; then r=PASS; else r=FAIL; fi
  echo "$1: $r ($(grep -c '  FAIL' $W.log) failed checks) $(grep '  FAIL' $W.log | head -2 | cut -c1-110)"
}
case "$1" in
1) run "rules at the close from the first reading" eng:helm/decide.py '"checked_at<=? AND current_price IS NOT NULL ORDER BY checked_at DESC LIMIT 1"' '"checked_at<=? AND current_price IS NOT NULL ORDER BY checked_at ASC LIMIT 1"';;
2) run "exit cash with the wrong sign" eng:helm/decide.py '"cash": (-(cp or 0.0) * 100 * n)' '"cash": ((cp or 0.0) * 100 * n)';;
3) run "gross shown as the result" pg:templates/put_closed.html '{{ usd(R.net, True) }}' '{{ usd(R.gross, True) }}';;
4) run "the chart keeps its sentences" eng:helm/decide.py 'T.close_svg(tr, plain=True)' 'T.close_svg(tr)';;
5) run "held days off by one" eng:helm/decide.py '"held": (date.fromisoformat(closed) - date.fromisoformat(opened)).days' '"held": (date.fromisoformat(closed) - date.fromisoformat(opened)).days + 1';;
6) run "closed puts not routed to the new card" pg:app.py '    if cz and not cz.get("error"):' '    if False:';;
7) run "exit price of the stock from the first check" eng:helm/decide.py '"spot_price IS NOT NULL AND checked_at<=? ORDER BY checked_at DESC LIMIT 1"' '"spot_price IS NOT NULL AND checked_at<=? ORDER BY checked_at ASC LIMIT 1"';;
c) run "control (unperturbed)";;
esac
