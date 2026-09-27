import json,statistics as st
D=json.load(open('ic/ic.json'))
for d in D: d['pct']=d['pnl']/d['credit'] if d['credit'] else None; d['pml']=d['pnl']/d['maxloss'] if d['maxloss'] else None
def line(g):
    if not g: return 'n=0'
    w=sum(d['pnl']>0 for d in g); big=sum(d['pml'] is not None and d['pml']<-.5 for d in g)
    return f"n={len(g):3d} win {w:3d} ({w/len(g):4.0%}) med%cr {st.median([d['pct'] for d in g]):+5.0%} med%maxloss {st.median([d['pml'] for d in g if d['pml'] is not None]):+5.0%} lost>half-maxloss {big:2d} $ {sum(d['pnl'] for d in g):+7.0f}"
for S in ['IRON_CONDOR','BEAR_CALL_SPREAD']:
    E=[d for d in D if d['strat']==S]
    sets={'ALL':E,'exJune':[d for d in E if d['month']!='2026-06'],'old':[d for d in E if d['month']!='2026-06' and d['closed']<='2026-08-17'],'new':[d for d in E if d['closed']>'2026-08-17']}
    print(f"\n######## {S}")
    for k,g in sets.items():
        for b in ['REAL','PAPER']: print(f"  {k:6s} {b:5s}",line([d for d in g if d['book']==b]))
    print('  -- exit reason (exJune)')
    for r in sorted({str(d['reason']) for d in sets['exJune']}):
        for b in ['REAL','PAPER']:
            g=[d for d in sets['exJune'] if str(d['reason'])==r and d['book']==b]
            if g: print(f"    {r:14s} {b:5s}",line(g))
    def fac(name,key,bk):
        print(f"  == {name}   [exJune | old | new]")
        for lab,f in bk:
            row=[]
            for k in ['exJune','old','new']:
                g=[d for d in sets[k] if d.get(key) is not None and f(d[key])]
                w=sum(d['pnl']>0 for d in g)
                row.append(f"n={len(g):3d} win {w/len(g) if g else 0:4.0%} $ {sum(d['pnl'] for d in g):+6.0f}")
            print(f"    {lab:12s} | "+" | ".join(row))
    fac('entry delta (short)','delta0',[('<0.15',lambda v:v<.15),('0.15-0.20',lambda v:.15<=v<.2),('0.20+',lambda v:v>=.2)])
    fac('IV rank at entry','ivr0',[('<50',lambda v:v<50),('50-70',lambda v:50<=v<70),('70+',lambda v:v>=70)])
    fac('IV at entry','iv0',[('<30%',lambda v:v<.3),('30-45%',lambda v:.3<=v<.45),('45%+',lambda v:v>=.45)])
    fac('credit / width','cw',[('<0.25',lambda v:v<.25),('0.25-0.33',lambda v:.25<=v<.33),('0.33+',lambda v:v>=.33)])
    fac('entry DTE','edte',[('<35',lambda v:v<35),('35-40',lambda v:35<=v<40),('40+',lambda v:v>=40)])
    fac('bid-ask spread %','spread_pct',[('<10%',lambda v:v<10),('10%+',lambda v:v>=10)])
    fac('VIX at entry','vix0',[('<15',lambda v:v<15),('15-17',lambda v:15<=v<17),('17+',lambda v:v>=17)])
    fac('trend at entry','trend0',[(t,lambda v,t=t:v==t) for t in ('UPTREND','PULLBACK')])
    fac('beta','beta',[('<0.8',lambda v:v<.8),('0.8-1.2',lambda v:.8<=v<1.2),('1.2+',lambda v:v>=1.2)])
    fac('sector','sector',[(s,lambda v,s=s:v==s) for s in ('Technology','Energy','Basic Materials','Healthcare','Financial Services','Consumer Defensive','Consumer Cyclical','Industrials')])
