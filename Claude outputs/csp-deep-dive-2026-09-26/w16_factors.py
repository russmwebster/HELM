import sys,os,statistics as st; sys.path.insert(0,'tools')
import w16_dte_sweep as w
c=w.connect(os.path.expanduser('~/helm_copy.db')); rows=w.universe(c,None)
def chk(pid,n):
    return c.execute("select * from checks where position_id=? and dte_now is not null and dte_now<=? and data_quality='GOOD' and pnl_unrealized is not null order by checked_at limit 1",(pid,n)).fetchone()
mc={r['as_of_date']:r for r in c.execute("select * from market_context")}
def mctx(d):
    ks=[k for k in mc if k<=d]; return mc[max(ks)] if ks else None
D=[]
for r in rows:
    a,b=chk(r['id'],28),chk(r['id'],21)
    if a is None or b is None: continue
    p=c.execute("select * from positions where id=?",(r['id'],)).fetchone()
    prem=abs(p['net_premium'] or 0) or None
    es=c.execute("select * from entry_snapshots where position_id=? order by snapshot_at limit 1",(r['id'],)).fetchone()
    ed=p['earnings_date']
    earn = None if not ed else (a['checked_at'][:10] < ed <= b['checked_at'][:10])
    m0,m1=mctx(a['checked_at'][:10]),mctx(b['checked_at'][:10])
    D.append(dict(t=r['ticker'],book=r['book'],strat=r['strategy'],month=r['entry_month'],
      chg=b['pnl_unrealized']-a['pnl_unrealized'], chgp=(b['pnl_unrealized']-a['pnl_unrealized'])/prem if prem else None,
      pnl28p=a['pnl_unrealized']/prem if prem else None, buf=a['buffer_pct'], d28=abs(a['delta']) if a['delta'] is not None else None,
      ivr28=a['iv_rank'], ivchg=a['iv_vs_entry'], spot=a['spot_pct_change'],
      ivr0=es['iv_rank'] if es else None, d0=abs(es['delta']) if es and es['delta'] is not None else None,
      atr=es['atr_strikes_otm'] if es else None, size=p['total_contracts'], maxloss=abs(p['max_loss']) if p['max_loss'] else None,
      earn=earn, vix28=m0['vix'] if m0 else None, trend=m0['index_trend'] if m0 else None,
      spxwk=(m1['spx_price']/m0['spx_price']-1) if m0 and m1 and m0['spx_price'] else None,
      edte=p['entry_dte']))
print('n with both marks:',len(D))
def rep(name,key,buckets):
    print(f'-- {name}')
    for lab,f in buckets:
        g=[d for d in D if d[key] is not None and f(d[key])]
        if not g: continue
        cp=[d['chgp'] for d in g if d['chgp'] is not None]
        print(f'   {lab:22s} n={len(g):3d} improved {sum(d["chg"]>0 for d in g):3d} ({sum(d["chg"]>0 for d in g)/len(g):4.0%}) median wk chg {st.median(cp) if cp else 0:+6.1%} of prem  sum ${sum(d["chg"] for d in g):+7.0f}  med ${st.median([d["chg"] for d in g]):+5.0f}')
    miss=sum(d[key] is None for d in D)
    if miss: print(f'   (missing {miss})')
rep('P&L at 28 DTE (% of premium)','pnl28p',[('losing >50%',lambda v:v<-.5),('losing 0-50%',lambda v:-.5<=v<0),('winning 0-40%',lambda v:0<=v<.4),('winning 40%+',lambda v:v>=.4)])
rep('Distance to short strike at 28 (buffer %)','buf',[('<2% (near/ITM)',lambda v:v<2),('2-5%',lambda v:2<=v<5),('5-10%',lambda v:5<=v<10),('10%+',lambda v:v>=10)])
rep('Short delta at 28','d28',[('<0.15',lambda v:v<.15),('0.15-0.30',lambda v:.15<=v<.3),('0.30-0.50',lambda v:.3<=v<.5),('0.50+',lambda v:v>=.5)])
rep('Stock move since entry at 28','spot',[('down >5%',lambda v:v<-5),('down 0-5%',lambda v:-5<=v<0),('up 0-5%',lambda v:0<=v<5),('up 5%+',lambda v:v>=5)])
rep('IV rank at 28','ivr28',[('<25',lambda v:v<25),('25-50',lambda v:25<=v<50),('50-75',lambda v:50<=v<75),('75+',lambda v:v>=75)])
rep('IV change since entry at 28 (pts)','ivchg',[('fell >3',lambda v:v<-3),('-3..+3',lambda v:-3<=v<=3),('rose >3',lambda v:v>3)])
rep('IV rank at entry','ivr0',[('<25',lambda v:v<25),('25-50',lambda v:25<=v<50),('50-75',lambda v:50<=v<75),('75+',lambda v:v>=75)])
rep('Short delta at entry','d0',[('<0.20',lambda v:v<.2),('0.20-0.25',lambda v:.2<=v<.25),('0.25-0.30',lambda v:.25<=v<.3),('0.30+',lambda v:v>=.3)])
rep('Strikes OTM in ATRs at entry','atr',[('<1',lambda v:v<1),('1-2',lambda v:1<=v<2),('2+',lambda v:v>=2)])
rep('Entry DTE','edte',[('<35',lambda v:v<35),('35-40',lambda v:35<=v<40),('40-45',lambda v:40<=v<=45),('>45',lambda v:v>45)])
rep('Contracts','size',[('1',lambda v:v==1),('2-3',lambda v:2<=v<=3),('4+',lambda v:v>=4)])
rep('Earnings inside the week','earn',[('yes',lambda v:v is True),('no',lambda v:v is False)])
rep('S&P over the week','spxwk',[('down >1%',lambda v:v<-.01),('flat ±1%',lambda v:-.01<=v<=.01),('up >1%',lambda v:v>.01)])
rep('VIX at 28','vix28',[('<16',lambda v:v<16),('16-18',lambda v:16<=v<18),('18+',lambda v:v>=18)])
rep('Index trend at 28','trend',[(t,lambda v,t=t:v==t) for t in ('UPTREND','PULLBACK','DOWNTREND','RECOVERY')])
rep('Strategy','strat',[(s,lambda v,s=s:v==s) for s in ('CSP','IRON_CONDOR')])
rep('Book','book',[(s,lambda v,s=s:v==s) for s in ('REAL','PAPER')])
rep('Entry month','month',[(s,lambda v,s=s:v==s) for s in ('2026-05','2026-06','2026-07','2026-08','2026-09')])
import json; json.dump(D,open(os.path.expanduser('~/w16_factors.json'),'w'))
