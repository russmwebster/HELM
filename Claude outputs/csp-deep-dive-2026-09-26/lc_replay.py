import json,statistics as st
from datetime import datetime as DT
D=[d for d in json.load(open('lc/lc.json')) if d['status']=='CLOSED' and d['path'] and d['debit']]
def sim(d,rule):
    peak=-1e18; t0=DT.fromisoformat(d['path'][0]['t'])
    for m in d['path']:
        p=m['pnl']; peak=max(peak,p); days=(DT.fromisoformat(m['t'])-t0).days
        if rule(d,m,p,peak,d['debit'],days): return p,True
    return d['pnl'],False
GB=lambda band,arm=None:(lambda d,m,p,pk,db,days: (arm is None or pk>=arm*db) and p<=max(pk-band*db,-.5*db))
R={'actual':lambda *a:False,
 'v3 give-back 20 (current paper)':GB(.20),
 'give-back 15':GB(.15),'give-back 25':GB(.25),'give-back 30':GB(.30),'give-back 40':GB(.40),
 'give-back 20, armed at +20%':GB(.20,.20),
 'give-back 25, armed at +30%':GB(.25,.30),
 'stop -30%':lambda d,m,p,pk,db,days:p<=-.3*db,
 'stop -40%':lambda d,m,p,pk,db,days:p<=-.4*db,
 'stop -50% only':lambda d,m,p,pk,db,days:p<=-.5*db,
 'take profit +25%':lambda d,m,p,pk,db,days:p>=.25*db,
 'take profit +50%':lambda d,m,p,pk,db,days:p>=.5*db,
 'time stop: day 10 and losing':lambda d,m,p,pk,db,days:days>=10 and p<0,
 'time stop: day 10 and below -10%':lambda d,m,p,pk,db,days:days>=10 and p<-.1*db,
}
for lab,sel in [('REAL',lambda d:d['book']=='REAL'),('REAL after 25 Jul',lambda d:d['book']=='REAL' and d['post_shift']),('PAPER',lambda d:d['book']=='PAPER')]:
    g=[d for d in D if sel(d)]
    print(f"\n==== {lab} n={len(g)}   fired | better/worse | won | total $ | median % of debit | total as % of debit")
    for name,fn in R.items():
        res=[sim(d,fn) for d in g]; v=[r[0] for r in res]
        bet=sum(r[0]>d['pnl']+.5 for r,d in zip(res,g)); wor=sum(r[0]<d['pnl']-.5 for r,d in zip(res,g))
        print(f"  {name:34s} {sum(r[1] for r in res):3d} | {bet:2d}/{wor:2d} | {sum(x>0 for x in v):2d} | {sum(v):+8.0f} | {st.median([r[0]/d['debit'] for r,d in zip(res,g)]):+5.0%} | {sum(v)/sum(d['debit'] for d in g):+5.1%}")
