# CSP deep dive dataset (read-only, helm_copy.db snapshot 2026-09-26)
import sqlite3, json, math
c=sqlite3.connect('helm_copy.db'); c.row_factory=sqlite3.Row
mc={r['as_of_date']:dict(r) for r in c.execute("select * from market_context")}
def mctx(d):
    ks=[k for k in mc if k<=d]; return mc[max(ks)] if ks else {}
sector={r['ticker']:(r['sector'],r['exposure_group'],r['beta']) for r in c.execute("select * from watchlist")}
out=[]
for p in c.execute("select * from positions where strategy='CSP' and status='CLOSED' and coalesce(exit_reason,'')!='VOIDED'"):
    leg=c.execute("select * from legs where position_id=? order by id limit 1",(p['id'],)).fetchone()
    n=leg['contracts'] if leg else (p['total_contracts'] or 1)
    credit=abs(p['net_premium'] or 0)
    es=c.execute("select * from entry_snapshots where position_id=? order by snapshot_at limit 1",(p['id'],)).fetchone()
    es=dict(es) if es else {}
    path=[dict(t=r['checked_at'][:16],dte=r['dte_now'],pnl=r['pnl_unrealized'],spot=r['spot_price'],buf=r['buffer_pct'],ivr=r['iv_rank'],delta=r['delta'])
          for r in c.execute("select * from checks where position_id=? and data_quality='GOOD' and pnl_unrealized is not null order by checked_at",(p['id'],))]
    iv=es.get('iv_current'); iv=(iv/100 if iv and iv>3 else iv)
    spot=es.get('spot_price'); dte=es.get('dte') or p['entry_dte']
    sigma=(spot*iv*math.sqrt(dte/365)*100*n) if (spot and iv and dte) else None
    m=mctx(p['opened_at'][:10])
    s=sector.get(p['ticker'],(None,None,None))
    path=[m for m in path if not p['closed_at'] or m['t']<=p['closed_at'][:16]]
    out.append(dict(id=p['id'],t=p['ticker'],book=p['book'],opened=p['opened_at'][:10],closed=p['closed_at'][:10],
        month=p['opened_at'][:7],reason=p['exit_reason'],pnl=p['realized_pnl'],credit=credit,n=n,
        strike=leg['strike'] if leg else None,expiry=leg['expiration'] if leg else None,
        edte=p['entry_dte'] or dte,delta0=abs(es['delta']) if es.get('delta') is not None else None,
        ivr0=es.get('iv_rank'),iv0=iv,hv30=es.get('hv_30d'),atr_otm=es.get('atr_strikes_otm'),spread_pct=es.get('bid_ask_spread_pct'),
        dte_earn0=es.get('days_to_earnings'),spot0=spot,otm_pct=((spot-leg['strike'])/spot*100 if spot and leg else None),
        sigma=sigma,collateral=(leg['strike']*100*n if leg else None),
        vix0=m.get('vix'),trend0=m.get('index_trend'),sector=s[0],group=s[1],beta=s[2],
        earn=p['earnings_date'],path=path))
json.dump(out,open('csp/csp.json','w'))
from collections import Counter
print(len(out),Counter(o['book'] for o in out),Counter(o['reason'] for o in out))
print('with path',sum(bool(o['path']) for o in out),'with delta0',sum(o['delta0'] is not None for o in out),'ivr0',sum(o['ivr0'] is not None for o in out),'sigma',sum(o['sigma'] is not None for o in out),'sector',sum(o['sector'] is not None for o in out))
