# HELM — Register  (issues · parking lot · status)

Canonical list of known issues, tech debt, deferred work, and open questions.
Referenced by session handovers. Keep entries **terse**; detail lives in the
session where the issue was worked.

**Conventions**
- IDs are stable (`HELM-NNN`); never reuse a retired number.
- Severity: `BUG` (wrong behavior) · `DEBT` (correct but accruing risk) ·
  `DESIGN` (architecture/sequencing) · `OPS` (operational/runtime) ·
  `DOCS` · `QUESTION` (unresolved unknown).
- Status: `OPEN` · `DEFERRED` (deliberate, with a trigger) · `RESOLVED` · `WONTFIX`.
- On resolution: move the line to the **Resolved log** with a one-line outcome + date.

_Last updated: 2026-06-20 (s27)._

---

## Status — where HELM is
_Snapshot; refreshed each `helm checkpoint`, read via `helm status`._

- **Phase:** scaffolding complete (live book · paper book · edge instrument). Watchlist is a deliberate 65-name `core_v1` universe; paper book clean-slated at the s26 regime-break. Learning loop still the frontier — corpus accumulating fresh on the clean universe, with the neutral long-vol (straddle) cell live and REAL opens now stamping their originating signal end-to-end.
- **Next highest-leverage:** HELM-002 — the `schema.sql` constraints / defaults / FK pass (self-contained desk work, no live dependency). Quick-win alternative: HELM-025/026 enum hygiene (drop the off-limits tokens; reconcile `LONG_PUT`).
- **Blocked (market/RTH):** `core_v1` IVR backfill (Mon RTH — the 12 new names); HELM-019 Fidelity reconcile; HELM-018 RTH P&L sweep; first paper straddle books on the next RTH scan (HELM-011); HELM-012 first live signal-link on the next RTH REAL open.
- **Counts:** 11 active · 5 parked · last shipped s27 (HELM-011 straddle cell + HELM-012 signal link).

---

## Active

### Bugs

**HELM-026 · `BUG` · `OPEN` · `LONG_PUT` is wired to book but absent from the canonical enum + CHECK**
`_PAPER_BOOKERS` maps `LONG_PUT` → `paper_open_one` and the `'buy'` family / `_paper_generate` docstring reference it, but `LONG_PUT` is in neither `STRATEGIES` nor the `positions`/`strategy_settings` CHECK. A `LONG_PUT` paper open would be rejected by the CHECK (caught as an atomic skip, so no corruption — but the cell is silently un-bookable). Fix: add `LONG_PUT` to the enum + CHECK (mirror of HELM-011 locus 1), or strip its booker wiring if it is not a target strategy.

### Tech debt

**HELM-025 · `DEBT` · `OPEN` · Off-limits strategies are live tokens in the canonical enum**
`SHORT_STRANGLE` and `JADE_LIZARD` sit in `STRATEGIES` (and the `positions`/`strategy_settings` CHECK) despite being confirmed off-limits (undefined-risk, IRA-ineligible). `bias_to_strategy` never emits them, so no live exposure today — but a token that passes the CHECK can be booked. Fix: drop them from the canonical set + CHECK, or add an explicit `off_limits` guard at the open path.

**HELM-002 · `DEBT` · `OPEN` · `schema.sql` not yet a fully faithful builder of live**
Additive table/column drift **reconciled s23** (see Resolved log): `schema.sql` now declares
all 20 live tables and all live columns; a fresh `init_db` reproduces live (validated by
executing the patched `schema.sql` into an in-memory DB and diffing to zero). **Remaining
(keep `OPEN`):** a deeper pass on constraints / defaults / FKs beyond table+column+index
presence. (Index drift + dead `shadow_*` drop reconciled s25; the live reverse-gap of 6 `positions` indexes is tracked as HELM-021.) Trigger: before any DB rebuild-from-schema, or when
convenient. Keep the execute-and-rediff gate (`apply_schema_reconcile.py`) as the standing
schema-change check.

**HELM-004 · `DEBT` · `DEFERRED` · Multileg paper liquidity capture not wired**
`_paper_open.py` leg dicts don't carry `oi`/`spread`/`spread_pct`, so multileg paper
writes those three `entry_snapshots` columns NULL. The `capture` fn and both helpers
are wired to accept them (s20); the remaining work is enriching each `_paper_open.py`
builder from the per-strategy `evaluate_*` keys, plus deciding short-leg vs
net-structure liquidity. Trigger: the thin-name thematic sleeve, where the signal
stops being muted.

**HELM-021 · `DEBT` · `OPEN` · Live `positions` table missing 6 declared secondary indexes**
The builder declares six `positions` indexes (`idx_pos_account` / `ticker` / `strategy` /
`status` / `opened` / `signal`) that the **live DB does not have** — surfaced s25 by diffing
the live index set against a `/tmp` build of `schema.sql` (builder 34, live 30; the 6 are the
reverse gap). `positions` is the hot table (every lookup, `reconcile`, `analyze edge`) and runs
unindexed live. Not a builder bug — a live-DB deficiency. Fix: a **gated live `CREATE INDEX`
pass** (read-only probe → `/tmp` validate → backup → live → verify baseline), not a `schema.sql`
edit. Trigger: before scale, or when convenient.

### Design / sequencing

**HELM-023 · `DESIGN` · `DEFERRED` · Learning / look-back layer (the endgame)**
The core purpose: use the PAPER counterfactual corpus to score and tune HELM's entry/exit levers
against live picks — selection skill, pass-cost, and the boundary/cell choices (`bias_to_strategy`
thresholds, the neutral-sub-rich cell HELM-011 reserves for exactly this). Distinguish entry-lever
from exit-lever learning; target the variance risk premium for premium-family strategies. Trigger:
HELM-005 breadth landed **and** the PAPER book has closed trades to score. Gated by HELM-005 (the
corpus must range wider than HELM's screening taste) and calendar time (positions must close).
Sub-threads land here as the loop takes shape.

### Ops / enhancement

**HELM-006 · `OPS` · `OPEN` · Scan trusts stale IVR silently**
Scan output shifts materially on stale vs fresh IVR (s20: the first scan's
monoculture and a false NEE anomaly were both stale-IVR artifacts; both corrected
after `helm ivr refresh`). Candidate enhancement: scan warns or refuses when IVR
data is stale, so it can't silently mis-assign strategies.

**HELM-009 · `OPS` · `OPEN` (suspected, unconfirmed) · No per-ticker timeout in paper generate**
First `paper generate` run (2026-06-16) showed `entry_snapshots.created_at` in two bursts —
AAPL→GE at 14:45:03–08, a ~45-min gap, then GOOGL→XOM at 15:30:17–59 — with the gap at the first
IBKR single-leg fetch (GOOGL). Single-run-with-stall is the leading read (summary said "booked 20",
exactly 20 rows exist, no dups), but run count could not be confirmed. If it's a stall, the
orchestration's try/except catches exceptions but not hangs, so one slow IBKR chain blocks the
batch. Candidate fix if confirmed: per-ticker fetch timeout → surface a stuck request as a skip,
not a stall.
_2026-06-17 (s22) — one RTH `helm paper generate` completed with no ~45-min single-leg stall in
visible output. One clean data point, not conclusive; keep watching across runs before deciding
on a per-ticker timeout._

**HELM-019 · `OPS` · `OPEN` · Stale frozen marks → wrong multi-leg P&L when market closed**
Outside RTH, `helm check` on multi-leg positions reads `ibkr-frozen` last-close marks that are
stale/noisy on thin OTM wings, so net P&L and any profit-target/stop signal off it can be
materially wrong. Not a calc bug — HELM-018's net math is correct; garbage-frozen-in. Freshly
booked WELL IC read +$80 vs Fidelity ~-$2,300 (~$2,400 gap); frozen MCD +$760 vs Fidelity
~break-even. Fix: prefer live marks; tag frozen P&L low-confidence in `helm check`; build a
HELM-vs-Fidelity mark/P&L reconcile (oracle = Fidelity CSV value + gain/loss). Re-validate
WELL/MCD next RTH. (Sibling of HELM-006.)
_v1+v1.1 shipped (2026-06-19, s24): `helm check` compact + condor deep views gate frozen/stale
marks — no profit-target/stop close off non-live data; P&L shown + tagged, capped YELLOW,
"confirm at RTH"; DTE + zone signals untouched. Remaining: the HELM-vs-Fidelity mark/P&L
reconcile (oracle = Fidelity CSV value + gain/loss)._
_Deferred (weakest-leg) — `check_one`'s leg_marks loop (`check_cmd.py` ~L617–626)
stores only each leg's mid and discards its source, so v1 confidence uses the primary
leg's `opt_source` as a market-state proxy (live / frozen / stale). Stamp per-leg source
there when that loop is reworked; pairs with the carried "mid-only fast fetch for hedge
legs" (HELM-018 follow-up)._

### Docs

**HELM-007 · `DOCS` · `OPEN` · Stale help / docstrings**
`paper_cmd.py` help says "single-leg" but `_PAPER_BOOKERS` books multileg too;
`workflow_cmd.py` is stale (missing `--manage` / paper, per handover). `_paper_generate.py`
(L32–35) still describes `open_position_with_snapshot` as non-atomic — stale since the s24
atomic-open fix (HELM-003). Pattern of docstrings lagging implementation.

### Open questions

**HELM-008 · `QUESTION` · `OPEN` · Provenance of `entry_snapshots` liquidity columns**
`open_interest` + `bid_ask_spread` were found on live with no `schema.sql` or code
ALTER trail; could not establish when/how they were added. Benign (correct types,
were unpopulated). Likely a prior partial/ad-hoc migration. Unresolved; not blocking.

---

## Resolved log

- **2026-06-20 (s27)** — **HELM-012 RESOLVED (pending first live link) — originating-signal stamp on REAL open.** Root cause was threefold: (D1) the link required `russ_intent='OPEN'`, a mark the scan→open flow never sets, so it never fired; (D2) the match was ticker-only, no strategy filter; (D3) the multi-leg writer never called the link at all — condors/straddles/diagonals could never link. Rewrote `Signal.link_position_opened(ticker, strategy, position_id)`: drops the intent gate, matches the latest unlinked signal for the ticker, links only when `top_strategy` equals the opened strategy (a deliberate exception stays unlinked), and wires **both** sides in one txn — `signals` (position_opened/position_id/russ_action='OPEN') and `positions.signal_id` (the field `close_cmd` reads for back-prop, which the old code never set). Threaded `strategy` into the single-leg call; added the best-effort block to the multi-leg writer. Code-only (all columns already present); `/tmp` contract validation green before apply (match→both sides, mismatch/no-signal→unlinked); `import OK`. Patch `apply_helm012_signal_link.py` (anchor-asserted, compile-gated, .bak per file). First real link lands on the next RTH REAL open. Unblocks the REAL side of HELM-023 back-prop. Commit `3c3403f`.
- **2026-06-20 (s27)** — **HELM-011 RESOLVED — straddle paper cell lit end-to-end.** Neutral + cheap-IVR → `LONG_STRADDLE` was already emitting signals (config, `evaluate_straddles`, live `helm open` dispatch, and the `bias_to_strategy` entry trigger were all pre-built — code had outrun the register); the only real gap was the paper booker. Shipped: DB token + CHECK widening on `positions`/`strategy_settings` via `writable_schema` (`helm011_a`/`_b`); `paper_open_straddle_one` — two LONG legs, ATM strike, same expiry, both filled @ ask → net debit — plus `_PAPER_BOOKERS` registration and `call_ask`/`put_ask` exposed in `evaluate_straddles` (`helm011_c`); long-vol exit guard in `paper_manage` (skip credit-family PT/stop for `LONG_STRADDLE`, DTE/EXPIRY-only) + a `strategy_settings` row (`dte_exit=21`, no PT/stop) (`helm011_d`/`_e`). IVR-boundary sub-question decided: leave the 35/15 lines untouched (trigger already produces ~2/wk). First booking lands on the next RTH scan; the 14 pre-`core_v1` straddle signals stay unbooked (pre-regime-break, old universe). Patches `helm011_a..e` (guarded). Commit `9c4764f`.
- **2026-06-20 (s26)** — **HELM-005 RESOLVED (reframed) — `core_v1` cull.** The monoculture wasn't a narrow watchlist: bare `helm scan` runs the `active` set, which had silently grown to 60 uncurated names (75% of signals from 156 thematic non-core tickers — the "benched" themes were never benched). Data-only fix: re-culled `active` to a deliberate 65 (53 quality + 12 directional-diversity adds — DHI PNC DAL FDX DE GM SLB NEM NUE TGT DG O), tagged `core_v1`, benched the rest (preserved, dormant). `active` is now the single source of truth for the scan universe; `build` is a label only. Verified 65 active / 65 core_v1 / 41 REAL untouched / paper emptied. Patch `patch_core_v1.py` (guarded). Commit `469c3cc`.
- **2026-06-20 (s26)** — **Paper clean slate.** Soft-voided the 14 open PAPER positions (→ CLOSED) so the corpus restarts on the clean 65; the `core_v1` cull date is the regime-break line for the learning layer. REAL book untouched. Commit `469c3cc`.
- **2026-06-20 (s26)** — **HELM-024 found + fixed — `helm watchlist add` crash.** `WatchlistItem` dataclass field `active: int = 0` collided with the classmethod `active(cls)`; @dataclass captured the method as the field default, so fresh items got `self.active = <bound method>` and `save()` raised `type 'method' is not supported`. Latent since the `active()` fetcher landed (rows had arrived via screen/build/import). Fix: renamed classmethod `active` → `active_universe` (sole caller `scan_cmd.py`); mechanical rename, no behavior change. Patch `fix_active_collision.py` (guarded). Commit `469c3cc`.
- **2026-06-20 (s25)** — **HELM-016 code landed** (Cluster D). Correction to the s24 entry
  below: the `analyze edge` command (`cmd_edge` + `_edge_*` helpers, ~174 lines, `cli/analyze.py`)
  was **never committed in s24** — it sat uncommitted in the working tree, and the "no code
  change" resolution mistook on-disk state for shipped. Committed s25. Clean run verified: 20
  graded closed trades; LONG_CALL mean **883.2%** reproduces (real, not a units bug); BSX
  COVERED_CALL correctly flagged ungradeable (no `stock_positions` capital basis); selection
  skill 0.0% as expected while PAPER has no closed trades. The median / EXPIRED / ungradeable-
  audit / LONG_CALL-basis follow-ups were present as s24 described — just not on `origin/main`
  until now. (Note: COVERED_CALL edge stays ungradeable until `stock_positions` is populated.)

- **2026-06-20 (s25)** — **HELM-022** opened + resolved: `paper generate` now skips tickers
  already open in the **REAL** book (`_paper_generate.py`, `_open_real_tickers()` + skip-with-
  reason "live ticker - open in real book"). Keeps a name out of both books at once, so the
  picks-vs-field edge comparison (`analyze edge`) isn't confounded by a ticker living in REAL
  and PAPER simultaneously. Cluster C — s24 working-tree orphan, now committed.

- **2026-06-20 (s25)** — **HELM-002** index reconcile + `shadow_*` drop shipped (Cluster B —
  the s24 working-tree orphan, never committed). Forward-index gap closed: `idx_ptx_hash` /
  `idx_ptx_date` (present live, undeclared) added to the reconcile block; the builder now
  produces all 30 live indexes, proven by a `/tmp` build + index-set diff
  (`apply_s25_index_reconcile.py`). Dead `shadow_positions` / `shadow_marks` confirmed gone
  live and dropped from the builder. HELM-002 narrowed to a constraints / defaults / FK pass;
  the reverse gap (6 builder-declared `positions` indexes absent live) spun out as **HELM-021**.

- **2026-06-19 (s24)** — **HELM-016** resolved (`analyze edge` v1.1). All four deferred
  follow-ups verified done. (a) **median** is reported alongside mean — summary-table column,
  per-strategy `mean/med` cells, and a median selection-skill line (`cli/analyze.py`,
  `_median`/`cmd_edge`). (c) the **ungradeable audit** itemizes every skipped trade
  (ticker/strategy/reason). (b) **EXPIRED** trades fold in via the query guard
  `status IN ('CLOSED','EXPIRED') AND realized_pnl IS NOT NULL`. (d) **LONG_CALL capital
  basis** confirmed against the live book — all five closed long-call rows (APP, UNH×2, UEC,
  CRWV) have `net_premium` = −(open_price × contracts × 100) to the dollar, so
  `abs(net_premium)` is total-dollar matching `realized_pnl`; the 883% row is a real
  annualized figure, not a units bug. (a)/(b)/(c) were already in the code — the register's
  "deferred" label was stale; no code change this session.
- **2026-06-19 (s24)** — **HELM-020** resolved. (1) `cmd_check_deep_iron_condor` now uses the
  position's ticker, not a hardcoded "HON" label (it was printing the wrong ticker on every
  non-HON condor deep view — WELL, MCD). (2) Removed the dead, shadowed `generate_guidance`
  duplicate — an exact copy sitting before `cmd_check_deep_iron_condor`; only the
  post-`cmd_check_deep` def ever ran (positional delete, one copy remains). Patches
  `apply_helm020_hon.py`, `apply_helm020_deadgg.py`.
- **2026-06-19 (s24)** — HELM-019 frozen-mark confidence shipped (v1 + v1.1). `helm check`
  derives `mark_confidence` (live/frozen/stale) from the primary `opt_source`; non-live marks
  can't drive a GREEN profit-target or RED stop (compact) or the condor deep-view "close and
  redeploy" verdict — P&L shown + tagged, capped YELLOW, "confirm at RTH". DTE + zone signals
  untouched. Patches `apply_helm019_v1.py`, `apply_helm019_v1_1.py`; live-validated on WELL/MCD.
  Remaining under HELM-019: the HELM-vs-Fidelity mark/P&L reconcile.
- **2026-06-18 (s24)** — WELL iron condor **backfilled** (live in Fidelity, never booked;
  reconcile showed 4 loose Fidelity-only legs). Recorded via one-off `book_well_condor.py`
  on the atomic writer — 4 legs, net credit $5,960, max loss $14,040, position opened
  2026-06-17, `pricing_source=fidelity`. First attempt failed on a null `spot_price` and
  rolled back cleanly (live proof of the HELM-013 atomic open); after the entry-spot fix,
  reconcile 20/20, integrity ALL CLEAR at 55 positions. (P&L read caveat → HELM-019.)
- **2026-06-18 (s24)** — **HELM-018** multi-leg P&L'd from a single leg **fixed**.
  `assess_position` priced only `opt_legs[0]`; now nets all legs, credit/debit signal from
  `net_premium` sign, `pnl_pct` over `net_premium`. Patch `apply_helm018_multileg_pnl.py`.
  Live RTH re-confirm + multi-leg sweep pending (carried).
- **2026-06-18 (s24)** — **HELM-003** non-atomic open **resolved**. Single-leg
  `open_position_with_snapshot` now wraps its 4 writes in one `transaction()` (best-effort
  Signal link kept outside); the multileg sibling was made all-or-nothing the same session.
  All four open paths (live/paper × single/multi) route through the two writers, so partial
  opens can no longer occur. Patch `apply_singleleg_atomic.py`.
- **2026-06-18 (s24)** — **HELM-013** live `confirm_condor`. `helm open <T> IRON_CONDOR
  --confirm` writes via the atomic multileg path (net-credit entry, short-leg reconciliation,
  `pricing_source=ibkr`); live-confirmed prompt + clean `n` exit. Patch `apply_confirm_condor.py`.
- **2026-06-18 (s24)** — **HELM-013** atomic multileg open. Conn-injectable models;
  `open_multileg_with_snapshot` threads one `transaction()` through Position/Leg/Lifecycle/
  snapshot; mid-sequence failure → 0 rows. Patch `apply_helm013.py`.
- **2026-06-18 (s24)** — `helm check --integrity` ratchet. 7-family invariant sweep
  (sign/role, leg-count, FK orphans, snapshot anchoring, dup-fills, coverage), fail-closed on
  unmapped strategies; cleared 32 orphaned SMR check rows in the same pass.
  Patch `apply_integrity_check.py` + `helm_orphan_checks_fix.py`.
- **2026-06-17 (s23)** — **HELM-017** fixed. `confirm_and_log` (`cli/open_cmd.py`) now stamps
  `selected["direction"] = config["direction"]` before `open_position_with_snapshot`, closing
  the class where single-leg longs inherited the `fetch_chain_from_ibkr` SHORT placeholder and
  persisted as `SHORT_CALL` / +credit. Patch `apply_helm017_code.py` (anchor-asserted,
  idempotent, py_compile-gated). Latent: the L531 SHORT placeholder is now harmless but remains.
- **2026-06-17 (s23)** — TSLA row correction + **HELM-012** relink (gated, Russ-executed). One
  txn, 11 fields across 4 rows: `positions` net_premium 3398 → -3398, signal_id → `SIG-78A351DC`;
  `legs` SHORT → LONG / `SHORT_CALL` → `LONG_CALL`; `signals` → `OPEN`/`OPEN`, position_opened 1.
  Script `helm017_data_fix.py` (drift-guarded, two WAL-safe backups, in-txn readback). Repairs the
  existing row only; source linkage still open under HELM-012.
- **2026-06-17 (s23)** — **HELM-015** resolved. Duplicate REAL SMR CSP row removed, keeping the
  one matching the Fidelity fill ($1.22): KEEP `SMR-CSP-20260603-5773F7`, DROP `...-694D73`
  (a stray re-booking 93 min later). Gated child-first delete `helm015_smr_dedupe.py` (drift guard
  refuses unless DROP=1.23 / KEEP=1.22; two WAL-safe backups; per-table rowcount asserts).
- **2026-06-17 (s23)** — **HELM-002** additive reconcile shipped. `schema.sql` brought to live for
  table+column presence: +4 `CREATE TABLE IF NOT EXISTS`, +6 `ALTER TABLE ADD COLUMN`, matching the
  file's CREATE+ALTER idiom. Self-sourcing, self-guarding `apply_schema_reconcile.py` with an
  in-memory execute-and-rediff gate (20/20 tables, zero column diff). Deeper constraint/index/FK
  pass + `shadow_*` drop remain under HELM-002.
- **2026-06-17 (s23)** — **HELM-014** resolved (premise corrected). `get_conn` (`db.py:18`) sets
  `PRAGMA foreign_keys = ON` per connection and is the only `sqlite3.connect` in the package —
  in-app FK enforcement is uniform. The s22/s23 CASCADE-didn't-fire was out-of-band scripts using a
  bare connect (FK OFF). Discipline: maintenance scripts route through `get_conn` or stay
  child-first. No app change.
- **2026-06-17 (s23)** — **HELM-010** resolved. Orphan `import_pathways` row `PTH-8E897BE6`
  (account_id `fidelity_5fee37`, a deleted account; `last_file` NULL, never imported) deleted via
  gated leaf delete `helm010_orphan_fix.py` (drift guard, two WAL-safe backups); `import_pathways`
  now 2 rows, 0 orphans, both on `fidelity_9e60c8`.
- **2026-06-17 (s22)** — `helm analyze edge` shipped (`cli/analyze.py`, additive). Per-trade score
  = annualized return on capital-tied-up (P&L ÷ capital × 365/days, 7-day floor), simple average,
  **closed trades only**, graded vs the whole field (REAL ∪ PAPER); reports selection-skill
  (picks − field) and pass-cost (paper), overall + by strategy, count + thin flag (N<5). First run:
  301.3% overall, CSP 107.3% (n=15), LONG_CALL 883.2% (n=5), 1 ungradeable. Patch `apply_edge.py`.
  v1.1 follow-ups → HELM-016.
- **2026-06-17 (s22)** — Paper-generate live-pick exclusion shipped (`cli/_paper_generate.py`,
  ticker-level, v3). Any ticker with an open REAL position is skipped on the paper side regardless
  of strategy, so the paper book never rides an underlying Russ is already live in. `seen`/`seen.add`
  dedup intact. Sandbox-proven incl. different-strategy-same-ticker. Earlier (ticker,strategy)-keyed
  v1/v2 superseded (v1 had a `seen`-rename `NameError`); deploy only v3.
- **2026-06-17 (s22)** — Live paper-book contamination cleaned. Two erroneous paper positions on
  live tickers (`TSLA-LONG_CALL-20260617-5DEB97` double-book + pre-existing
  `AAPL-BULL_PUT_SPREAD-20260617-2BC3A5`) removed via gated child-first txn: 9 rows. WAL-safe
  `.backup()` + keeper (`data/helm.db.predelete_20260617_150853.bak`), before/after verified zero,
  REAL book untouched (exact-id scoped).
- **2026-06-16 (s21)** — HELM-001 low-IVR-neutral/mildly-bearish → IRON_CONDOR
  fallthrough **fixed**. The moderate IVR band (15–34) no longer routes to a
  premium-sell: neutral → LONG_STRADDLE, mildly-bearish → BEAR_PUT_SPREAD; IC now
  fires only at IVR ≥ 35 (`ivr_rich`). Two-line edit to `bias_to_strategy`
  (`scan_cmd.py`) fallthroughs; cheap branches and the rich sell-line untouched.
  Closed offline via a direct `bias_to_strategy` ladder test (10/10 cells); live-scan
  confirmation rides along next RTH. (`scan_cmd.py.bak.20260616_201920`.)
- **2026-06-16 (s21)** — `helm guide` strategy matrix **re-based** to the engine's
  real IVR boundaries. The guide was built on a 35/60 scheme while `bias_to_strategy`
  sells at ≥35 / buys <15 — pre-existing drift independent of HELM-001 (the mildly-
  bull and mildly-bear 35–60 cells were already wrong). Columns moved to
  <15 / 15–35 / ≥35, all 5 rows re-derived from the engine, IVR table reconciled,
  RSI-conditional mildly-bull-moderate cell footnoted. (`guide_cmd.py.bak.20260616_204019`.)
- **2026-06-16 (s20)** — NEE "mildly-bearish → IRON_CONDOR" anomaly: was a
  **stale-IVR artifact**, not a bug. On fresh IVR (IVR 11) it correctly assigned
  BEAR_PUT_SPREAD. (The live low-IVR-neutral fallthrough remains — see HELM-001.)
- **2026-06-16 (s20)** — Entry-liquidity capture shipped: additive migration
  (`bid_ask_spread_pct` to live; `open_interest` + `bid_ask_spread` already present),
  plus `cli/entry_snapshot.py` wiring (signature + INSERT + single-leg pass-through +
  multileg helper forward-wired). Single-leg opens now populate the three columns.

---

## Parking lot
_Future aspirations and enhancements, un-numbered until promoted. On promotion: assign the next free HELM-NNN and move to Active._

- **HELM stages & workflow UI** — interactive graphic of HELM's development stages and operational loop (scan → decide → REAL/PAPER → manage → analyze). Productionize the s25 chat workflow diagram + dev-phase status into a navigable interface; build as standalone HTML (static file, or served at `helm.local`); doubles as onboarding. Why: at-a-glance orientation for where the system sits and how the loop runs.
- **COVERED_CALL gradeability** — populate `stock_positions` (underlying cost basis) so covered calls stop being skipped as "no capital basis" in `analyze edge` (surfaced s25, BSX). Why: every covered call is currently ungradeable.
- **`.gitignore` sweep** — ignore the working-dir pile (`*.bak.*`, `apply_*.py`, `helm*_fix.py`, `HELM_handover_*.md`, `ISSUES_*_additions.md`). Why: ~70 untracked files clutter every `git status`.
- **Setup / onboarding flow** — first-run config (watchlist, broker pathway, account) per the original "built after core strategies" intent. Why: currently assumes a hand-built DB.
- **`helm status` / `helm checkpoint` CLI** — `helm status` prints the Status block + active/parked counts (flag staleness when `_Last updated_` is old); `helm checkpoint` assists the close-out. Why: the chat triggers work today, the CLI verbs make them first-class.

---

## Carried threads · un-promoted follow-ups

Not yet promoted to numbered issues; pull in as they get worked.

**s24:**
- HELM-018 RTH confirm + multi-leg P&L sweep — re-run `helm check MCD` / `helm check WELL` at
  RTH (expect convergence toward Fidelity), then sweep all multi-leg positions.
- HELM-018 follow-ups — mid-only fast fetch for hedge legs (skip the ~8s greek wait); store net
  cost-to-close as `current_price` for multi-leg; integrity invariant: persisted `pnl_pct` > 100%
  on a credit structure → FAIL.
- Manual multi-leg booking command (`helm open <T> --manual`) — so backfills / exact-fill entries
  don't need one-off scripts. The atomic writer needs an `opened_at` override and a non-null
  `spot_price` path (entry-snapshot `spot_price` is NOT NULL — bit the WELL backfill).
- Reconcile UX — group unmatched Fidelity-only legs into a suspected structure ("WELL: looks like
  an iron condor, 4 legs, unbooked") instead of N loose `--` rows.
- Strangles hint typo — `display_strangles` "To open" line: `IRON_CONDOR --confirm` →
  `SHORT_STRANGLE --confirm`.
- Real booking via `--confirm` — condor + single-leg not yet exercised end-to-end with a live
  fill (RTH, real money; write path proven).
- Duplicate `'check'` key in `helm.py` (L17 dead `helm.cli.check` / L28 live `helm.cli.check_cmd`).

**Earlier (carried):**
three-way `STRATEGIES` constant collapse (`position.py`/`settings.py`/`setup.py`) ·
`confirmed_bias` not respecting `user_bias_override` · `to_ibkr_symbol()` wiring ·
`WatchlistItem.save()` schema derivation · BRK-B CSV mapping · `strategy_settings`
second-strategy CHECK unsynced · diagonal.py vestigial code · `paper_generate` RTH gate
edge cases · `STRATEGY_CONFIG` dup key · Russ-scan desktop interface · additional scan
metrics (expected move, IV/HV ratio, OI/liquidity, skew, ex-div, earnings move) ·
trust-handover staging model.

**s26:**
- Monday RTH: `helm ivr refresh` to backfill IVR on the 12 `core_v1` adds (they scan via the `ivr_unknown` score-only path until then).
- `helm ivr refresh` churns all 206 watchlist names, not just the active 65 — harmless, but scoping it to `active` is a small OPS nicety worth a future ticket.
- Uncommitted after this checkpoint: `patch_issues_s26.py` + `ISSUES.md` — commit on the usual explicit-named-files step; push separate. (Cull/fix landed in commit `469c3cc`.)

**s27:**
- WELL half-link cleanup — its signal is `russ_action=OPEN` but `position_id` NULL (the s24 backfill flipped the action without stamping the position side). One-line data fix to complete or reset the link; HELM-012 prevents recurrence going forward.
- Conviction not stored — `signals` has no `conviction` column; the scan's Low/Mod/High is derived at display (off `top_fit`/fit_score). HELM-023 will need a real source when it scores conviction.
- Working-dir clutter mounting — s27 alone added `validate_helm012_link.py`, `apply_helm012_signal_link.py`, `helm011_a..e`, and several `.bak`s. Promote the `.gitignore` sweep (parking lot) to a numbered issue.
- Uncommitted after this checkpoint: `ISSUES.md` (this register update) — commit on the usual explicit-named-files step; push separate. (HELM-012 code landed in `3c3403f`.)
