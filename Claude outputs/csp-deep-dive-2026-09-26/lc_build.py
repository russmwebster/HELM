# Long call dataset (read-only). Paths trimmed to closed_at. Forward stock returns from daily scan spot (signals) vs SPX (market_context).
import sqlite3, json, bisect
c=sqlite3.connect('helm_copy.db'); c.row_factory=sqlite3.Row
spx=sorted((r['as_of_date'],r['spx_price']) for r in c.execute("select as_of_date,spx_price from market_context where spx_price is not null"))
px={}
for r in c.execute("select ticker,substr(generated_at,1,10) d,spot_price from signals where spot_price is not null order by generated_at"):
    px.setdefault(r['ticker'],{})[r['d']]=r['spot_price']
px={t:sorted(v.items()) for t,v in px.items()}
def fwd(series,d0,k):
    ds=[x[0] for x in series]; i=bisect.bisect_left(ds,d0)
    if i>=len(series) or i+k>=len(series): return None
    return series[i+k][1]/series[i][1]-1
def lastsig(t,d):
    r=c.execute("select * from signals where ticker=? and generated_at<=? order by generated_at desc limit 1",(t,d)).fetchone()
    return dict(r) if r else {}
sec={r['ticker']:r['sector'] for r in c.execute("select ticker,sector from watchlist")}
out=[]
for p in c.execute("select * from positions where strategy='LONG_CALL' and coalesce(exit_reason,'')!='VOIDED'"):
    leg=c.execute("select * from legs where position_id=? order by id limit 1",(p['id'],)).fetchone()
    es=c.execute("select * from entry_snapshots where position_id=? order by snapshot_at limit 1",(p['id'],)).fetchone(); es=dict(es) if es else {}
    s=lastsig(p['ticker'],p['opened_at'])
    path=[dict(t=r['checked_at'][:16],dte=r['dte_now'],pnl=r['pnl_unrealized'],spot=r['spot_price'])
          for r in c.execute("select * from checks where position_id=? and data_quality='GOOD' and pnl_unrealized is not null order by checked_at",(p['id'],))
          if not p['closed_at'] or r['checked_at'][:16]<=p['closed_at'][:16]]
    d0=p['opened_at'][:10]; ser=px.get(p['ticker'],[])
    R={}
    for k in (5,10,20):
        a=fwd(ser,d0,k); b=fwd(spx,d0,k); R[k]=(a,b)
    out.append(dict(id=p['id'],t=p['ticker'],book=p['book'],status=p['status'],opened=d0,closed=(p['closed_at'] or '')[:10],reason=p['exit_reason'],
        pnl=p['realized_pnl'] if p['status']=='CLOSED' else (path[-1]['pnl'] if path else None),debit=abs(p['net_premium'] or 0),n=leg['contracts'] if leg else 1,
        edte=p['entry_dte'] or es.get('dte'),delta0=abs(es['delta']) if es.get('delta') is not None else (leg['entry_delta'] if leg else None),
        iv0=es.get('iv_current'),ivr0=es.get('iv_rank'),spread_pct=es.get('bid_ask_spread_pct'),extr=es.get('extrinsic_ratio'),
        bias=s.get('auto_bias_score'),rsi=s.get('rsi_14'),adx=s.get('adx'),vs50=s.get('price_vs_sma50'),vs200=s.get('price_vs_sma200'),vs52=s.get('price_vs_52wk_pct'),
        ivhv=s.get('iv_hv90_ratio_xearn') or s.get('iv_hv90_ratio'),hv252=s.get('hv_252'),screen=s.get('lc_screen_pass'),dte_earn=s.get('days_to_earnings'),
        sector=sec.get(p['ticker']),post_shift=d0>='2026-07-25',
        r5=R[5][0],m5=R[5][1],r10=R[10][0],m10=R[10][1],r20=R[20][0],m20=R[20][1],path=path))
json.dump(out,open('lc/lc.json','w'))
from collections import Counter
print(len(out),Counter((o['book'],o['status']) for o in out))
print('r10 known',sum(o['r10'] is not None for o in out),'r20',sum(o['r20'] is not None for o in out),'bias',sum(o['bias'] is not None for o in out),'ivhv',sum(o['ivhv'] is not None for o in out),'path',sum(bool(o['path']) for o in out))
