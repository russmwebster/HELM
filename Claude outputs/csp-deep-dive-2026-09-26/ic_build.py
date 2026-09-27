# Iron condor + bear call spread dataset (read-only). Paths trimmed to closed_at.
import sqlite3, json
c=sqlite3.connect('helm_copy.db'); c.row_factory=sqlite3.Row
mc={r['as_of_date']:dict(r) for r in c.execute("select * from market_context")}
def mctx(d):
    ks=[k for k in mc if k<=d]; return mc[max(ks)] if ks else {}
sector={r['ticker']:(r['sector'],r['beta']) for r in c.execute("select * from watchlist")}
out=[]
for p in c.execute("select * from positions where strategy in ('IRON_CONDOR','BEAR_CALL_SPREAD') and status='CLOSED' and coalesce(exit_reason,'')!='VOIDED'"):
    legs={l['leg_role']:dict(l) for l in c.execute("select * from legs where position_id=?",(p['id'],))}
    sp=legs.get('SHORT_PUT',{}).get('strike'); sc=legs.get('SHORT_CALL',{}).get('strike')
    lp=legs.get('LONG_PUT',{}).get('strike'); lc=legs.get('LONG_CALL',{}).get('strike')
    n=max([l['contracts'] or 0 for l in legs.values()] or [1])
    es=c.execute("select * from entry_snapshots where position_id=? order by snapshot_at limit 1",(p['id'],)).fetchone(); es=dict(es) if es else {}
    path=[dict(t=r['checked_at'][:16],dte=r['dte_now'],pnl=r['pnl_unrealized'],spot=r['spot_price'],buf=r['buffer_pct'],ivr=r['iv_rank'],delta=r['delta'])
          for r in c.execute("select * from checks where position_id=? and data_quality='GOOD' and pnl_unrealized is not null order by checked_at",(p['id'],))
          if not p['closed_at'] or r['checked_at'][:16]<=p['closed_at'][:16]]
    iv=es.get('iv_current'); iv=(iv/100 if iv and iv>3 else iv); spot=es.get('spot_price')
    m=mctx(p['opened_at'][:10]); s=sector.get(p['ticker'],(None,None))
    wing=max([(sp-lp) if sp and lp else 0,(lc-sc) if sc and lc else 0])
    out.append(dict(id=p['id'],t=p['ticker'],strat=p['strategy'],book=p['book'],opened=p['opened_at'][:10],closed=(p['closed_at'] or '')[:10],
        month=p['opened_at'][:7],reason=p['exit_reason'],pnl=p['realized_pnl'],credit=abs(p['net_premium'] or 0),maxloss=abs(p['max_loss'] or 0),n=n,
        sp=sp,sc=sc,wing=wing,edte=p['entry_dte'] or es.get('dte'),delta0=abs(es['delta']) if es.get('delta') is not None else None,
        ivr0=es.get('iv_rank'),iv0=iv,spread_pct=es.get('bid_ask_spread_pct'),spot0=spot,
        put_otm=((spot-sp)/spot*100 if spot and sp else None),call_otm=((sc-spot)/spot*100 if spot and sc else None),
        cw=(abs(p['net_premium'] or 0)/(wing*100*n) if wing and n else None),
        vix0=m.get('vix'),trend0=m.get('index_trend'),sector=s[0],beta=s[1],path=path))
json.dump(out,open('ic/ic.json','w'))
from collections import Counter
print(len(out),Counter((o['strat'],o['book']) for o in out),Counter(o['reason'] for o in out))
print('with path',sum(bool(o['path']) for o in out),'cw',sum(o['cw'] is not None for o in out),'spread',sum(o['spread_pct'] is not None for o in out))
