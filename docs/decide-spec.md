# HELM — Decide Spec

*Design reference. Status: locked in design, not yet built. Captured 2026-06-11.*

## Purpose

HELM is a conversation-first options-trading co-pilot for a Fidelity IRA. The **decide** step is its heart: for each candidate name, two *independent* reads — Russ's and HELM's — are formed, reconciled into a decision, and both are tracked and scored over time. The goal is not only to pick trades but to build a **ledger** that measures whose judgment is better, and where — so trust in HELM's reads can be calibrated against evidence.

## Vocabulary — the read model

- **Read** — an independent assessment of one name.
- **HELM's read** — the engine's call from `scan`: direction + strategy + conviction grade.
- **Your read** (Russ's read) — your own independent call: direction + strategy.
- **The decision** — the reconciled result of the two reads: act or skip (and, if act, the committed direction + strategy).
- **The ledger** — the accumulated record of both reads and their outcomes; the basis for scoring Russ vs HELM over time.

## The flow

1. **Scan -> HELM's reads.** Scan runs the universe and produces a read per name (direction + strategy + conviction), ranked by directional strength (`-abs(bias_score)`, bullish tie-break). Stored and timestamped, but **hidden** from Russ.
2. **Your reads (blind).** Russ engages a subset of names and enters his own read (direction + strategy) **before** HELM's is revealed.
3. **Reveal + reconcile.** HELM's read is shown beside Russ's. Agreement or divergence is visible on both axes (direction, strategy). The reconciled output is **the decision**.
4. **Two parallel tracks** (below) carry reads forward.
5. **Ledger.** Both tracks feed outcomes back; reads are scored over time.

## Locked decisions

1. **Read richness** — both reads carry **direction + strategy**.
2. **Blind** — **hard**. HELM's read is computed and stored but not shown until Russ's read is committed.
3. **Scope** — **engaged names only**. A decide pass covers HELM-flagged names (by conviction) plus any Russ pulls in manually; the rest are implicit skips.
4. **Outcome depth** — **full P&L**, tied to the position lifecycle, with outcomes attributed to **both reads independently** for analysis/learning.

## The two tracks

**Real track — Russ's acted reads.**
Your read -> decision = *act* -> `open` builds the spec -> confirm -> place at Fidelity -> **position tracker** (`positions` + `lifecycle_events`) -> monitored to close -> **real P&L**.

**Paper track — HELM's parallel track.**
A HELM read is promoted to a **paper trade** when it is **high-conviction** *and* Russ did not take it — i.e. Russ either **never engaged** the name **or** engaged and **skipped** it. Paper trades run the **same lifecycle**, simulated, cradle-to-close -> **reference P&L**.

**Promotion gate** — only **high-conviction-strength** HELM reads qualify for the paper track (threshold knob — e.g. `abs(bias_score) >= 2`, or conviction grade *High*). This keeps the paper track from flooding.

## Counterfactual scoring

Decision: **capture-complete now, scoring engine later** — bounded by the paper track.

- v1 records both reads in full + realized P&L on real trades, and promotes HELM's high-conviction unrequited reads to the paper track.
- The reference-outcome *evaluation engine* (paper P&L at a horizon, for multi-leg strategies) can be built later.
- **Non-lossy requirement:** every read record must be self-sufficient for future scoring — capture direction, strategy, and **spot price + date at read time**. Market history persists, so scores can be backfilled — but only if the inputs were captured.

## Data-model mapping

Existing `signals` fields cover much of this:

- HELM's read -> `auto_bias` / `bias_score` / `reasoning`, `top_strategy`, `top_fit`, `conviction`
- Your read -> `user_bias_override`, `russ_intent` (+ a strategy field — likely new)
- The decision -> `confirmed_bias`
- Outcome -> `russ_action`, `spec_match`, `spec_delta`, + P&L via the position lifecycle

Likely **new** storage needed:

- A strategy field on Russ's read.
- Paper-track positions (new `paper_positions` table, or a `mode` flag on `positions`).
- Spot-at-read-time + read-date capture, for counterfactual backfill.
- A distinction between **active skip** (engaged, passed) and **never engaged** (silence).

## Open items (to define before / at build)

- Exact high-conviction threshold for paper promotion.
- Paper-track storage shape (table vs flag).
- Reference-outcome horizon + evaluator (deferred).
- Where Russ's-read strategy lives in the schema.
