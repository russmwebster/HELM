#!/bin/bash
# s166: test the GOOGL share-sale booking on fresh copies of the database. Usage: verify.sh SCRIPT [label]
set -u
export TMPDIR=/tmp PYTHONPATH=/tmp/pylib HELM_ROOT="$HOME/mnt/helm" HELM_DOWNLOADS="$HOME/mnt/Downloads" COLUMNS=220
SCRIPT="$1"; W=/tmp/s166/run.$$; mkdir -p $W
python3 -B - "$W" <<'PY'
import sqlite3,os,sys
src=os.path.expanduser('~/mnt/helm/data/helm.db'); c=sqlite3.connect('file:'+src+'?mode=ro',uri=True)
for n in ('a','b'): c.execute("VACUUM INTO '%s/%s.db'"%(sys.argv[1],n))
PY
dump(){ python3 -B - "$1" <<'PY'
import sqlite3,sys,hashlib
c=sqlite3.connect('file:%s?mode=ro'%sys.argv[1],uri=True)
for (t,) in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by 1"):
    cols=[r[1] for r in c.execute('pragma table_info("%s")'%t)]
    h=hashlib.sha256()
    n=0
    for r in c.execute('select * from "%s" order by 1,2'%t if len(cols)>1 else 'select * from "%s" order by 1'%t): h.update(repr(r).encode()); n+=1
    print(t,len(cols),n,h.hexdigest()[:12])
PY
}
echo "== 1 dry run"; python3 -B "$SCRIPT" --db $W/a.db; echo "rc=$?"
dump $W/a.db > $W/after_dry.txt; dump $W/b.db > $W/base.txt
echo "== 2 dry run wrote nothing: $(diff -q $W/base.txt $W/after_dry.txt >/dev/null && echo PASS || echo FAIL)"
echo "== 3 apply"; python3 -B "$SCRIPT" --db $W/a.db --apply; echo "rc=$?"
dump $W/a.db > $W/after.txt
echo "== 4 tables changed (expect exactly: broker_fills legs positions stock_positions):"; diff $W/base.txt $W/after.txt | grep '^>' | awk '{print $2}' | tr '\n' ' '; echo
echo "== 5 second apply must refuse"; python3 -B "$SCRIPT" --db $W/a.db --apply; echo "rc=$? (expect 1)"
dump $W/a.db > $W/after2.txt; echo "   unchanged by the refused run: $(diff -q $W/after.txt $W/after2.txt >/dev/null && echo PASS || echo FAIL)"
python3 -B - $W <<'PY'
import sqlite3,sys,os
sys.path.insert(0,os.path.expanduser('~/mnt/helm'))
from helm import sleeve as S, netpnl as N
a=sqlite3.connect('file:%s/a.db?mode=ro'%sys.argv[1],uri=True); b=sqlite3.connect('file:%s/b.db?mode=ro'%sys.argv[1],uri=True)
for c in (a,b): c.row_factory=sqlite3.Row
ra,rb=S.realized_since(a)[0],S.realized_since(b)[0]; fa,fb=N.fees_since(a,S.SLEEVE_START),N.fees_since(b,S.SLEEVE_START)
ua=sum(i['dollars'] for i in S.in_use_items(a)); ub=sum(i['dollars'] for i in S.in_use_items(b))
ok=lambda c,m: print(("PASS " if c else "FAIL ")+m)
ok(abs((ra-rb)-(-6975.0))<0.005,"sleeve gross realized moved by %.2f (expect -6975.00)"%(ra-rb))
ok(abs((fa-fb)-0.55)<0.005,"fees moved by %.2f (expect 0.55)"%(fa-fb))
ok(abs((ua-ub)-(-33500.0))<0.005,"in use moved by %.2f (expect -33500.00)"%(ua-ub))
l=a.execute("select * from legs where position_id='GOOGL-COVERED_CALL-20261123-CDB860' and option_type='STOCK'").fetchall()
ok(len(l)==1 and l[0]['contracts']==100 and l[0]['multiplier']==1 and l[0]['open_price']==335.0 and l[0]['close_price']==265.25 and l[0]['status']=='CLOSED' and l[0]['close_date']=='2026-11-25T16:00:00' and l[0]['open_date']=='2026-11-05T16:00:00',"one closed stock leg, 100 x 335.00 -> 265.25, 11-05 -> 11-25")
p=a.execute("select * from positions where id='GOOGL-COVERED_CALL-20261123-CDB860'").fetchone(); pb=b.execute("select * from positions where id='GOOGL-COVERED_CALL-20261123-CDB860'").fetchone()
ok(abs(p['realized_pnl']-(-6728.0))<0.005 and p['status']=='CLOSED' and p['exit_reason']==pb['exit_reason'] and p['closed_at']==pb['closed_at'] and p['entry_dte']==pb['entry_dte'],"position realized %.2f (expect -6728.00); status, exit reason, close date, entry days unchanged"%p['realized_pnl'])
ok(a.execute("select count(*) from stock_positions").fetchone()[0]==0,"HELM holds no shares")
f=a.execute("select * from broker_fills where note='SHARE_SALE'").fetchall()
ok(len(f)==1 and f[0]['symbol']=='GOOGL' and f[0]['qty']==-100 and f[0]['price']==265.25 and f[0]['fees']==0.55 and f[0]['amount']==26524.45 and f[0]['as_of']=='2026-11-25' and f[0]['outcome']=='BOOKED' and f[0]['leg_id']==l[0]['id'],"fill log line: -100 GOOGL at 265.25, fee 0.55, cash 26524.45, tied to the leg")
# only the expected rows differ in positions / legs
dp=[r['id'] for r in a.execute("select * from positions") if tuple(r)!=tuple(b.execute("select * from positions where id=?",(r['id'],)).fetchone() or ())]
ok(dp==['GOOGL-COVERED_CALL-20261123-CDB860'],"only that position's row differs: %s"%dp)
nb=b.execute("select count(*) from legs").fetchone()[0]; na=a.execute("select count(*) from legs").fetchone()[0]
same=all(tuple(r)==tuple(a.execute("select * from legs where id=?",(r['id'],)).fetchone()) for r in b.execute("select * from legs"))
ok(na==nb+1 and same,"legs: one added, every earlier leg identical")
PY
echo "== 6 HELM's own commands on the booked copy"
for c in "sleeve" "book" "advise" "fills --file $HOME/mnt/Downloads/Accounts_History-3.csv"; do HELM_DB=$W/a.db python3 -B "$HOME/mnt/helm/helm.py" $c > $W/cmd.txt 2>&1; echo "helm $(echo $c|cut -d' ' -f1): rc=$? lines=$(wc -l < $W/cmd.txt) traceback=$(grep -c Traceback $W/cmd.txt) GOOGL-mentions=$(grep -c GOOGL $W/cmd.txt)"; done
HELM_DB=$W/b.db python3 -B "$HOME/mnt/helm/helm.py" sleeve 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -iE "sleeve|in use|room|realized" | head -8 > $W/s_b.txt
HELM_DB=$W/a.db python3 -B "$HOME/mnt/helm/helm.py" sleeve 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -iE "sleeve|in use|room|realized" | head -8 > $W/s_a.txt
echo "-- helm sleeve before"; cat $W/s_b.txt; echo "-- after"; cat $W/s_a.txt
echo "W=$W"
