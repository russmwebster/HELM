import sys,statistics; sys.path.insert(0,'tools')
import w16_dte_sweep as w
c=w.connect(sys.argv[1])
rows=w.universe(c,None)
cl={r['id']:r2 for r2 in [None] for r in rows}
closed={r[0]:r[1] for r in c.execute("select id,date(closed_at) from positions")}
def run(label,sel):
    rs=[r for r in rows if sel(r)]
    if not rs: return
    out=[]
    for n in (21,28):
        vals=[w.outcome(c,r['id'],n,r['realized_pnl'])[0] for r in rs]
        s=w.tail_stats(vals); out.append(s)
    d=[w.outcome(c,r['id'],28,r['realized_pnl'])[0]-w.outcome(c,r['id'],21,r['realized_pnl'])[0] for r in rs]
    nz=sorted([x for x in d if abs(x)>.5],key=lambda x:-abs(x))
    fav=[r['ticker'] for r in rs if w.outcome(c,r['id'],21,r['realized_pnl'])[0]< -2000 <= w.outcome(c,r['id'],28,r['realized_pnl'])[0]]
    adv=[r['ticker'] for r in rs if w.outcome(c,r['id'],28,r['realized_pnl'])[0]< -2000 <= w.outcome(c,r['id'],21,r['realized_pnl'])[0]]
    wk=[w.mark(c,r['id'],21)-w.mark(c,r['id'],28) for r in rs if w.mark(c,r['id'],21) is not None and w.mark(c,r['id'],28) is not None]
    print(f"{label:34s} n={len(rs):3d} | 21: tot {out[0]['total']:+7d} <-2k {out[0]['losses_under_2k']:2d} worst {out[0]['worst']:+6d} cvar {out[0]['cvar10']:+6d} winsum {out[0]['win_sum']:+6d} | 28: tot {out[1]['total']:+7d} <-2k {out[1]['losses_under_2k']:2d} worst {out[1]['worst']:+6d} cvar {out[1]['cvar10']:+6d} winsum {out[1]['win_sum']:+6d}")
    if nz: print(f"{'':34s} differ {len(nz)} helped {sum(x>0 for x in nz)} hurt {sum(x<0 for x in nz)} med {statistics.median(nz):+.0f} sum {sum(nz):+.0f} dropTop10 {sum(nz[10:]):+.0f} | cross fav {fav} adv {adv}")
    if wk: print(f"{'':34s} 28->21 week n={len(wk)} mean {statistics.mean(wk):+.0f} median {statistics.median(wk):+.0f} improved {sum(x>0 for x in wk)}")
run('ALL',lambda r:True)
run('NEW: closed after 2026-08-17',lambda r:closed[r['id']]>'2026-08-17')
run('OLD: closed <= 2026-08-17',lambda r:closed[r['id']]<='2026-08-17')
run('EXCL June-entry cohort',lambda r:r['entry_month']!='2026-06')
run('EXCL June, PAPER',lambda r:r['entry_month']!='2026-06' and r['book']=='PAPER')
run('EXCL June, REAL',lambda r:r['entry_month']!='2026-06' and r['book']=='REAL')
run('EXCL June, CSP',lambda r:r['entry_month']!='2026-06' and r['strategy']=='CSP')
run('EXCL June, IRON_CONDOR',lambda r:r['entry_month']!='2026-06' and r['strategy']=='IRON_CONDOR')
