#!/bin/bash
# s152: each perturbation must make verify_retry.py fail; the unperturbed repo runs LAST and must pass.
# Run in the device VM from the repo: TMPDIR=/tmp HELM_ROOT=$HOME/mnt/helm PYTHONPATH=/tmp/pylib bash _s152/perturb.sh
R=$(pwd); run(){ python3 -B _s152/verify_retry.py "$1" 2>&1 | grep -E "^FAIL|failed$" | head -4; }
mk(){ rm -rf /tmp/s152p; mkdir -p /tmp/s152p; cp -r "$R/helm" "$R/helm.py" /tmp/s152p/; ln -s "$R/logs" /tmp/s152p/logs; mkdir /tmp/s152p/data; }
echo "== 1. no try is ever set aside";  mk; sed -i 's/                gone.add(j)/                pass/' /tmp/s152p/helm/cli/audit_cmd.py; run /tmp/s152p
echo "== 2. timed from its own start";   mk; sed -i 's/(r.get("first_start") if hasattr(r, "get") else None) or r\["started_at"\]/r["started_at"]/' /tmp/s152p/helm/cli/audit_cmd.py; run /tmp/s152p
echo "== 3. the snapshot writes no marker"; mk; sed -i 's/        if _rt and _rt\[0\] >= 2:$/        if False:/' /tmp/s152p/helm/cli/check_cmd.py; run /tmp/s152p
echo "== 4. advice ignores slot_at";     mk; sed -i 's/slot_of(slot_at or /slot_of(None or /' /tmp/s152p/helm/advice.py; run /tmp/s152p
echo "== 5. no age guard in the audit";  mk; sed -i 's/        if age < -60 or age > RETRY_MAX_AGE_MIN \* 60:\n            return None, None, notes//; s/^RETRY_MAX_AGE_MIN = 60 /RETRY_MAX_AGE_MIN = 9999 /' /tmp/s152p/helm/cli/audit_cmd.py; run /tmp/s152p
echo "== control (the repo)";             run "$R"
