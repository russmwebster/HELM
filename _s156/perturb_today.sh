#!/bin/bash
# s156: each perturbation must make verify_today.py fail; the unperturbed control runs LAST and must pass.
export TMPDIR=/tmp PYTHONPATH=/tmp/pylib
H=$HOME/mnt/helm; P=$HOME/mnt/helm-pg; W=/tmp/s156q
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
  if python3 -B $H/_s156/verify_today.py $W/eng $W/pg > $W.log 2>&1; then r=PASS; else r=FAIL; fi
  echo "$1: $r ($(grep -c '  FAIL' $W.log) failed checks) $(grep '  FAIL' $W.log | head -2 | cut -c1-110)"
}
case "$1" in
1) run "close at 99 a contract" eng:helm/todayview.py '-nums["mark"] * 100 * (short["contracts"] or 0)' '-nums["mark"] * 99 * (short["contracts"] or 0)';;
2) run "a roll from an earlier day not dated" eng:helm/todayview.py 'if rl["run_at"][:10] < today.isoformat():' 'if False:';;
3) run "HELM-held shares left off" eng:helm/todayview.py '            shares.append(row)' '            pass';;
4) run "passing book rules listed" eng:helm/todayview.py 'if r["state"] in ("fired", "near")]' 'if r["state"] in ("fired", "near", "pass")]';;
5) run "put cushion measured the wrong way" eng:helm/todayview.py '(sp - k) / sp * 100.0 if p["strategy"] == "CSP"' '(k - sp) / sp * 100.0 if p["strategy"] == "CSP"';;
6) run "declined count counts the fitting" eng:helm/todayview.py '"n_declined": len([c for c in cands if not c.get("size_n")])' '"n_declined": len([c for c in cands if c.get("size_n")])';;
7) run "PMCC close at the bid/ask" eng:helm/todayview.py 'row["close"] = pm["num"]["close_mid"]' 'row["close"] = pm["num"]["close_ba"]';;
c) run "control (unperturbed)";;
esac
