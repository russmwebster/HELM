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

_Last updated: 2026-06-22 (s31)._

---

## Status — where HELM is
_Snapshot; refreshed each `helm checkpoint`, read via `helm status`._

- **Phase:** scaffolding complete (live · paper · edge). `schema.sql` is now a faithful builder of live including constraints / defaults / FKs (HELM-002), guarded by a standing `diag_schema_constraints.py`; the hot `positions` table is indexed live (HELM-021). Learning loop still the frontier — corpus accumulating on the clean `core_v1` universe, neutral long-vol (straddle) cell live, REAL opens stamping their originating signal (HELM-012 wired, pending first live fire).
- **Next highest-leverage:** the OPEN backlog is essentially drained — what remains is deferred-by-design or RTH-paired: HELM-019 Part 2 (HELM `assess_position` vs the Fidelity oracle, divergence delta, RTH), HELM-004's remaining multileg legs (thin-name trigger), HELM-023 learning layer (corpus-gated). Parking desk picks: COVERED_CALL gradeability and the `helm status`/`helm checkpoint` CLI.
- **Last shipped (s31):** earnings awareness — `helm scan` refreshes `watchlist.next_earnings` (active universe, per-scan cap, stamp-on-success), every `signals` row carries `earnings_date`/`days_to_earnings`/`earnings_warning`, and the scan table shows an Earnings column. `helm open` precise-expiry line deferred by choice.
- **Blocked (market/RTH):** `core_v1` IVR backfill for the 12 new names (Mon RTH); HELM-019 stale-marks reconcile vs Fidelity; first paper straddle books on the next RTH scan; HELM-012 first live signal-link fire on the next RTH REAL open.
- **Counts:** 3 active · 4 parked · last shipped s30 (HELM-006 stale-IVR warn · HELM-004 credit-spread short-leg liquidity · HELM-019 Part 1 Fidelity-oracle column · `helm-servers.sh` retired).
- **Monday RTH readiness:** no blockers; running server already has all s30 code. Run `helm ivr refresh` early to backfill the 12 new `core_v1` names (else they score `ivr_unknown`). First live exercise of HELM-009 `RequestTimeout` on real opens — watch. HELM-019 stale-mark P&L self-heals once live marks return; the s24 no-close-off-stale-marks guard stands.

---

## Active

### Tech debt

**HELM-004 · `DEBT` · `DEFERRED` · Multileg paper liquidity capture not wired**
_Narrowed s30 (`e55a00b`): credit spreads wired — `paper_open_spread_one` stamps short-leg liquidity (oi + spread/spread_pct), long leg spread-only. Remaining: debit/condor/diagonal/straddle, deferred to the thin-name sleeve._
`_paper_open.py` leg dicts don't carry `oi`/`spread`/`spread_pct`, so multileg paper
writes those three `entry_snapshots` columns NULL. The `capture` fn and both helpers
are wired to accept them (s20); the remaining work is enriching each `_paper_open.py`
builder from the per-strategy `evaluate_*` keys, plus deciding short-leg vs
net-structure liquidity. Trigger: the thin-name thematic sleeve, where the signal
stops being muted.

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

**HELM-019 · `OPS` · `OPEN` · Stale frozen marks → wrong multi-leg P&L when market closed**
_Part 1 shipped s30 (`b49b7b5`): `helm reconcile` renders a per-position **Fid P&L** column (Fidelity Total Gain/Loss $ summed across a position's legs) — the broker oracle. Accessors (trailing-comma +1 shift): Current Value ← `Last Price Change`, Total G/L $ ← `Today's Gain/Loss Percent`. Part 2 (RTH): HELM `assess_position` `pnl_mtm` vs oracle + divergence delta; validate WELL/MCD live._
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

---

## Resolved log

- **2026-06-22 (s31)** — **Earnings awareness wired into the scan pipeline.** HELM was blind to earnings at entry: `watchlist.next_earnings` and the `signals` earnings fields were empty across the board, and `helm open` surfaced earnings only on PERM. Added `helm/earnings.py` (yfinance fetch plus `days_until`/`earnings_warning`, 45-day window). `helm scan` now refreshes `watchlist.next_earnings` for the active universe (per-scan cap of 12, oldest-first, stamped on success only — after an initial-burst yfinance throttle that the first run wrongly cached as fresh-but-null, fixed in 2b). Every `signals` row carries `earnings_date`/`days_to_earnings`/`earnings_warning`, and the scan table shows an Earnings column (MM-DD plus DTE, yellow inside window). The `helm open` precise-expiry line was deliberately deferred — earnings is now a visible factor at scan, which was judged sufficient. Caveat: yfinance occasionally returns a past date (COST), rendered as `--`. Patches 1/2/2b/3/4, helper plus scan_cmd plus _decision_capture.
- **2026-06-21 (s30)** — **HELM-006 RESOLVED — scan warns on stale IVR.** `fetch_technicals` copied the IVR value but discarded the record's age, so scan scored stale ranks as fresh (s20 monoculture + false NEE anomaly). Added `IVR_STALE_DAYS=3`, plumbed `ivr_date`/`ivr_stale` from `ivr_record.date`, a leading `⚠ IVR stale (as-of …)` bias chip, and a footer count. Warn-only — strategy assignment untouched; missing IVR stays the existing `ivr_unknown` path. (`ceebcb3`)
- **2026-06-21 (s30)** — **`helm-servers.sh` retired (parking lot).** The launchd-managed `com.helm.server` (KeepAlive) made the old heredoc launcher a foot-gun (Errno-48 + a fake ready line). Replaced its body with a deprecation wrapper that kickstarts the agent (same effect as `helm restart`); only touches `com.helm.server`. (`322ecc1`)
- **2026-06-21 (s29)** — **HELM-009 RESOLVED — per-request IBKR timeout in `fetch_chain_from_ibkr`.** The paper-generate booker call was guarded by `except Exception` (bad ticker → skip) but nothing bounded a hung `qualifyContracts`/`reqSecDefOptParams`, so one stuck IBKR chain stalled the whole batch (the 2026-06-16 ~45-min GOOGL gap). Set `ib.RequestTimeout = 45` after connect so a hung request raises/returns bounded and is caught upstream as a per-ticker skip. Shared with the live open path — strictly a guard for both. Verified live in the running server. (`1265f2f`)
- **2026-06-21 (s29)** — **HELM-008 RESOLVED — `entry_snapshots` liquidity-column provenance.** `open_interest`/`bid_ask_spread`/`bid_ask_spread_pct` are the `entry_snapshot.py` liquidity-capture columns, introduced in code at HELM-013 (`6fd56bd`) and back-ported into `schema.sql` at HELM-002 Cluster B (`8a9a5c3`) without the provenance comment the adjacent index block got. Live carried them ahead of the builder; now declared (CREATE @242 + ALTER @740) so the HELM-002 builder reproduces them. Wired and functioning — 3/28 live rows populated, so the prior "unpopulated" note was stale. Added the documenting comment to `schema.sql`. (`036d8ba`)
- **2026-06-21 (s29)** — **OPS — `helm restart` added; server is launchd-managed.** The server runs as launchd agent `com.helm.server` (KeepAlive, PPID 1), not the heredoc in `helm-servers.sh` — which conflicts on port 8766 and can never restart this agent (its `pkill -f "...8766"` can't match a heredoc whose port lives on stdin). Added `helm restart` wrapping `launchctl kickstart -k gui/<uid>/com.helm.server` (new `helm/cli/server_cmd.py` + dispatch entry). Canonical restart is now `helm restart`. (`1762cc2`)
- **2026-06-21 (s28)** — **HELM-002 RESOLVED — `schema.sql` is a faithful builder of live (constraints / defaults / FKs).** Built `diag_schema_constraints.py` to diff a fresh `schema.sql` build against live across CHECK / DEFAULT / NOT NULL / PK / FK / UNIQUE — the surface the presence-only `apply_schema_reconcile.py` never compared. Only drift: three CHECK token-lists lagging live's `writable_schema` widenings (`positions` + `strategy_settings` missing `LONG_PUT` / `LONG_STRADDLE`, `lifecycle_events` missing `PENDING`); no default / FK / UNIQUE drift. Back-ported additively in live token order (`706cdf7`); both gates now CLEAN / NO-OP. Diagnostic kept as a standing constraint companion gate (`9ce62c9`). Lesson: a `writable_schema` CHECK widening on live must be back-ported to `schema.sql` in the same step — the new gate guards it.

- **2026-06-21 (s28)** — **HELM-021 RESOLVED — six `positions` secondary indexes created live.** `idx_pos_account` / `ticker` / `strategy` / `status` / `opened` / `signal` (verbatim from the builder) were absent live — only the autoindex present, on the hot table. Gated live pass: read-only probe → WAL-safe `/tmp` validate (`integrity_check` ok, all six present) → timestamped backup (`data/helm.db.bak.20260621-081710`) → `CREATE INDEX IF NOT EXISTS` on live → re-verify. Live-only change, no code / commit. Server picks up new indexes on next statement prepare.

- **2026-06-21 (s28)** — **HELM-007 RESOLVED — stale paper-book docstrings refreshed (`c6bd777`).** `paper_cmd.py`: dropped the inaccurate `single-leg` qualifier (×2) — `_PAPER_BOOKERS` books single- and multi-leg. `_paper_generate.py`: removed `straddle` from the absent / skipped list (now booked via `paper_open_straddle_one`, HELM-011) and rewrote the not-atomic / orphan-PAPER-position block to reflect the atomic open (HELM-003). `workflow_cmd.py`: added the missing `helm paper generate` entry. Note: the issue's `--manage` was a phantom — `helm paper` exposes only `generate`, no paper-manage command exists.

- **2026-06-20 (s27)** — **HELM-025 RESOLVED — off-limits guard at the open path.** `SHORT_STRANGLE` / `JADE_LIZARD` (undefined-risk, IRA-ineligible) were already un-openable — both absent from `STRATEGY_CONFIG`, so `helm open` hit the “Unknown strategy” gate — but that message was wrong (recognized-but-off-limits, not garbage) and the protection was incidental (adding either to `STRATEGY_CONFIG` later would silently re-enable it). Decided GUARD over DROP: the tokens are load-bearing (`import_cmd` classifies imports as `SHORT_STRANGLE`; `check_cmd` leg-count map; `position.py` risk class; `setup.py` defaults; `paper_manage` grouping), so dropping is unsafe. Added module-level `OFF_LIMITS = {SHORT_STRANGLE, JADE_LIZARD}` and an explicit refusal in `run()` before the `STRATEGY_CONFIG` gate — honest reason, robust even if a token later enters the config; tokens untouched. Code-only, one file, py_compile-gated; live-verified (both refuse, CSP/LONG_PUT proceed, unknown still rejected). Patch `apply_helm025_guard.py`. Commit `52bcda7`.
- **2026-06-20 (s27)** — **HELM-026 RESOLVED — `LONG_PUT` first-class.** Code had outrun the register: `LONG_PUT` was fully wired (scan `'buy'` family, full `STRATEGY_CONFIG` entry, open path, `_PAPER_BOOKERS`, analyze, display) but missing from `STRATEGIES` and both CHECKs with no `strategy_settings` row — a `LONG_PUT` write was silently rejected. Shipped: `'LONG_PUT'` token in `STRATEGIES` (after `LONG_CALL`); `positions` + `strategy_settings` CHECK widened via `writable_schema`; a `strategy_settings` row cloned from `LONG_CALL` (inherits 0.75 PT / 21-DTE exit, id `default_LONG_PUT_<acct>`). DB migration `/tmp`-validated (both CHECKs allow it, integrity ok, a probe `LONG_PUT` position + the settings row both insert) before live behind a `.backup()`; enum patch py_compile-gated. `setup.py` skipped (straddle precedent — fresh-install seeder, not needed for live). Patches `apply_helm026_db.py` (gated), `apply_helm026_enum.py`. Commit `5445b61` (enum; live DB migration applied separately, gitignored).
- **2026-06-20 (s27)** — **`.gitignore` sweep RESOLVED** (parking lot cleared). A tracked 44-line `.gitignore` was invisible to `git status` (tracked-unmodified) and got overwritten by an `mv`; caught via the commit’s 44-deletion count. Recovered the original (`git show HEAD~1:.gitignore`), merged old + new (deduped), validated via `git check-ignore` — live DB (`data/helm.db`, `data/*.db`), `.env`, the `!_paper_*.py` negation, and generated output all protected; the working-dir clutter (`apply_*.py`, `*.bak.*`, handover/additions `.md`) swept. Working dir ~90 untracked → clean; today’s patch `.bak`s are already covered. Commit `315f4a1`.
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
- Uncommitted after this checkpoint: `ISSUES.md` (this register update) — commit on the usual explicit-named-files step. Unpushed: `5445b61` (HELM-026 enum) + `52bcda7` (HELM-025 guard) + this `ISSUES.md` commit; origin/main is at `315f4a1` — push together.
