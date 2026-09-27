import json,statistics as st
D=json.load(open('lc/lc.json'))
seen=set(); U=[]
for d in sorted(D,key=lambda d:d['book']!='REAL'):
    k=(d['t'],d['opened'])
    if k in seen: continue
    seen.add(k); U.append(d)
for d in U: d['ex10']=(d['r10']-d['m10']) if d['r10'] is not None and d['m10'] is not None else None
C={(d['t'],d['opened']):d for d in D if d['status']=='CLOSED' and d['debit']}
def rep(name,key,bk):
    print(f"-- {name}  [unique entries: stock beat S&P over 10d | median excess]  [closed trades: won | median % of debit]")
    for lab,f in bk:
        g=[d for d in U if d.get(key) is not None and f(d[key])]
        e=[d['ex10'] for d in g if d['ex10'] is not None]
        cl=[C[(d['t'],d['opened'])] for d in g if (d['t'],d['opened']) in C]
        s1=f"n={len(e):2d} beat {sum(x>0 for x in e)/len(e):4.0%} med {st.median(e):+5.1%}" if e else "n= 0"
        s2=f"n={len(cl):2d} won {sum(x['pnl']>0 for x in cl)/len(cl):4.0%} med {st.median([x['pnl']/x['debit'] for x in cl]):+4.0%}" if cl else "n= 0"
        print(f"   {lab:22s} {s1:36s} | {s2}")
rep('price vs 50-day avg','vs50',[('<+3%',lambda v:v<3),('+3 to +7%',lambda v:3<=v<7),('+7%+',lambda v:v>=7)])
rep('price vs 52-week high','vs52',[('>5% below',lambda v:v< -5),('within 5%',lambda v:v>=-5)])
rep('RSI at entry','rsi',[('<55',lambda v:v<55),('55-65',lambda v:55<=v<65),('65+',lambda v:v>=65)])
rep('bias score','bias',[('<=1',lambda v:v<=1),('2',lambda v:v==2),('3+',lambda v:v>=3)])
rep('ADX (trend strength)','adx',[('<20',lambda v:v<20),('20-30',lambda v:20<=v<30),('30+',lambda v:v>=30)])
rep('IV / HV90','ivhv',[('<0.9',lambda v:v<.9),('0.9-1.1',lambda v:.9<=v<1.1),('1.1+',lambda v:v>=1.1)])
rep('1-yr stock volatility','hv252',[('<25',lambda v:v<25),('25-35',lambda v:25<=v<35),('35+',lambda v:v>=35)])
rep('entry delta','delta0',[('<0.60',lambda v:v<.6),('0.60-0.70',lambda v:.6<=v<.7),('0.70+',lambda v:v>=.7)])
rep('entry DTE','edte',[('<90',lambda v:v<90),('90-130',lambda v:90<=v<130),('130+',lambda v:v>=130)])
rep('passed LC screen','screen',[('yes',lambda v:v==1),('no',lambda v:v==0)])
rep('days to earnings','dte_earn',[('<30',lambda v:v<30),('30-60',lambda v:30<=v<60),('60+',lambda v:v>=60)])
rep('sector','sector',[(s,lambda v,s=s:v==s) for s in ('Technology','Healthcare','Financial Services','Industrials','Consumer Defensive','Consumer Cyclical','Energy','Communication Services')])
rep('book','book',[(s,lambda v,s=s:v==s) for s in ('REAL','PAPER')])
rep('entry month','opened',[(m,lambda v,m=m:v.startswith(m)) for m in ('2026-05','2026-06','2026-07','2026-08','2026-09')])
