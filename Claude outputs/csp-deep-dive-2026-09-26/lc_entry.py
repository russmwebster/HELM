import json,statistics as st
D=json.load(open('lc/lc.json'))
seen=set(); U=[]
for d in sorted(D,key=lambda d:d['book']!='REAL'):
    k=(d['t'],d['opened'])
    if k in seen: continue
    seen.add(k); U.append(d)
def stock(g,label):
    for k in (5,10,20):
        v=[(d[f'r{k}'],d[f'm{k}']) for d in g if d[f'r{k}'] is not None and d[f'm{k}'] is not None]
        if not v: continue
        ex=[a-b for a,b in v]
        print(f"  {label:26s} {k:2d}d n={len(v):2d} stock up {sum(a>0 for a,b in v)/len(v):4.0%} med stock {st.median([a for a,b in v]):+5.1%} med S&P {st.median([b for a,b in v]):+5.1%} | beat S&P {sum(e>0 for e in ex)/len(ex):4.0%} med excess {st.median(ex):+5.1%} mean excess {st.mean(ex):+5.1%}")
print('== Did the stock go up after we bought the call? (unique entries)')
stock(U,'all entries')
stock([d for d in U if not d['post_shift']],'before 25 Jul (ATM,60-90d)')
stock([d for d in U if d['post_shift']],'after 25 Jul (ITM,90-180d)')
stock([d for d in U if d['book']=='REAL'],'real-book entries')
stock([d for d in U if d['book']=='PAPER'],'paper-only entries')
# how much of option P&L is explained by the stock move
C=[d for d in D if d['status']=='CLOSED' and d['debit']]
print('\n== closed trades: option result vs stock move over the hold')
for lab,f in [('stock up over hold',lambda d:d['path'] and d['path'][-1]['spot'] and d['path'][0]['spot'] and d['path'][-1]['spot']>d['path'][0]['spot']),
              ('stock down over hold',lambda d:d['path'] and d['path'][-1]['spot'] and d['path'][0]['spot'] and d['path'][-1]['spot']<=d['path'][0]['spot'])]:
    g=[d for d in C if f(d)]
    print(f"  {lab:22s} n={len(g)} won {sum(d['pnl']>0 for d in g)} median % of debit {st.median([d['pnl']/d['debit'] for d in g]):+.0%}")
