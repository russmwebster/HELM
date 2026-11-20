# Screening study, part 3: ten-year price tests. Read-only. Usage: python3 screen_tests.py PXDIR DB OUTJSON
import sys, os, json, math, sqlite3
import numpy as np, pandas as pd
PX, DB, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
erf = np.vectorize(math.erf)
N = lambda x: 0.5 * (1 + erf(np.asarray(x) / math.sqrt(2)))
px = {f[:-4]: pd.read_csv(os.path.join(PX, f), index_col=0, parse_dates=True)["Close"] for f in os.listdir(PX) if f.endswith(".csv")}
spy = px["SPY"]; idx = spy.index
vix = px["VIX"].reindex(idx).ffill(); v3 = px["VIX3M"].reindex(idx).ffill()
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
sector = {t: s for t, s in con.execute("select ticker, sector from watchlist where coalesce(active,0)=1")}
funds = {r[0] for r in con.execute("select ticker from approved_funds")}
earn = {}
for t, d in con.execute("select ticker, earn_date from earnings_history"):
    earn.setdefault(t, []).append(pd.Timestamp(d))
EARN_FROM = pd.Timestamp("2020-07-21")
SECETF = {"Basic Materials": "XLB", "Communication Services": "XLC", "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
          "Energy": "XLE", "Financial Services": "XLF", "Healthcare": "XLV", "Industrials": "XLI", "Real Estate": "XLRE",
          "Technology": "XLK", "Utilities": "XLU"}
# ---- market features
ma200 = spy.rolling(200).mean(); ma50 = spy.rolling(50).mean()
rv20 = np.log(spy).diff().rolling(20).std() * math.sqrt(252) * 100
dd = spy / spy.rolling(252, min_periods=60).max() - 1
state = pd.Series("mixed", idx); state[(spy > ma200) & (ma50 > ma200)] = "rising"; state[(spy < ma200) & (ma50 < ma200)] = "falling"
ratio = vix / v3
fear = pd.Series("normal", idx); fear[ratio > 1.0] = "short-term fear above 3-month"; fear[ratio < 0.9] = "calm curve"
vixb = pd.cut(vix, [0, 15, 20, 30, 200], labels=["VIX<15", "15-20", "20-30", "30+"])
spike_passed = (vix.rolling(20).max() >= 30) & (vix <= 0.8 * vix.rolling(20).max())
rebound = (dd.rolling(60).min() <= -0.15) & (spy > spy.rolling(20).min() * 1.05)
gapb = pd.cut(vix - rv20, [-100, 0, 4, 8, 100], labels=["VIX below actual", "0-4 pts", "4-8 pts", "8+ pts"])
# sector leadership: 6-month return rank among sector funds
sr = pd.DataFrame({e: px[e].reindex(idx).pct_change(126) for e in SECETF.values()})
srank = sr.rank(axis=1, ascending=False); scount = sr.notna().sum(axis=1)
HALF = pd.Timestamp("2021-11-20")
# puts that lived through each bad stretch: entered from ~45 days before it began to its end
STRETCH = {"late 2018": ("2018-08-15", "2018-12-24"), "early 2020": ("2020-01-01", "2020-03-23"),
           "2022": ("2021-11-15", "2022-10-12"), "spring 2025": ("2025-01-01", "2025-04-08")}
H, T, D1 = 31, 45 / 365, 0.613
rows = []
for t, s0 in px.items():
    if t in ("VIX", "VIX3M"): continue
    s = s0.reindex(idx)
    lr = np.log(s).diff(); vol = lr.rolling(60).std() * math.sqrt(252)
    hi = s.rolling(252).max(); r21 = s.pct_change(21); r126 = s.pct_change(126)
    stead = s.rolling(5).min() > s.shift(5).rolling(15).min()
    isfund = t in funds or t in SECETF.values()
    etf = SECETF.get(sector.get(t))
    ed = sorted(earn.get(t, []))
    for i in range(252, len(idx) - H, 5):
        S, sig, ST = s.iloc[i], vol.iloc[i], s.iloc[i + H]
        if not (S > 0 and sig > 0 and ST > 0) or np.isnan(hi.iloc[i]): continue
        sT = sig * math.sqrt(T); K = S * math.exp(-(D1 * sT - 0.5 * sT * sT)); d2 = D1 - sT
        P = float(K * N(-d2) - S * N(-D1)); loss = max(0.0, K - ST)
        day = idx[i]
        lead = None
        if etf and not np.isnan(srank.at[day, etf]):
            rk, n = srank.at[day, etf], scount.at[day]
            lead = "leading sector" if rk <= 3 else ("lagging sector" if rk > n - 3 else "middle sector")
        dist = S / hi.iloc[i] - 1
        if r21.iloc[i] < -0.10:
            dip = "sharp drop, within 25% of high" if dist > -0.25 else "sharp drop, >25% below high"
        elif r126.iloc[i] < -0.20 and dist < -0.25:
            dip = "long slide, >25% below high"
        else:
            dip = "no big drop"
        er = None
        if ed and day >= EARN_FROM:
            end = idx[i + H]; er = any(day < x <= end for x in ed)
        rows.append(dict(t=t, day=day, fund=isfund, K=K, P=P, loss=loss, ST=ST,
                         state=state.iat[i], fear=fear.iat[i], vixb=str(vixb.iat[i]), spike=bool(spike_passed.iat[i]),
                         vix=vix.iat[i], rebound=bool(rebound.iat[i]), gap=str(gapb.iat[i]), lead=lead, dip=dip,
                         stead=bool(stead.iat[i]), report=er))
df = pd.DataFrame(rows)
df["half"] = np.where(df.day < HALF, "first half", "second half")
df["stretch"] = None
for k, (a, b) in STRETCH.items():
    df.loc[(df.day >= a) & (df.day <= b), "stretch"] = k
def summ(d, by):
    out = {}
    for k, g in d.groupby(by, dropna=True):
        if len(g) < 30: continue
        out[str(k)] = dict(n=int(len(g)), assigned=round(100 * (g.ST < g.K).mean(), 1),
                           avg_loss_pct=round(100 * (g.loss / g.K).mean(), 2),
                           payout=round(g.loss.sum() / g.P.sum(), 2),
                           big_loss=round(100 * (g.loss / g.K > 0.10).mean(), 1),
                           net_pct=round(100 * ((g.P - g.loss) / g.K).mean(), 2))
    return out
def both(d, by):
    return {"all": summ(d, by), **{h: summ(d[d.half == h], by) for h in ("first half", "second half")}}
R = {"n_puts": int(len(df)), "n_names": int(df.t.nunique()), "first_day": str(df.day.min().date()), "last_day": str(df.day.max().date())}
st = df[~df.fund]
R["q1_state_stocks"] = both(st, "state")
R["q1_state_spy"] = both(df[df.t == "SPY"], "state")
R["q1_fear_stocks"] = both(st, "fear")
R["q1_vix_stocks"] = both(st, "vixb")
R["q1_spike_vix20plus"] = both(st[st.vix >= 20], "spike")
R["q1_rebound_stocks"] = both(st, "rebound")
R["q1_stretch_by_fear"] = {k: summ(st[st.stretch == k], "fear") for k in STRETCH}
R["q1_stretch_by_spike"] = {k: summ(st[(st.stretch == k) & (st.vix >= 20)], "spike") for k in STRETCH}
R["fund_vs_stock"] = both(df, "fund")
R["fund_vs_stock_stretch"] = {k: summ(df[df.stretch == k], "fund") for k in STRETCH}
R["q2_lead"] = both(st.dropna(subset=["lead"]), "lead")
R["q2_lead_rebound"] = summ(st.dropna(subset=["lead"])[st.dropna(subset=["lead"]).rebound], "lead")
R["q2_lead_falling"] = summ(st.dropna(subset=["lead"])[st.dropna(subset=["lead"]).state == "falling"], "lead")
R["q2_lead_stretch"] = {k: summ(st.dropna(subset=["lead"])[st.dropna(subset=["lead"]).stretch == k], "lead") for k in STRETCH}
R["q3_dip"] = both(st, "dip")
sd = st[st.dip.str.startswith("sharp")]
R["q3_sharp_steadied"] = both(sd.assign(k=sd.dip + " / " + np.where(sd.stead, "steadied", "still falling")), "k")
R["q4_gap_spy"] = both(df[df.t == "SPY"], "gap")
R["q4_gap_stocks"] = both(st, "gap")
R["q5_report_in_trade"] = both(st.dropna(subset=["report"]), "report")
# ---- q6: bad days
rets = pd.DataFrame({t: px[t].reindex(idx).pct_change() for t in px if t not in ("VIX", "VIX3M") and t not in funds and t not in SECETF.values()})
sp = spy.pct_change()
normal = sp.abs() < 0.015
beta = {t: (rets[t][normal].cov(sp[normal]) / sp[normal].var()) for t in rets}
bad = sp <= -0.02
worst = sp.nsmallest(25).index
q6 = {"bad_days": int(bad.sum()), "worst25_from": str(min(worst).date()), "worst25_to": str(max(worst).date())}
ratios = []
for d in worst:
    for t in rets:
        r, b = rets.at[d, t], beta[t]
        if np.isnan(r) or np.isnan(b) or b <= 0: continue
        ratios.append(r / (b * sp[d]))
ratios = np.array(ratios)
q6["worst25_fell_more_than_multiple_pct"] = round(100 * (ratios > 1).mean(), 1)
q6["worst25_fell_1p5x_multiple_pct"] = round(100 * (ratios > 1.5).mean(), 1)
q6["worst25_median_ratio"] = round(float(np.median(ratios)), 2)
def avgcorr(mask):
    sub = rets[mask].dropna(axis=1, thresh=int(mask.sum() * 0.8))
    c = sub.corr(); names = list(c.columns)
    same, diff = [], []
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            v = c.iat[a, b]
            if np.isnan(v): continue
            sa, sb = sector.get(names[a]), sector.get(names[b])
            if sa and sb: (same if sa == sb else diff).append(v)
    return dict(days=int(mask.sum()), same_sector=round(float(np.mean(same)), 2), different_sectors=round(float(np.mean(diff)), 2))
q6["corr_spy_down_1pct"] = avgcorr(sp <= -0.01)
q6["corr_spy_up_1pct"] = avgcorr(sp >= 0.01)
q6["corr_quiet_days"] = avgcorr(sp.abs() < 0.005)
# share of stocks falling together on bad days
fell = (rets[bad] < -0.02).mean(axis=1)
q6["bad_days_share_of_names_down_2pct_median"] = round(100 * float(fell.median()), 1)
R["q6"] = q6
# ---- q7: wide-market rejections in recorded put runs
runs = con.execute("select rejects_json from put_runs").fetchall()
cnt = {}
for (j,) in runs:
    for t, why in json.loads(j or "{}").items():
        if "spread" in why: cnt[t] = cnt.get(t, 0) + 1
R["q7_runs"] = len(runs); R["q7_spread_rejects_by_name"] = dict(sorted(cnt.items(), key=lambda x: -x[1]))
json.dump(R, open(OUT, "w"), indent=1, default=str)
print("ok", R["n_puts"], R["n_names"], R["first_day"], R["last_day"])
