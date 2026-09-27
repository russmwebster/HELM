import json,statistics as st,sys
D=[d for d in json.load(open('csp/csp.json')) if d['path'] and d['credit']]
def sim(d,rule):
    """walk GOOD marks in order; first firing mark closes at that mark's pnl; else actual realized."""
    peak=-1e18
    for m in d['path']:
        pnl=m['pnl']; peak=max(peak,pnl); cr=d['credit']
        if rule(d,m,pnl,peak,cr): return pnl,True
    return d['pnl'],False
R={
 'actual':lambda d,m,p,pk,cr:False,
 'target 30%':lambda d,m,p,pk,cr:p>=.30*cr,
 'target 40%':lambda d,m,p,pk,cr:p>=.40*cr,
 'stop 1x credit':lambda d,m,p,pk,cr:p<=-1.0*cr,
 'stop 1.5x':lambda d,m,p,pk,cr:p<=-1.5*cr,
 'stop 2x':lambda d,m,p,pk,cr:p<=-2.0*cr,
 'stop 3x':lambda d,m,p,pk,cr:p<=-3.0*cr,
 'strike breach':lambda d,m,p,pk,cr:m['spot'] is not None and d['strike'] and m['spot']<d['strike'],
 'within 2% of strike':lambda d,m,p,pk,cr:m['spot'] is not None and d['strike'] and m['spot']<d['strike']*1.02,
 'delta >= 0.50':lambda d,m,p,pk,cr:m['delta'] is not None and abs(m['delta'])>=.5,
 'giveback: peak>=20%, -15pts':lambda d,m,p,pk,cr:pk>=.2*cr and p<=pk-.15*cr,
 'giveback: peak>=20%, -25pts':lambda d,m,p,pk,cr:pk>=.2*cr and p<=pk-.25*cr,
 'giveback: peak>=30%, -20pts':lambda d,m,p,pk,cr:pk>=.3*cr and p<=pk-.20*cr,
 'giveback: peak>=30%, -30pts':lambda d,m,p,pk,cr:pk>=.3*cr and p<=pk-.30*cr,
 'giveback: to breakeven after >=25%':lambda d,m,p,pk,cr:pk>=.25*cr and p<=0,
}
sets={'exJune':[d for d in D if d['month']!='2026-06'],'old':[d for d in D if d['closed']<='2026-08-17' and d['month']!='2026-06'],'new':[d for d in D if d['closed']>'2026-08-17']}
book=sys.argv[1] if len(sys.argv)>1 else None
for k,g in sets.items():
    if book: g=[d for d in g if d['book']==book]
    print(f"\n==== {k} {book or 'BOTH'} n={len(g)}   (columns: fired | better/worse vs actual | winners | loss>2x credit | worst % | total $ | median % of credit)")
    for name,fn in R.items():
        res=[sim(d,fn) for d in g]
        v=[r[0] for r in res]; pct=[r[0]/d['credit'] for r,d in zip(res,g)]
        bet=sum(r[0]>d['pnl']+.5 for r,d in zip(res,g)); wor=sum(r[0]<d['pnl']-.5 for r,d in zip(res,g))
        print(f"  {name:36s} {sum(r[1] for r in res):3d} | {bet:3d}/{wor:3d} | {sum(x>0 for x in v):3d} ({sum(x>0 for x in v)/len(v):4.0%}) | {sum(p<-2 for p in pct):2d} | {min(pct):+6.0%} | {sum(v):+8.0f} | {st.median(pct):+5.0%}")
