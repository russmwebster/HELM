import json,statistics as st
D=[d for d in json.load(open('ic/ic.json')) if d['path'] and d['credit']]
def sim(d,rule):
    peak=-1e18
    for m in d['path']:
        p=m['pnl']; peak=max(peak,p)
        if rule(d,m,p,peak,d['credit']): return p,True
    return d['pnl'],False
def breach(d,m):
    s=m['spot']; 
    if s is None: return False
    return (d['sp'] and s<d['sp']) or (d['sc'] and s>d['sc'])
R={'actual':lambda d,m,p,pk,cr:False,
 'close at 28 DTE':lambda d,m,p,pk,cr:m['dte'] is not None and m['dte']<=28,
 'close at 21 DTE':lambda d,m,p,pk,cr:m['dte'] is not None and m['dte']<=21,
 'close at 14 DTE':lambda d,m,p,pk,cr:m['dte'] is not None and m['dte']<=14,
 'stop 1x credit':lambda d,m,p,pk,cr:p<=-cr,
 'stop 2x credit':lambda d,m,p,pk,cr:p<=-2*cr,
 'stop 50% of max loss':lambda d,m,p,pk,cr:d['maxloss'] and p<=-.5*d['maxloss'],
 'short strike breached':lambda d,m,p,pk,cr:breach(d,m),
 'giveback: after +20%, -25pts':lambda d,m,p,pk,cr:pk>=.2*cr and p<=pk-.25*cr,
}
for S in ['IRON_CONDOR','BEAR_CALL_SPREAD']:
  for k,sel in [('exJune',lambda d:d['month']!='2026-06'),('old',lambda d:d['month']!='2026-06' and d['closed']<='2026-08-17'),('new',lambda d:d['closed']>'2026-08-17' and d['month']!='2026-06')]:
    g=[d for d in D if d['strat']==S and sel(d)]
    if not g: continue
    print(f"\n==== {S} {k} n={len(g)} (REAL {sum(d['book']=='REAL' for d in g)})   fired | better/worse | winners | lost>half maxloss | total $ | median % credit")
    for name,fn in R.items():
        res=[sim(d,fn) for d in g]; v=[r[0] for r in res]
        bet=sum(r[0]>d['pnl']+.5 for r,d in zip(res,g)); wor=sum(r[0]<d['pnl']-.5 for r,d in zip(res,g))
        big=sum(r[0]< -.5*d['maxloss'] for r,d in zip(res,g) if d['maxloss'])
        print(f"  {name:30s} {sum(r[1] for r in res):3d} | {bet:3d}/{wor:3d} | {sum(x>0 for x in v):3d} ({sum(x>0 for x in v)/len(v):4.0%}) | {big:2d} | {sum(v):+7.0f} | {st.median([r[0]/d['credit'] for r,d in zip(res,g)]):+5.0%}")
