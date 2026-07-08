# helm check — per-strategy redesign

Status: design approved 2026-07-08. Tickets: HELM-073 (data prerequisite), HELM-074 (renderer). Source: conversation-driven audit of live helm.db + rendered mockup.

## Problem
The no-flags `helm check` prints one flat table sorted alphabetically by strategy, forcing CSP, credit spreads, iron condors and long calls into one CSP-shaped column set — buffer/strike are blank for the 8 multi-leg / long rows, and a single "strike" misrepresents a 4-leg condor. Danger is a 3-tier health stoplight that never reaches RED even for CSPs down >150% of credit and deep in-the-money, so triage collapses to healthy / not-healthy. There is no portfolio total. And a buffer%-only danger read is not volatility-normalized, which is precisely the blind spot the high-vol spec-name CSPs exploited.

## Principle
Instrument panel over stoplight. Surface the data points that drive lifecycle decisions and group by strategy so each shows its own gauges. Percentages stay for the profit axis (share of credit/max captured); the danger axis is delta-led, with buffer shown alongside.

## What experienced traders watch (research, 2026-07)
Short premium (CSP / BCS / IC): percent of max/credit captured, ~50% the take-profit convention; short-strike delta (~0.20–0.30 at entry, 0.30–0.40 = defend, "delta doubled" = trouble) as the volatility- and time-normalized danger read; DTE with the 21-DTE gamma line as a hard manage/roll marker. Spreads add a hard stop near 2x credit (loss ~= 1x credit). ICs add the tested-side concept (defend the side whose short delta is climbing). Roll only for a net credit; 2–3 rolls max.
Long calls: delta (0.70–0.80 to behave like stock), extrinsic value remaining (the premium bleeding, long-side analogue of the seller's edge), breakeven (strike + debit), DTE (LEAPS roll ~6 months out); take ~50% profit in <50% of time.

## Data audit — helm.db, 2026-07-07, 24 REAL open, greeks_source ibkr-live

| Field | Where | Status |
|---|---|---|
| basis (credit/debit) | positions.net_premium | stored, 24/24 |
| breakeven | positions.breakeven_low/high | stored 5/24 (defined-risk only); derive CSP = K − credit/sh, LC = K + debit/sh |
| percent of max / kept% | positions.max_profit/max_loss (5/24) + checks.pnl_unrealized (live) | derive: CSP pnl/credit; spreads & IC pnl/max_profit |
| short-strike delta | checks.delta (single-leg, live); per-leg greeks fetched live every run | display reads live at render (no write); persisting per-leg greeks to leg_checks is snapshot-side, deferred -> HELM-073 |
| extrinsic | not stored; leg_checks.current_price 479/479 + checks.spot_price | derive: mark − intrinsic |

Sign smell: CRM (BEAR_CALL_SPREAD) checks.delta = +0.38 where negative expected. HON position delta NULL.

## Column spec (per group)
Tags: [live] present now · [derive] one-liner off stored · [pending] needs HELM-073.

CSP — sort by absolute Δ descending:
ticker · dte (21-flag) · Δ put [live] · buf% strike = (spot−K)/spot and buf% breakeven = (spot−BE)/spot [live/derive] · kept% = pnl/credit [derive] · extrinsic = mark−intrinsic [derive] · P&L [live] · credit = net_premium [live] · breakeven = K − credit/sh [derive]

BCS (bear call) — sort by kept% ascending:
ticker · dte (21-flag) · Δ call [live, partial] · dual buffer · kept% = pnl/max_profit [derive] · P&L · credit · width · breakeven [stored]. Note: kept% of −100% is the ~2x-credit stop.

IC — sort by buffer-to-tested ascending:
ticker · dte (21-flag) · tested side (live per-leg Δ at render, RTH; nearest-strike fallback off-hours) · dual buffer to tested · net Δ [live] · kept% = pnl/max_profit · P&L · short put/call strikes · breakevens lo–hi.

LONG_CALL — sort by Δ ascending:
ticker · dte · Δ [live] · spot vs strike (itm/otm) + buffer-to-breakeven · extrinsic (bleed) · P&L · debit · breakeven = K + debit/sh · IV.

## Portfolio pulse (header)
open P&L total · positions up/down counted by P&L (not health) · concentration of the largest correlated cluster.
As of 2026-07-07: open P&L −$45,287 · 3 up / 21 down (the old view called 6 "green" on health) · 5 spec-name CSPs (IREN, OKLO, RKLB, IONQ, SMR) = 57% of drawdown (−$25,931).

## Thresholds → config
delta bands <0.30 / 0.30–0.60 / >=0.60 · 21-DTE gamma flag · kept% take-profit 50%. All trader-tunable; spec names may warrant tighter.

## Buffer decision
Show both buffers, stacked: distance to short strike (primary — the assignment line, pairs with delta) and distance to breakeven (secondary — the actual loss line, reads a few points looser).

## Capture vs display (corrected 2026-07-08)
`helm check` is display-only (`check_one` persist=False) and writes nothing. The 3x-daily `helm snapshot` (persist=True) is the sole journal writer. Per-leg greeks are fetched live on every run (`fetch_ibkr_option`) but the snapshot's `leg_checks` INSERT drops them — a snapshot-writer gap (HELM-073), not a display gap.

## Deploy plan (phased)
1. HELM-074 (display) — unblocked, needs no writes. Four grouped renderers + derived columns + pulse header; per-leg / tested-side delta comes from the live fetch at render time (RTH; dash off-hours). Ship all four groups.
2. Edge cases: null max_loss (undefined-risk CSP -> kept% off credit) · off-hours greeks -> dash · IC tested side from live per-leg delta (RTH), nearest-strike fallback off-hours.
3. Keep book_filter default REAL (existing).
4. HELM-073 (deferred, snapshot-side) — enrich the `leg_checks` INSERT to persist per-leg greeks for the learning corpus, and decide the `checks.delta` sign convention (net position delta). Do only when next opening the snapshot writer; not required for the display.
