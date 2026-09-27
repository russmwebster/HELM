import sys; sys.path.insert(0,'tools')
import w16_dte_sweep as w
c=w.connect(sys.argv[1]); rows=w.universe(c,None)
closed={r[0]:r[1] for r in c.execute("select id,date(closed_at) from positions")}
def g(label,sel):
    rs=[r for r in rows if sel(r)]; n=len(rs)
    print(f"== {label}  (n={n})")
    for d in (21,28):
        v=[w.outcome(c,r['id'],d,r['realized_pnl']) for r in rs]
        vals=[x[0] for x in v]; hit=sum(x[1] for x in v)
        win=sum(x>0 for x in vals); lose=sum(x<0 for x in vals); big=sum(x<-2000 for x in vals)
        print(f"  {d}: reached deadline {hit}/{n} | winners {win}/{n} ({win/n:.0%}) | losers {lose}/{n} ({lose/n:.0%}) | losses >$2k {big}/{n} ({big/n:.1%}) | total {sum(vals):+.0f} | avg/pos {sum(vals)/n:+.0f} | won {sum(x for x in vals if x>0):+.0f} lost {sum(x for x in vals if x<0):+.0f}")
    both=[(w.mark(c,r['id'],28),w.mark(c,r['id'],21)) for r in rs]
    both=[(a,b) for a,b in both if a is not None and b is not None]
    if both:
        imp=sum(b>a for a,b in both); m=len(both)
        print(f"  last week (28->21), {m} positions reached both: better {imp}/{m} ({imp/m:.0%}), avg change {sum(b-a for a,b in both)/m:+.0f}")
g('All history',lambda r:True)
g('Before last run (closed <= 17 Aug)',lambda r:closed[r['id']]<='2026-08-17')
g('New since last run (closed after 17 Aug)',lambda r:closed[r['id']]>'2026-08-17')
g('All, excluding June entries',lambda r:r['entry_month']!='2026-06')
g('CSP, excl June',lambda r:r['entry_month']!='2026-06' and r['strategy']=='CSP')
g('Iron condor, excl June',lambda r:r['entry_month']!='2026-06' and r['strategy']=='IRON_CONDOR')
