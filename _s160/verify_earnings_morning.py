import sys, sqlite3, os
sys.path.insert(0, sys.argv[1]); sys.dont_write_bytecode=True
import helm.earnings as E
print("loaded", E.__file__)
db=sys.argv[2]
def dump(c): return {t:[tuple(r) for r in c.execute("select * from %s order by 1"%t)] for t in [r[0] for r in c.execute("select name from sqlite_master where type='table'")]}
c=sqlite3.connect(db); c.row_factory=sqlite3.Row
c.execute("update watchlist set last_fundamentals_at='2026-11-01T09:35:00' where ticker not in (select ticker from approved_funds)")
c.execute("update watchlist set next_earnings=NULL,last_fundamentals_at=NULL,active=1 where ticker in (select ticker from approved_funds)"); c.commit()
pre=dump(c)
calls=[]
FAKE={"DELL":"2026-11-24","NVDA":"2026-11-17"}
def fetch(t):
    calls.append(t)
    if t=="AVGO": return None
    return FAKE.get(t,"2027-02-01")
ok=bad=0
def check(cond,msg):
    global ok,bad
    if cond: ok+=1
    else: bad+=1; print("FAIL",msg)
held=E.held_names(c)
check("DELL" in held and "GOOGL" in held and "SPY" not in held, "held names %s"%held)
s=E.refresh_morning(c, fetch=fetch)
print(E.morning_line(s))
funds={r[0] for r in c.execute("select ticker from approved_funds")}
check(not (set(calls)&funds), "funds fetched")
check(set(held)<=set(calls), "every held name read")
check(len([t for t in calls if t not in held])==35, "batch cap %d"%len([t for t in calls if t not in held]))
check(c.execute("select next_earnings from watchlist where ticker='DELL'").fetchone()[0]=="2026-11-24","DELL watchlist")
check(c.execute("select earnings_date from positions where ticker='DELL' and status='OPEN'").fetchone()[0]=="2026-11-24","DELL position")
pv=pre['positions'];  # AVGO failed -> unchanged
check(c.execute("select earnings_date from positions where ticker='AVGO' and status='OPEN'").fetchone()[0]=="2026-12-09","AVGO kept on failure")
check("AVGO" in s["held_failed"],"AVGO reported")
post=dump(c)
changed=[t for t in post if post[t]!=pre.get(t)]
check(sorted(changed)==["positions","watchlist"], "tables changed %s"%changed)
# only the date columns moved
def cols(t): return [r[1] for r in c.execute("pragma table_info(%s)"%t)]
for t,allowed in (("watchlist",{"next_earnings","last_fundamentals_at"}),("positions",{"earnings_date"})):
    cs=cols(t); a=dict((r[0],r) for r in pre[t]); b=dict((r[0],r) for r in post[t])
    check(set(a)==set(b),"%s rows"%t)
    moved=set()
    for k in a:
        for i,cn in enumerate(cs):
            if a[k][i]!=b[k][i]: moved.add(cn)
    check(moved<=allowed,"%s columns moved %s"%(t,moved))
closed_pre=[r for r in pre['positions'] if r[cols('positions').index('status')]!='OPEN']
closed_post=[r for r in post['positions'] if r[cols('positions').index('status')]!='OPEN']
check(closed_pre==closed_post,"closed positions untouched")
# second run next day: batch moves on, fresh names cached
calls.clear(); s2=E.refresh_morning(c, fetch=fetch)
check(s2["cached"]>=s["fetched"],"second run caches the first batch (%s)"%s2)
print("ok",ok,"fail",bad)
