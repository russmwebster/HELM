import json,statistics as st
D=json.load(open('csp/csp.json'))
for d in D: d['pct']=d['pnl']/d['credit'] if d['credit'] else None
def line(g):
    if not g: return 'n=0'
    p=[d['pct'] for d in g if d['pct'] is not None]; w=sum(d['pnl']>0 for d in g); big=sum(d['pct']is not None and d['pct']<-1 for d in g)
    return f"n={len(g):3d} win {w:3d} ({w/len(g):4.0%}) med% {st.median(p):+5.0%} big(loss>credit) {big:2d} ({big/len(g):4.0%}) $ {sum(d['pnl'] for d in g):+7.0f}"
sets={'ALL':D,'exJune':[d for d in D if d['month']!='2026-06'],'old<=8/17':[d for d in D if d['closed']<='2026-08-17'],'new>8/17':[d for d in D if d['closed']>'2026-08-17']}
print('== overview');
for k,g in sets.items():
    for b in ['REAL','PAPER']: print(f"  {k:10s} {b:5s}",line([d for d in g if d['book']==b]))
print('\n== exit reason (exJune)')
for r in sorted({str(d['reason']) for d in D}):
    for b in ['REAL','PAPER']:
        g=[d for d in sets['exJune'] if str(d['reason'])==r and d['book']==b]
        if g: print(f"  {r:14s} {b:5s}",line(g))
def fac(name,key,bk):
    print(f"\n== {name}   [exJune | old | new]")
    for lab,f in bk:
        row=[]
        for k in ['exJune','old<=8/17','new>8/17']:
            g=[d for d in sets[k] if d.get(key) is not None and f(d[key])]
            w=sum(d['pnl']>0 for d in g); big=sum(d['pct']<-1 for d in g if d['pct'] is not None)
            row.append(f"n={len(g):3d} win {w/len(g) if g else 0:4.0%} big {big:2d} $ {sum(d['pnl'] for d in g):+6.0f}")
        print(f"  {lab:14s} | "+" | ".join(row))
    miss=sum(d.get(key) is None for d in sets['exJune']); 
    if miss: print(f"  (missing in exJune: {miss})")
fac('entry delta','delta0',[('<0.20',lambda v:v<.2),('0.20-0.25',lambda v:.2<=v<.25),('0.25-0.30',lambda v:.25<=v<.3),('0.30+',lambda v:v>=.3)])
fac('IV rank at entry','ivr0',[('<50',lambda v:v<50),('50-65',lambda v:50<=v<65),('65-80',lambda v:65<=v<80),('80+',lambda v:v>=80)])
fac('IV level at entry','iv0',[('<30%',lambda v:v<.3),('30-45%',lambda v:.3<=v<.45),('45-60%',lambda v:.45<=v<.6),('60%+',lambda v:v>=.6)])
fac('IV / HV30 at entry','ivhv',[('<1.0',lambda v:v<1),('1.0-1.3',lambda v:1<=v<1.3),('1.3+',lambda v:v>=1.3)])
fac('strike % below spot','otm_pct',[('<5%',lambda v:v<5),('5-8%',lambda v:5<=v<8),('8-12%',lambda v:8<=v<12),('12%+',lambda v:v>=12)])
fac('entry DTE','edte',[('<35',lambda v:v<35),('35-40',lambda v:35<=v<40),('40-45',lambda v:40<=v<=45),('>45',lambda v:v>45)])
fac('credit % of collateral','cy',[('<1.5%',lambda v:v<1.5),('1.5-2.5%',lambda v:1.5<=v<2.5),('2.5-4%',lambda v:2.5<=v<4),('4%+',lambda v:v>=4)])
fac('VIX at entry','vix0',[('<15',lambda v:v<15),('15-17',lambda v:15<=v<17),('17+',lambda v:v>=17)])
fac('market trend at entry','trend0',[(t,lambda v,t=t:v==t) for t in ('UPTREND','PULLBACK','DOWNTREND')])
fac('sector','sector',[(s,lambda v,s=s:v==s) for s in ('Technology','Energy','Basic Materials','Consumer Cyclical','Consumer Defensive','Healthcare','Financial Services','Industrials','Communication Services','Utilities')])
fac('beta','beta',[('<0.8',lambda v:v<.8),('0.8-1.2',lambda v:.8<=v<1.2),('1.2-1.6',lambda v:1.2<=v<1.6),('1.6+',lambda v:v>=1.6)])
fac('size one-sigma $','sigma',[('<3k',lambda v:v<3000),('3-5k',lambda v:3000<=v<5000),('5-8k',lambda v:5000<=v<8000),('8k+',lambda v:v>=8000)])
fac('bid-ask spread % at entry','spread_pct',[('<5%',lambda v:v<5),('5-10%',lambda v:5<=v<10),('10%+',lambda v:v>=10)])
