# The HELM Decision Model — Description & Assessment

*A walk through what HELM actually does at each stage, the indicators it uses and why, and an honest read on where the edge is (and isn't) yet. Grounded in the code as of s57 — `helm/decision.py`, `helm/cli/scan_cmd.py`, `helm/verdict.py`, `helm/health.py`.*

---

## 0. The thesis it's built on

HELM is a bet on the **variance risk premium (VRP)**: implied volatility tends to trade richer than volatility subsequently realizes, so *selling* options when IV is expensive and *buying* them when IV is cheap should, over many trades, pay a premium. Everything downstream serves that idea. The headline the system is trying to earn is **risk-adjusted expectancy**, not win rate — a deliberate choice, because premium-selling wins often and loses rarely-but-large, and win rate flatters exactly that shape.

The model runs in three stages: **entry** (what to put on and why), **lifecycle** (when to take it off), and **health** (what the trader sees between those two). A fourth layer — **learning** — instruments all three so the levers can eventually be tuned against real outcomes rather than intuition.

---

## 1. Entry — the scan (`scan_cmd.py`)

### 1a. Feature computation (`fetch_technicals`)

For each name, HELM pulls daily bars from yfinance and computes a fixed feature set:

- **price** — the current close.
- **RSI-14** — 14-period Relative Strength Index. Momentum/mean-reversion oscillator.
- **EMA-20, SMA-50, SMA-200** — a fast exponential average and two slower simple averages. Trend structure.
- **ATR-14** — 14-period Average True Range. A raw volatility/typical-move measure, also used to set strike distances.
- **IV (current)** — implied vol read off the options chain at the nearest expiry ≥ 20 DTE. The "is vol expensive right now?" number in absolute terms.
- **IVR (IV Rank)** — where current IV sits within its *own* trailing range (a percentile-like 0–100). This is the load-bearing volatility signal: it answers "expensive *relative to this name's own history*," which is what VRP actually cares about. Sourced from `iv_history`; can be stale/absent when there's no IBKR data.
- **52-week high/low and `price_vs_52wk_pct`** — position within the annual range (0 = at low, 100 = at high).

*Why this set:* it's the minimum needed to answer three separate questions — which **direction** (RSI, MAs, 52wk), how **expensive** is vol (IV, IVR), and how **wide** to place strikes (ATR). Note one dead input: `iv_pct` is always `None` at the call site, so the IV-*percentile* branches never fire — routing is IVR-only (this is HELM-042's cleanup, folded into the re-base).

### 1b. Directional score — the legacy `bias_score` (−3…+3)

An integer directional read summed from three terms:

- **RSI:** < 30 → +2 ("oversold"), < 45 → +1, > 70 → −2, > 55 → −1.
- **Trend:** price > EMA20 > SMA50 → +1 (uptrend); the reverse → −1.
- **52-week:** ≤ 25% of range → +1 ("near low, mean reversion"); ≥ 75% → *flagged but scores nothing* ("near high — momentum," explicitly neutral).

The result is clamped to [−3, +3]. IV terms here are informational only — they annotate, they don't score.

*The tension, stated plainly:* this scorer is **internally split**. The RSI and 52-week terms are **mean-reversion**-coded — they reward *weakness* (oversold, near lows) as bullish, i.e. "buy the dip." The trend term is **momentum**-coded — it rewards *strength*. On a falling-but-oversold name the two fight; on a quiet uptrend the mean-reversion terms simply go silent and the score understates a real trend. This is the reason HELM-042 exists.

### 1c. Momentum score — the shadow (`momentum_bias`, new at s57, **not routed**)

A parallel score on the same [−3, +3] scale, computed alongside the legacy one for comparison, from existing features (MACD deferred):

- **MA stack:** price > SMA50 > SMA200 → +2 (bullish structure), the reverse → −2, partial → ±1; falls back to EMA20 > SMA50 when SMA200 is missing on short-history names.
- **RSI band:** 50–72 → +1 (healthy momentum, *not* oversold); > 72 → flagged, no points (extended); < 50 → −1.
- **52-week:** ≥ 75% → +1 (near high = momentum); ≤ 25% → −1.
- **ATR over-extension guard:** if price is > 3 ATR above SMA50, −1 — don't chase a parabolic move.

It **inverts** the legacy RSI and 52-week logic (reward strength, not weakness) and adds real trend structure and a chase guard. A s57 eyeball probe (TGT / NVDA / PFE / OR) showed the two diverging in the intended direction — TGT +2 momentum vs 0 legacy (trending, near highs); PFE −2 momentum vs +1 legacy (a falling knife legacy scored bullish). It stays shadow-only until the paper corpus can score both ways.

### 1d. Strategy routing (`bias_to_strategy`) — where VRP actually lives

This is the crux. Routing keys on **IVR** (the volatility regime) first, then the directional score (the shape):

- **IVR ≥ 35 → rich → sell premium:** CSP (bullish), iron condor (neutral), credit spreads.
- **IVR < 15 → cheap → buy premium:** long call (directional), long straddle (neutral).
- **15–35 → moderate → defined-risk spreads** (bull put / bear call / diagonals).

The directional score then selects the *specific* structure within the regime (strong-bullish + cheap → long call; neutral + rich → condor; etc.). This is the VRP thesis operationalized: **IVR decides which side of variance you're on; the directional score decides the shape.**

### 1e. Conviction (`compute_conviction`, 0–100)

A strategy-aware confidence blend: directional strength (|score| / 2) mixed with IVR richness (for sellers) or cheapness (for buyers), weighted by strategy family. Range/neutral strategies weight IVR heavily; directional strategies weight score alignment. It's a display/ranking aid, not a gate.

---

## 2. Lifecycle — the verdict engine (`decision.evaluate`)

One book-agnostic function decides, for any open position: **hold, or close — and why.** It sums P&L from the per-leg marks (SHORT legs profit as price falls, LONG legs as it rises), loads that strategy's levers from `strategy_settings`, and returns one of four reasons or `None` (hold).

The levers (defaults, overridable per strategy in the DB):

- **profit_target_pct = 0.50** — fraction of credit (or of max profit) to capture.
- **stop_loss_multiplier = 2.0** — loss stop at N × credit.
- **dte_exit_threshold = 21** — calendar management line.

Then it branches by **management family**, and this differentiation is where the options knowledge shows:

- **CREDIT** (CSP, iron condor, credit spreads, jade lizard): take profit at 50% of credit; **stop** at 2× credit, capped at max loss. The only family with a price stop. Stop is A/B-suppressible (see §4).
- **LONG_DEBIT** (long call / put): profit target as a % gain on premium; **no stop** — max loss is the premium already paid, so a price stop is redundant. Otherwise exit on the calendar.
- **DEBIT_SPREAD** (bear put / bull call): profit target measured against **max profit**, not the debit (it's a defined-reward structure); no stop.
- **COVERED** (covered call): profit on the call credit; no stop (the stock leg is external, and a rally just means capped-gain assignment).
- **LONG_VOL** (long straddle): **calendar only — no profit cap, no stop.** The convex tail *is* the edge; capping it would defeat the position.
- **DIAGONAL** (PMCC / diagonals): manages off the **back (long) leg's** DTE — the structure is only "near expiry" when its defining leg is.

Finally, the **calendar overlay** applies to all (if no profit/stop reason already fired): DTE ≤ 0 → **EXPIRY**; DTE ≤ 21 → **DTE_MANAGE**. Priority order is profit/stop first, then calendar.

*Why 21-DTE and 2×, not tighter price stops:* the design belief is that **time-based management controls tail loss better than tightening price stops**, and that **over-managing losers is the dominant premium-selling error**. Exiting at 21 DTE sidesteps the gamma/assignment danger zone without whipsawing on noise. It's a defensible, literature-aligned stance — and the stop A/B experiment exists precisely to test it rather than assume it.

---

## 3. Health — what the trader sees (`verdict.py` / `health.py`)

Between entry and exit, `/health` renders a colored verdict per position via `band_for(reason, evidence)`:

- **The `reason` from `evaluate` owns RED and every action state** (STOP → RED "close or roll," EXPIRY → RED "act now," etc.). This keeps the displayed verdict and the auto-manager's actual decision on one engine — no divergence.
- **Evidence can lift a HOLD to YELLOW but never to RED** (an explicit invariant). Thin buffer, ITM short strike, underwater P&L, or condor proximity raise attention without asserting action.
- **Condor proximity** (`condor_proximity`, v1a): how far spot has traveled from the body's center toward a tested short strike (0 = safe, 1.0 = at the strike, >1.0 = breached). Prep at 50%, manage at 75%. This killed a real false-green where a P&L-only read painted the most-tested condor green.
- **RED act-reason** (v1b, s57): a RED condor headline now names the tested side and depth ("…short put past strike (118%)").

*The gap:* health is **P&L + structure only — there are no live greeks** (delta/gamma/theta/vega come back `None` on condor legs). A position can be delta-dangerous before P&L reflects it. HELM-043 steps 3–4 (populate greeks, then a trajectory layer) address this but are gated on the IBKR feed.

---

## 4. Learning — the instrumentation

The point of the paper book and the capture columns is to eventually answer "do these levers actually produce risk-adjusted expectancy?" with data:

- **Entry features + exit outcomes** are recorded per position, so entry-lever effects and exit-lever effects can be separated.
- **Shadow captures** run counterfactuals without acting: the momentum score (HELM-042), a long-debit stop signal (HELM-031), and a **stop A/B** across five arms — no-stop / 50% / 75% / 2× / 3× (HELM-030) — recording what each *would* have done.
- The **paper book ranges wider than HELM's own screening taste** on purpose, so the corpus isn't just confirming the live book's biases.

The honest status: the corpus is thin — ~18 genuine closes, **zero real STOP closes** yet. Track A (the exit-lever scorecard) is held on corpus maturity, not effort. The measurement apparatus is built; the measurements aren't in.

---

## 5. What I think

**What's genuinely good.**

- The **VRP spine is coherent.** The IVR gate cleanly separates the buy-vol and sell-vol regimes, and routing operationalizes the thesis rather than gesturing at it. This is the strongest part of the model.
- The **exit engine is principled and family-aware.** The differentiation — straddle uncapped, debit spread measured against max profit, covered call never stopped, credit family the only one with a price stop — reflects real options understanding, not a one-size rule bolted onto every structure.
- The **21-DTE / anti-over-management discipline** is well-grounded, and crucially it's held as a *hypothesis under test* (the A/B), not dogma.
- The **engineering culture is unusually disciplined** — shadow-before-flip, corpus-gated authoritative changes, guarded idempotent patches. The system is built to *learn before it commits*, which is exactly right for something managing real money.

**Where I'd push.**

1. **The legacy entry scorer contradicts itself** (mean-reversion RSI/52wk vs momentum trend). The momentum re-base is the correct fix and is now in shadow — good — but until it's proven and flipped, live routing is still fed by a split signal.
2. **Direction may matter far less than the model implies.** The register's own read is that the binding constraint is vol-cost (IVR), not direction — most momentum candidates were already IVR-rich. If that holds, the directional scorer is close to noise for strategy selection, and nearly all the entry edge lives in the IVR gate. Worth confirming, because it would refocus tuning effort away from the directional scorer and onto IVR sourcing/freshness.
3. **The health read is structurally blind to greeks.** A condor can be delta-dangerous before P&L moves; proximity (v1a) is a good greeks-free proxy, but it's a proxy. This is known (HELM-043) and feed-gated.
4. **The credit stop looks inert at live sizes (HELM-030).** If the 2× stop rarely fires before max loss at real IRA position sizes, the exit rail's stop isn't protecting live positions the way the settings imply. Of everything open, this is the one closest to actual money and the least "gated" — I'd size it next.
5. **The edge is unproven.** This is the honest headline: **the architecture is more mature than the evidence.** HELM is a well-formed VRP hypothesis with a genuinely good measurement rig around it — but with ~18 closes and no real STOP fills, it hasn't yet earned the claim that these specific levers produce positive risk-adjusted expectancy. That's the *right* order (build the measurement before trusting the signal), and it means confidence in the model should stay provisional until the corpus speaks.

**Net:** a thoughtfully designed, internally coherent premium-selling system whose weakest link is not its design but the amount of real outcome data it has run through itself. The most valuable near-term work isn't adding signal — it's the things that let the corpus mature honestly (persisting the shadow scores, fixing the inert stop, getting greeks into the health read) so that when Track A finally runs, it's grading a clean book.
