# HELM Data Model
*Version 1.0 — 2026-05-23*

## Overview

HELM's data model is built around three core principles:

**Strategy is first-class.** Every position knows what it is — CSP, Iron Condor, PMCC, etc. Strategy shapes the entry snapshot fields that are captured, the thresholds the health engine uses, and the lifecycle options presented to the trader. It is never an afterthought.

**Legs are native.** A position is a container that always holds one or more legs. A CSP is one leg. A Bull Put Spread is two. An Iron Condor is four. The data model understands this relationship natively — it does not bolt multi-leg on via group IDs or external joins.

**Lifecycle is append-only.** Positions evolve. Rolling creates a lifecycle event and a new position linked to the original — it never mutates old records. The full history of a position is always recoverable.

---

## Tables

### `accounts`
Broker accounts. HELM can manage multiple accounts simultaneously.

### `positions`
The central entity. One row per trade, regardless of how many legs it has.

Key fields:
- `strategy` — one of 11 strategies, validated by CHECK constraint
- `status` — OPEN | CLOSED | EXPIRED | ASSIGNED | ROLLED_OUT
- `parent_position_id` — links a rolled position back to the original, preserving the full roll chain
- `max_loss` — null for undefined-risk strategies (Short Strangle, naked positions)
- `credit_exceeds_width` — Jade Lizard structural integrity flag

### `legs`
One row per leg within a position. Cascade-deletes with the position.

- `leg_role` — SHORT_PUT | LONG_PUT | SHORT_CALL | LONG_CALL | LONG_STOCK | SHORT_STOCK | LONG_LEAPS
- `direction` — LONG | SHORT (redundant with role, kept for query convenience)
- Stock legs (for Covered Call, PMCC) use `option_type = 'STOCK'`, no strike or expiration

### `entry_snapshots`
Captured once at position open. Never updated. This is the "independent variable" for all outcome analysis.

Contains the full market context at entry: spot price, IV rank/percentile, all four Greeks (delta, gamma, theta, vega), DTE, days to earnings, premium collected, skew, and a JSON snapshot of the user's strategy settings at the time.

The settings snapshot answers the question: *"Did this trade follow my own rules when I entered it?"*

### `checks`
A structured health assessment of an open position. Not just raw data — the health engine evaluates the data against the user's strategy settings and produces:

- `health_flag` — GREEN | YELLOW | RED
- `action_signal` — HOLD | WATCH | ADJUST | CLOSE | ROLL
- `narrative` — plain-English explanation of what the data means for this trade

The narrative field is where HELM teaches. Over time, checks build a longitudinal record of how the position evolved, which feeds the outcome analysis engine.

### `lifecycle_events`
Append-only audit trail. Every significant event in a position's life is recorded here:
OPENED | ROLLED | CLOSED | ASSIGNED | EXPIRED | ADJUSTED | NOTE | CHECK_ALERT

Rolling a position creates a ROLLED event with `new_position_id` pointing to the replacement position. The original position status changes to ROLLED_OUT. The chain is always traceable.

### `strategy_settings`
User-configurable thresholds per strategy per account. Pre-populated with practitioner defaults at setup. Every threshold the health engine uses comes from here — nothing is hardcoded.

Key design: every entry snapshot captures a JSON blob of the settings that were active at the time of entry. This means future analysis can ask: *"When I deviated from my own delta rules, did outcomes suffer?"*

### `watchlist`
Tickers under consideration, with willing-to-own flags and thesis notes.

---

## The 11 Strategies

| Strategy | Legs | Risk | IV Environment | Primary Greek |
|---|---|---|---|---|
| CSP | 1 | Defined (collateral) | High | Theta |
| Covered Call | 2 (stock + call) | Stock ownership | Moderate-high | Theta |
| Long Call | 1 | Debit paid | Low preferred | Delta, Vega |
| PERM | 1-2 | Debit paid | Rising pre-earnings | Vega |
| Bull Put Spread | 2 | Defined | High | Theta |
| Bear Call Spread | 2 | Defined | High | Theta |
| Iron Condor | 4 | Defined | Very high | Theta |
| Diagonal Spread | 2 | Defined (debit) | Moderate | Theta (short leg) |
| PMCC | 2 (LEAPS + call) | Net debit | Low-moderate | Theta (short leg) |
| Short Strangle | 2 | Undefined | Very high | Theta, Vega |
| Jade Lizard | 3 | Defined upside, undefined downside | High | Theta |

---

## Key Design Decisions

**PnL is always computed, never stored stale.** The `realized_pnl` field on positions is only written at close. Unrealized PnL during a check is computed from current prices and stored on the check record, not the position.

**Settings are snapshotted at entry.** When a user changes their CSP delta threshold, past entries retain the settings that were active when they opened. This enables the analysis question: *"When I entered outside my own rules, what happened?"*

**Roll chains are non-destructive.** Rolling position A creates position B. A's status becomes ROLLED_OUT, A's lifecycle_events get a ROLLED record pointing to B, and B's `parent_position_id` points back to A. You can always reconstruct the full history of a wheel cycle or a diagonal's roll sequence.

**Undefined max loss is explicit.** `max_loss = NULL` is not missing data — it means undefined risk. The system treats NULL max_loss as a flag requiring appropriate position sizing warnings.

---

## Outcome Analysis Queries (future)

The schema is designed to support these questions from day one:

- *"What was my win rate by IV rank at entry for CSPs?"*
  → JOIN positions + entry_snapshots WHERE strategy='CSP' GROUP BY iv_rank ranges

- *"Do my Iron Condors perform better when net delta stays below 20?"*
  → JOIN checks + positions, analyze max delta_drift_warning vs realized_pnl

- *"What delta at entry correlates with best outcomes for covered calls?"*
  → JOIN entry_snapshots + positions WHERE strategy='COVERED_CALL', correlate delta with realized_pnl

- *"When I held past 21 DTE on spreads, how did outcomes compare?"*
  → JOIN lifecycle_events (CLOSED) + entry_snapshots, compute days_held vs realized_pnl

- *"How did my PERM trades perform when IV rank was above/below 40?"*
  → JOIN entry_snapshots + positions WHERE strategy='PERM', split by iv_rank threshold

---

## Files

- `helm/schema.sql` — DDL: all table and index definitions
- `helm/seed_defaults.sql` — practitioner defaults for strategy_settings
- `docs/DATA_MODEL.md` — this document
