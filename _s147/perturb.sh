#!/bin/bash
# s147: each perturbation must make verify_pmcc.py fail; the unperturbed control runs LAST and must pass.
REPO="$HOME/mnt/helm"; DB="$REPO/data/helm.db"; export HELM_ROOT="$REPO" TMPDIR=/tmp
run() {  # name file old new
  P=/tmp/s147/pert_$1; rm -rf $P; mkdir -p $P; cp -r $REPO/helm $REPO/helm.py $P/
  python3 - "$P/$2" "$3" "$4" <<'PY'
import sys; p,o,n=sys.argv[1:]; s=open(p).read(); assert s.count(o)==1,(p,o); open(p,'w').write(s.replace(o,n))
PY
  out=$(python3 -B $REPO/_s147/verify_pmcc.py "$DB" "$P" 2>&1)
  if echo "$out" | grep -q "^all passed"; then echo "NOT CAUGHT  $1"; else echo "caught      $1: $(echo "$out" | grep FAIL | head -2 | cut -c1-110 | tr '\n' '|')"; fi
}
run width_at_mid  helm/pmcctool.py 'ok_ask, floor_ask, _ = width(lg, sh["strike"], lg["ask"])' 'ok_ask, floor_ask, _ = width(lg, sh["strike"], lg["mid"])'
run rank_by_yield helm/pmcctool.py 'return (-(rc if rc is not None else 1e9), -b["ann_return"], b["ticker"])' 'return (-b["ann_return"], b["ticker"])'
run no_downtrend  helm/pmcctool.py 'flags.append("downtrend")' 'pass'
run two_per_co    helm/pmcctool.py '        if k in firsts:' '        if False:'
run open_pmcc_ok  helm/pmcctool.py '        if k in held:' '        if False:'
run no_range      helm/cli/open_cmd.py 'if strike_range is not None:' 'if False:'
run no_entry_trend helm/fillopens.py 'lab += "; trend at entry: " + record_entry_trend(pid, tk, as_of)' 'pass'
out=$(python3 -B $REPO/_s147/verify_pmcc.py "$DB" 2>&1); echo "control (last): $(echo "$out" | tail -1)"
