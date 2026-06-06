# HELM Health Map — Design Reference (v9)
# Built: 2026-06-05 — port to helm-server next session

## Command intent
helm health              # open portfolio health map in browser
helm health ORCL         # open single position heat map

## Data query (CSP positions)
SELECT p.ticker, p.company_name, l.strike, l.open_price, l.entry_delta, l.contracts,
       p.net_premium, c.spot_price, c.pnl_unrealized, c.delta, c.delta_vs_entry,
       c.dte_now, c.theta, c.iv_rank
FROM positions p
JOIN legs l ON l.position_id=p.id AND l.leg_role='SHORT_PUT'
LEFT JOIN checks c ON c.id=(
    SELECT id FROM checks WHERE position_id=p.id ORDER BY checked_at DESC LIMIT 1
)
WHERE p.status='OPEN' AND p.strategy='CSP'
ORDER BY p.ticker

## Scoring model (CSP profile)
Variable         Weight   Score 0-10
-------------------------------------------
b/e buffer %     25%      0=below b/e, 3=<5%, 5=<10%, 7=<15%, 9=<20%, 10=>20%
delta x DTE      20%      combined signal — see scorecard below
delta            15%      10=<0.20, 8=<0.30, 6=<0.40, 3=<0.50, 1=>0.50
1x stop used     15%      10=<20%, 8=<40%, 5=<60%, 3=<80%, 1=<100%, 0=>=100%
theta/day        10%      10=<7d recovery, 8=<14d, 6=<21d, 4=<30d, 2=>30d
DTE              10%      10=>30d, 7=>21d, 5=>14d, 3=>7d, 1=<=7d, 0=expired
strike buffer    5%       10=>15%, 7=>8%, 4=>3%, 2=>0%, 0=ITM
delta drift      5%       9=<0.05, 7=<0.10, 4=<0.20, 1=>0.20, null=gray
IVR              ctx      8=>70, 5=>40, 3=<40 (context only, not weighted)
premium          ref      reference only, no score

## delta x DTE scorecard
delta<0.30 + DTE>21  = 10
delta<0.30 + DTE<=21 = 7
delta<0.40 + DTE>21  = 6
delta<0.40 + DTE<=21 = 3
delta>=0.40 + DTE>21 = 4
delta>=0.40 + DTE<=21= 1
no delta data        = 6 (neutral default)

## Composite -> color
score >= 70  green   rgb interpolated
score >= 40  amber
score <  40  red

## Summary row (per position)
spot (color: green=above strike, amber=between strike and b/e, red=below b/e)
strike
b/e
DTE
close now pill: pnl_unrealized (green if positive, red if negative)
ITM badge if spot < strike

## Heat map layout (3 rows, cell size = weight)
Row 1 (large):   b/e buffer (2.2fr)  |  delta x DTE (1.8fr)
Row 2 (medium):  delta (1fr)  |  1x stop used (1fr)  |  theta/day (1.4fr)
Row 3 (small):   DTE (1fr)  |  strike buf (1fr)  |  delta drift (1fr)  |  IVR (1fr)  |  premium (1fr)

## Cell color stops
score >= 8: bg=#eaf3de bd=#97c459 text=#27500a val=#3b6d11 (green)
score >= 6: bg=#f2f8e6 bd=#b8d97a text=#3b6d11 val=#639922 (light green)
score >= 4: bg=#faeeda bd=#fac775 text=#633806 val=#ba7517 (amber)
score >= 2: bg=#fdf0da bd=#f0b050 text=#854f0b val=#d85a30 (orange)
score <  2: bg=#fcebeb bd=#f09595 text=#791f1f val=#a32d2d (red)
no data:    bg=#f5f5f3 bd=#dddcd5 text=#888780 val=#5f5e5a (gray)

## Pending work
- Port to helm-server: GET /health returns the full HTML page
- Add helm health CLI command (opens browser to helm.local:8766/health)
- Fix delta not writing to checks table (helm check --silent not capturing greeks)
- Long Call health profile (different variables — theta hurts, delta rising = good)
- Iron Condor health profile (two-sided — put side + call side each scored)
- Guidance redesign using composite score not P&L% threshold
- ITM-but-above-b/e deserves its own guidance message (not emergency RED)

---

## LONG CALL health profile

### Key difference from CSP
- Theta is your ENEMY — time decay costs you money every day
- Delta rising = GOOD (stock moving toward or through strike)
- IV rising = GOOD (increases option value)
- You need the stock to MOVE — not sit still

### Variables (Long Call)
Variable           Weight   Notes
buffer to strike   25%      How far stock needs to rise to reach strike (OTM)
delta              20%      Want HIGH and RISING — 0.50+ is good
theta decay rate   15%      INVERTED vs CSP. High theta = bad for buyer.
                            Score: theta/day as % of option value. <1%/d=good, >3%/d=bad
DTE                15%      Less time = MORE urgent (opposite of CSP)
                            >60d=10, 45-60d=8, 30-45d=6, 21-30d=4, <21d=2, <7d=0
time value %       10%      % of option price that is time value (not intrinsic)
                            High time value = more decay risk. Lower = safer
IV change          10%      Rising IV since entry = good. Falling = bad.
stop used          5%       Max loss = premium paid. Same logic as CSP.

### Heat map layout (Long Call)
Row 1: buffer to strike (2.2fr) | delta (1.8fr)
Row 2: theta decay rate (1fr) | DTE (1fr) | IV change (1.4fr)
Row 3: time value % | stop used | intrinsic value | premium paid

---

## IRON CONDOR health profile

### Key difference
Two-sided — put spread below AND call spread above.
Profit when stock stays INSIDE the range.
Net delta near zero = healthy. Drift = risk.

### Variables (Iron Condor)
Variable              Weight   Notes
put side b/e buffer   20%      spot - (short_put_strike - net_credit/share)
call side b/e buffer  20%      (short_call_strike + net_credit/share) - spot
net delta             15%      |net_delta|<0.05=10, <0.10=7, <0.20=4, >0.20=1
DTE                   10%      Same as CSP — more time = better
put side delta        10%      Short put delta — want LOW, rising = bad
call side delta       10%      Short call delta — want LOW, rising = bad
1x stop used          10%      Max loss = spread width - credit collected
theta/day             5%       Earning from both spreads — good

### Alert thresholds
- Stock within 3% of either short strike = warning
- Either short delta > 0.25 = watch
- Either short delta > 0.40 = action required
- 21 DTE = evaluate close
- 7 DTE = close immediately

### Heat map layout (Iron Condor)
Row 1: put b/e buffer (2fr) | call b/e buffer (2fr)
Row 2: net delta (1fr) | put delta (1fr) | call delta (1fr)
Row 3: DTE | 1x stop used | theta/day | profit zone width remaining

