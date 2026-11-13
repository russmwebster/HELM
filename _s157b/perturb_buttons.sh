#!/bin/bash
# s157c: perturb copies (never the Mac's trees); each must fail its harness; control (0) LAST runs both.
N="$1"; E=/tmp/s157p/eng; P=/tmp/s157p/pg; mkdir -p /tmp/s157p
rm -rf $E $P; mkdir -p $E $P
(cd $HOME/mnt/helm && tar --exclude=./data --exclude=./archive --exclude=./.git --exclude=./_s122/px --exclude='./_s1[0-4]*' -cf - .) | tar -xf - -C $E; mkdir -p $E/data
(cd $HOME/mnt/helm-pg && tar --exclude=./.git -cf - .) | tar -xf - -C $P
H=runs
case "$N" in
 1) python3 - $P/helm_engine.py <<'X'
import sys; p=sys.argv[1]; s=open(p).read(); o='''        r = {"ok": False, "output": "could not run helm pmcc: %s" % exc}
    finally:
        _chain_release()'''; assert s.count(o)==1; open(p,'w').write(s.replace(o,'''        r = {"ok": False, "output": "could not run helm pmcc: %s" % exc}'''))
X
 ;;
 2) python3 - $P/helm_engine.py <<'X'
import sys; p=sys.argv[1]; s=open(p).read(); o='''    w = _in_window()
    if w:
        return {"ok": False, "refused": True,
                "output": "It's inside the %s snapshot window -- a run would compete with "
                          "the snapshot for IBKR. Try after it." % w}'''; assert s.count(o)==1; open(p,'w').write(s.replace(o,'    w = None'))
X
 ;;
 3) python3 - $P/helm_engine.py <<'X'
import sys; p=sys.argv[1]; s=open(p).read(); o='''    if _PMCC_JOB["running"]:'''; assert s.count(o)==1; open(p,'w').write(s.replace(o,'    if False:'))
X
 ;;
 4) H=pages; python3 - $E/helm/todayview.py <<'X'
import sys; p=sys.argv[1]; s=open(p).read(); o='''                to_price.append(p["ticker"])\n'''; assert s.count(o)==1; open(p,'w').write(s.replace(o,''))
X
 ;;
 5) H=pages; python3 - $E/helm/todayview.py <<'X'
import sys; p=sys.argv[1]; s=open(p).read(); o='''"width_room": c["width_room"]'''; assert s.count(o)==1; open(p,'w').write(s.replace(o,'"width_room": c["width_floor"]'))
X
 ;;
 6) H=pages; python3 - $P/templates/reference.html <<'X'
import sys,re; p=sys.argv[1]; s=open(p).read(); n=re.sub(r'\n<dt id="trend">.*?</dd>','',s,flags=re.S); assert n!=s; open(p,'w').write(n)
X
 ;;
 0) H=both ;;
esac
echo "perturbation $N -> $H"
cd $HOME/mnt/helm
if [ "$H" = runs ] || [ "$H" = both ]; then TMPDIR=/tmp PYTHONPATH=/tmp/pylib timeout 170 python3 -B _s157b/verify_buttons_runs.py $E $P 2>&1 | grep -E "FAIL|checks|^engine"; fi
if [ "$H" = pages ]; then TMPDIR=/tmp PYTHONPATH=/tmp/pylib timeout 170 python3 -B _s157b/verify_buttons_pages.py $E $P 2>&1 | grep -E "FAIL|checks|^new engine"; fi
