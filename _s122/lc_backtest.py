# s122 long-call stock-picking test. Read-only: reads _s122/px/*.csv and helm.db (watchlist sectors).
# Question: which entry rule picks stocks that go UP over the next 10/20/40 trading days?
# (A long call's result tracks the stock: 17/21 won when the stock rose, 0/36 when it fell.)
# Candidates: A current screen (price part), B proposal, C B+market filter, D breakout, E pullback, F baseline.
# Signals sampled every 5 trading days per stock. IV-based gates are NOT replicable (no IV history) -- same for all rules.
import os, glob, sqlite3, json, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); PX = os.path.join(HERE, "px")
ETF = {"Technology":"XLK","Energy":"XLE","Financial Services":"XLF","Healthcare":"XLV","Industrials":"XLI",
       "Consumer Cyclical":"XLY","Consumer Defensive":"XLP","Basic Materials":"XLB","Utilities":"XLU",
       "Communication Services":"XLC","Real Estate":"XLRE"}
def load(t):
    f = os.path.join(PX, t + ".csv")
    if not os.path.exists(f): return None
    d = pd.read_csv(f, index_col=0, parse_dates=True); d.index = pd.to_datetime(d.index).tz_localize(None)
    return d[~d.index.duplicated()].sort_index()
spy = load("SPY"); SPYc = spy["Close"]
db = sqlite3.connect("file:" + os.path.join(HERE, "..", "data", "helm.db") + "?mode=ro", uri=True)
sect = dict(db.execute("select ticker, sector from watchlist"))
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def adx(c, n=14):  # close-only approximation (no high/low stored): Wilder on close-to-close moves
    up = c.diff().clip(lower=0); dn = (-c.diff()).clip(lower=0); tr = c.diff().abs()
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100*up.ewm(alpha=1/n, adjust=False).mean()/atr; mdi = 100*dn.ewm(alpha=1/n, adjust=False).mean()/atr
    dx = 100*(pdi-mdi).abs()/(pdi+mdi); return dx.ewm(alpha=1/n, adjust=False).mean()
def rsi(c, n=14):
    d = c.diff(); g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean(); l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1+g/l)
spy200 = SPYc.rolling(200).mean()
rows = []
for f in sorted(glob.glob(os.path.join(PX, "*.csv"))):
    t = os.path.basename(f)[:-4]
    if t in ("SPY",) or t in ETF.values(): continue
    d = load(t)
    if d is None or len(d) < 400: continue
    c, v = d["Close"], d["Volume"]
    s = pd.DataFrame(index=d.index)
    s["c"] = c; s["sma20"]=c.rolling(20).mean(); s["ema20"]=ema(c,20); s["sma50"]=c.rolling(50).mean()
    s["sma150"]=c.rolling(150).mean(); s["sma200"]=c.rolling(200).mean()
    s["sma200_rising"] = s["sma200"] > s["sma200"].shift(21)
    macd = ema(c,12)-ema(c,26); s["macdh"] = macd - ema(macd,9)
    obv = (np.sign(c.diff()).fillna(0)*v).cumsum(); s["obv_tr"] = np.sign(obv - obv.shift(20))
    s["adx"] = adx(c); s["rsi"] = rsi(c)
    s["hi52"] = c.rolling(252).max(); s["pct_off_hi"] = c/s["hi52"]-1
    s["prev_hi"] = c.shift(1).rolling(251).max()
    sp = SPYc.reindex(s.index).ffill()
    s["ex20"] = (c/c.shift(20)) - (sp/sp.shift(20))
    s["mom12_1"] = c.shift(21)/c.shift(252) - 1
    s["spy_mom12_1"] = sp.shift(21)/sp.shift(252) - 1
    s["hv252"] = np.log(c).diff().rolling(252).std()*np.sqrt(252)*100
    s["vol_ratio"] = v/v.rolling(50).mean(); s["vol5"] = v.rolling(5).mean()/v.rolling(50).mean()
    s["tight"] = np.log(c).diff().rolling(10).std()/np.log(c).diff().rolling(60).std()
    s["ret5"] = c/c.shift(5)-1
    s["mkt_up"] = (sp > spy200.reindex(s.index).ffill())
    for k in (10,20,40):
        s[f"f{k}"] = c.shift(-k)/c - 1; s[f"x{k}"] = s[f"f{k}"] - (sp.shift(-k)/sp - 1)
    # replica of HELM momentum_bias (v2) on closes
    stack = np.where((c>s.sma50)&(s.sma50>s.sma200),2,np.where((c<s.sma50)&(s.sma50<s.sma200),-2,np.where(c>s.sma50,1,np.where(c<s.sma50,-1,0))))
    b = stack + np.sign(s["macdh"]).fillna(0) + s["obv_tr"].fillna(0)
    b = np.where(s["adx"]<20, 0, np.where(s["adx"]<25, np.trunc(b/2), b))
    s["bias"] = np.clip(b, -3, 3)
    s["t"] = t; s["sector"] = sect.get(t)
    s = s.iloc[260::5].dropna(subset=["f10","sma200","mom12_1","hv252"])
    rows.append(s)
D = pd.concat(rows); D["year"] = D.index.year
D["date"] = D.index
# cross-sectional momentum rank per date
D["mom_rank"] = D.groupby("date")["mom12_1"].rank(pct=True)
calm = D.hv252 < 40
A = (D.bias>=2)&(D.c>D.sma50)&(D.sma50>D.sma200)&calm
template = (D.c>D.sma50)&(D.c>D.sma150)&(D.c>D.sma200)&(D.sma150>D.sma200)&D.sma200_rising&(D.pct_off_hi>=-0.25)
notspiked = (D.ex20<=0.06)&(D.c<=D.sma50*1.08)
B = template&(D.mom_rank>=0.70)&notspiked&calm
C = B & D.mkt_up
Dbo = template&(D.mom_rank>=0.70)&calm&(D.c>=D.prev_hi)&(D.vol_ratio>=1.4)&(D.tight<=1.0)
E = template&(D.mom_rank>=0.70)&calm&(D.c<=D.sma50*1.03)&(D.c>=D.sma50*0.97)&(D.ret5<0)&(D.vol5<1.0)
F = calm
Aspike = A & ~notspiked
RULES = {"A current screen":A, "A, spiked entries only":Aspike, "B proposal":B, "C proposal + market up":C,
         "D breakout":Dbo, "E pullback":E, "F baseline (any calm name)":F}
def summ(m, sub=None):
    g = D[m] if sub is None else D[m & sub]
    if len(g)==0: return None
    o = {"n":int(len(g)), "names":int(g.t.nunique())}
    for k in (10,20,40):
        x = g[f"x{k}"].dropna(); f_ = g[f"f{k}"].dropna()
        o[f"up{k}"] = round(float((f_>0).mean()),3); o[f"beat{k}"] = round(float((x>0).mean()),3)
        o[f"medx{k}"] = round(float(x.median()),4); o[f"meanx{k}"] = round(float(x.mean()),4)
    o["up3_40"] = round(float((g["f40"].dropna()>0.03).mean()),3)
    return o
out = {"period": [str(D.index.min().date()), str(D.index.max().date())], "stocks": int(D.t.nunique()), "rules":{}}
for name, m in RULES.items():
    r = {"all": summ(m), "by_year": {}, "market_up": summ(m, D.mkt_up), "market_down": summ(m, ~D.mkt_up)}
    for y in sorted(D.year.unique()): r["by_year"][int(y)] = summ(m, D.year==y)
    out["rules"][name] = r
json.dump(out, open(os.path.join(HERE, "lc_backtest_results.json"), "w"), indent=1)
print("period", out["period"], "stocks", out["stocks"])
print(f"{'rule':30s} {'n':>6s} {'up20':>6s} {'beat20':>7s} {'medx20':>7s} {'up40':>6s} {'beat40':>7s} {'medx40':>7s} {'up>3%40':>8s}")
for name,r in out["rules"].items():
    a=r["all"]
    if a: print(f"{name:30s} {a['n']:6d} {a['up20']:6.0%} {a['beat20']:7.0%} {a['medx20']:+7.1%} {a['up40']:6.0%} {a['beat40']:7.0%} {a['medx40']:+7.1%} {a['up3_40']:8.0%}")
