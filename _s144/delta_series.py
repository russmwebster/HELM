import sqlite3, sys, json
DB=sys.argv[1]
c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
spy=dict(l.strip().split(',') for l in open(sys.argv[2]))
spy={k:float(v) for k,v in spy.items()}
beta={r['ticker']:r['beta'] for r in c.execute("select ticker,beta from watchlist")}
days=sorted(d for d in spy if d>='2026-09-15')
out=[]
for D in days:
    T=D+'T15:59:59'; D0=D+'T00:00'
    rows=[]
    for p in c.execute("select * from positions where book='REAL' and opened_at<=? and (closed_at is null or closed_at>?)",(T,T)):
        legs=c.execute("select * from legs where position_id=? and open_date<=? and (close_date is null or close_date>?)",(p['id'],D,T)).fetchall()
        chk=c.execute("select * from checks where position_id=? and checked_at between ? and ? order by checked_at desc limit 1",(p['id'],D0,T)).fetchone()
        dsh=0; gsh=0; th=0; miss=[]; 
        for l in legs:
            if l['leg_role'] in ('LONG_STOCK','SHORT_STOCK'):
                dsh+=(1 if l['direction']=='LONG' else -1)*(l['contracts'] or 0); continue
            rd=c.execute("select * from leg_checks where leg_id=? and checked_at between ? and ? order by checked_at desc limit 1",(l['id'],D0,T)).fetchone()
            if (rd is None or rd['delta'] is None) and len(legs)==1 and chk and chk['delta'] is not None: rd=chk
            if rd is None or rd['delta'] is None: miss.append(l['leg_role']); continue
            m=(1 if l['direction']=='LONG' else -1)*(l['multiplier'] or 100)*(l['contracts'] or 0)
            dsh+=m*rd['delta']; gsh+=m*(rd['gamma'] or 0); th+=m*(rd['theta'] or 0)
        spot=chk['spot_price'] if chk else None
        b=beta.get(p['ticker']) or 1.0
        bw=dsh*spot*b if spot else None
        rows.append(dict(id=p['id'],t=p['ticker'],s=p['strategy'],dsh=dsh,gsh=gsh,spot=spot,beta=b,bw=bw,th=th,miss=miss,nolegs=len(legs)==0))
    out.append(dict(d=D,spy=spy[D],rows=rows))
json.dump(out,open(sys.argv[3],'w'))
for o in out:
    bw=sum(r['bw'] or 0 for r in o['rows']); th=sum(r['th'] for r in o['rows'])
    bad=[r['t'] for r in o['rows'] if r['bw'] is None or r['miss']]
    print(o['d'], o['spy'], len(o['rows']), round(bw), round(th), 'incomplete:',bad)
