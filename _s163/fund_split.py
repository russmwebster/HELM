# s163, read-only: funds vs single stocks on the screening study's simulated puts (the figures in the 2026-11-20
# reply to the Book). Same put as _study_screen/screen_tests.py (0.27-delta, 45 days, priced off the name's own
# 60-day swings), plus: touched (any close below the strike), SPY priced off the VIX, groups of funds.
# Usage: cd ~/Projects/helm && python3 _s163/fund_split.py _study_screen/px data/helm.db _s163/fund_split_out.json
import sys, os, json, math, sqlite3
import numpy as np, pandas as pd
PX, DB, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
erf = np.vectorize(math.erf)
N = lambda x: 0.5 * (1 + erf(np.asarray(x) / math.sqrt(2)))
px = {f[:-4]: pd.read_csv(os.path.join(PX, f), index_col=0, parse_dates=True)["Close"] for f in os.listdir(PX) if f.endswith(".csv")}
spy = px["SPY"]; idx = spy.index
vix = px["VIX"].reindex(idx).ffill(); v3 = px["VIX3M"].reindex(idx).ffill()
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
bucket = {t: b for t, b in con.execute("select ticker, bucket from approved_funds")}
SECT = {"XLB","XLC","XLY","XLP","XLE","XLF","XLV","XLI","XLRE","XLK","XLU","SMH"}
def group(t):
    if t in ("SPY","QQQ","IWM","DIA"): return "1 big index funds"
    if t in SECT: return "2 sector funds"
    if t in bucket: return "3 other funds"
    return "4 single stocks"
ratio = vix / v3
fear = pd.Series("normal", idx); fear[ratio > 1.0] = "fear up"; fear[ratio < 0.9] = "calm"
dd = spy / spy.rolling(252, min_periods=60).max() - 1
rebound = (dd.rolling(60).min() <= -0.15) & (spy > spy.rolling(20).min() * 1.05)
vlo = vix.rolling(252).min(); vhi = vix.rolling(252).max(); vrank = 100 * (vix - vlo) / (vhi - vlo)
HALF = pd.Timestamp("2021-11-20")
STRETCH = {"late 2018": ("2018-08-15", "2018-12-24"), "early 2020": ("2020-01-01", "2020-03-23"),
           "2022": ("2021-11-15", "2022-10-12"), "spring 2025": ("2025-01-01", "2025-04-08")}
H, T, D1 = 31, 45 / 365, 0.613
rows = []
def one(t, s, vol_series, tag):
    for i in range(252, len(idx) - H, 5):
        S, sig, ST = s.iloc[i], vol_series.iloc[i], s.iloc[i + H]
        if not (S > 0 and sig > 0 and ST > 0): continue
        sT = sig * math.sqrt(T); K = S * math.exp(-(D1 * sT - 0.5 * sT * sT)); d2 = D1 - sT
        P = float(K * N(-d2) - S * N(-D1)); loss = max(0.0, K - ST)
        low = s.iloc[i + 1:i + H + 1].min()
        rows.append(dict(t=t, g=tag, day=idx[i], K=K, P=P, loss=loss, ST=ST, touched=bool(low < K), sig=sig,
                         fear=fear.iat[i], rebound=bool(rebound.iat[i]), vrich=bool(vrank.iat[i] >= 50)))
for t, s0 in px.items():
    if t in ("VIX", "VIX3M"): continue
    s = s0.reindex(idx)
    if s.rolling(252).max().notna().sum() == 0: continue
    vol = np.log(s).diff().rolling(60).std() * math.sqrt(252)
    vol[s.rolling(252).max().isna()] = np.nan
    one(t, s, vol, group(t))
    if t == "SPY":
        one("SPY", s, vol, "0a SPY, priced off its own swings")
        one("SPY", s, vix / 100.0, "0b SPY, priced off the VIX")
df = pd.DataFrame(rows)
df["half"] = np.where(df.day < HALF, "first half", "second half")
df["stretch"] = None
for k, (a, b) in STRETCH.items():
    df.loc[(df.day >= a) & (df.day <= b), "stretch"] = k
def summ(g):
    if len(g) < 20: return None
    net = (g.P - g.loss) / g.K
    return dict(n=int(len(g)), names=int(g.t.nunique()), prem_pct=round(100 * (g.P / g.K).mean(), 2),
                touched=round(100 * g.touched.mean(), 1), assigned=round(100 * (g.ST < g.K).mean(), 1),
                big_loss=round(100 * (g.loss / g.K > 0.10).mean(), 1), avg_loss_pct=round(100 * (g.loss / g.K).mean(), 2),
                payout=round(g.loss.sum() / g.P.sum(), 2), net_pct=round(100 * net.mean(), 2),
                net_yr=round(100 * net.mean() * 365 / 45, 1), worst_pct=round(100 * net.min(), 1),
                p5_pct=round(100 * net.quantile(0.05), 1), vol=round(100 * g.sig.mean(), 1))
halves = lambda x: {"all": summ(x), "first half": summ(x[x.half == "first half"]), "second half": summ(x[x.half == "second half"])}
R = {}
for g, d in df.groupby("g"):
    R[g] = dict(halves(d), stretch={k: summ(d[d.stretch == k]) for k in STRETCH},
                fear={k: halves(x) for k, x in d.groupby("fear")}, rebound={str(k): halves(x) for k, x in d.groupby("rebound")})
R["by_name"] = {t: summ(df[(df.t == t) & (df.g == "1 big index funds")]) for t in ("SPY","QQQ","IWM","DIA")}
df["yr"] = df.day.dt.year
R["by_year"] = {g: {int(y): round(100 * ((x.P - x.loss) / x.K).mean() * 365 / 45, 1) for y, x in d.groupby("yr")} for g, d in df.groupby("g")}
# the desk's "rich premium" bar applied to the S&P: the VIX in the upper half of its own past-year range
r = vrank.dropna()
R["vix_rank_50plus_pct_of_days"] = {"all": round(100 * (r >= 50).mean(), 1), "by_year": {int(y): round(100 * (x >= 50).mean()) for y, x in r.groupby(r.index.year)}}
sv = df[df.g == "0b SPY, priced off the VIX"]
R["spy_off_vix_by_bar"] = {("bar met" if k else "bar not met"): halves(x) for k, x in sv.groupby("vrich")}
json.dump(R, open(OUT, "w"), indent=1, default=str)
print("puts", len(df), "| S&P fund off the VIX, per year:", R["0b SPY, priced off the VIX"]["all"]["net_yr"], "| index funds:", R["1 big index funds"]["all"]["net_yr"],
      "| single stocks:", R["4 single stocks"]["all"]["net_yr"], "| VIX over the bar, % of days:", R["vix_rank_50plus_pct_of_days"]["all"])
