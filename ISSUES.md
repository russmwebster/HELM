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

_Last updated: 2026-06-29 (s40 — **HELM-036 build session**. Shipped Stages 1–4a of HELM-036 (the data-quality / GOOD-filter program) as five paired commits, c5207d3 → 8edb8f6: Stage 1 added `Check.latest_good` / `daily_good_series` + the shared `_pnl_pick` / `bounded_pnl` bound in `models/check.py`; Stages 2–4a routed the GOOD filter through `status`, `analyze`, and `/health`, clamped analyze's dollar-P&L displays, and consolidated health's local `_pnl_pick` onto the shared copy. Correction logged: the GOOD filter does NOT clean HELM-035 by construction — 15 corrupt rows are GOOD-stamped — so the `_pnl_pick` bound is load-bearing. All five commits are LOCAL (origin/main still at s39's 1faee02 — needs push). Bridge was flaky all session (stale-response ghost, large-payload jams, wedged /exec); recovery = full tab close+reopen, keep greps narrow. Then 4b shipped (6/29, separate commit): csp/longcall /health P&L bound at the gather chokepoint via _pnl_pick - bases computed in-loop because DB max_loss/max_profit are NULL for CSP/LONG_CALL (the obvious "pull p.max_loss/p.max_profit and wrap" would have been a silent no-op); pnl_display/pnl_source plumbed, the two _summary_facts renderers flipped to read pnl_display. Unit-verified: clamps impossible profit/loss to None, leaves long-call upside uncapped. Then notify GOOD-filter shipped (6/29): build_summary's latest-check-per-position MAX(checked_at) subquery now constrained to data_quality='GOOD' (inline predicate, Stage-2 pattern) - completes the GOOD-filter mandate across all six audited checks consumers. notify confirmed live (registered helm notify command + 10am Mon-Fri workflow automation + osascript watcher), so patched not removed; build_summary is its sole DB read, the watcher does none. HELM-036 GOOD-filter complete - resolve at checkpoint. Next: HELM-027 WS5 (/health GUI -> core verdict, bundles HELM-033 book-blind /health).)._

---

## Status — where HELM is
_Snapshot; refreshed each `helm checkpoint`, read via `helm status`._

- **Phase:** scaffolding complete (live · paper · edge). `schema.sql` is now a faithful builder of live including constraints / defaults / FKs (HELM-002), guarded by a standing `diag_schema_constraints.py`; the hot `positions` table is indexed live (HELM-021). Learning loop still the frontier — corpus accumulating on the clean `core_v1` universe, neutral long-vol (straddle) cell live, REAL opens stamping their originating signal (HELM-012 wired, pending first live fire).
- **Next highest-leverage:** **HELM-027 WS5 - /health GUI -> core verdict.** HELM-036 done (notify GOOD-filter shipped 6/29 closes the six-consumer GOOD mandate; resolve-to-log at checkpoint). WS5: point the `/health` GUI at `decision.evaluate` and repurpose `health.py`'s 8-indicator composite as evidence; bundle HELM-033 (the `/health` `gather_csp` query is book-blind + CSP-only, mixes REAL/PAPER - bring it to `check`'s REAL default). Alt forks if preferred: WS6 (`manage_paper_book` on a launchd timer + stop A/B) or push the 7 local commits. Residual logged to HELM-035: notify's `total_pnl` + take/stop buckets still read `pnl_unrealized` unbounded, so GOOD-but-corrupt victim rows (IREN/EOSE/SMR/FCX/APH/INTC) can still skew the daily push - fold in when notify routes through a shared bounded accessor, not by re-deriving per-family bases a third time.
- **Last shipped (s40):** HELM-036 Stage 4a — `/health` GOOD-filtered (csp/longcall/icondor/bearput latest-check subqueries) + health's local `_pnl_pick` retired onto the shared `models/check` copy (8edb8f6; verified `_pnl_pick.__module__ == helm.models.check`). Full session chain, all LOCAL and paired (code+register in each commit): Stage 1 accessors+bound (c5207d3), Stage 2 status (7e52bc1), Stage 3a analyze-filter (a927e95), Stage 3b analyze-clamp (7aec548), Stage 4a health (8edb8f6). Each: sha-checked delivery, py_compile, live-import, idempotent guarded patch.
- **Blocked (market/RTH):** first HELM-030 stop-A/B managed pass records on the next RTH (flag is live; arms begin then); `core_v1` IVR backfill for the new names (Mon RTH); live re-pull of the deep-ITM spec CSPs (RKLB/IREN/OKLO/IONQ) for the willing-to-own calls; verify `check.daily` writes GOOD-only rows on the next RTH.
- **Counts:** 14 active · 6 parked · last shipped s40 (HELM-036 notify GOOD-filter, `notify.py` build_summary). HELM-036 GOOD-filter mandate COMPLETE across all six consumers (status · analyze×2 · health×3 · notify) - resolve-to-log at next checkpoint (active 14→13). This session: HELM-036 Stages 1–4b + notify shipped; HELM-035 register premise corrected (15 GOOD corrupt rows → bound is load-bearing); notify pnl-bound residual logged to HELM-035.
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

**HELM-034 · `QUESTION` · `OPEN` · SHORT_STRANGLE / JADE_LIZARD in the HELM-030 naked arm set but guarded off-limits at open**
_Surfaced s37. `helm open` refuses SHORT_STRANGLE and JADE_LIZARD by design (per the CLI reference), yet `decision.evaluate_arms` includes both in the naked-credit arm set (`cr_2x`/`cr_3x`). Those arms can therefore only receive data from legacy/imported positions, if any — otherwise they are dead branches in the A/B. Verify next session whether any open paper SHORT_STRANGLE/JADE_LIZARD positions exist; if not, drop the arms or document them as import-only. Bears on whether part of the s37 build is exercised._

**HELM-027 · `DESIGN` · `OPEN` · Decision core — unified verdict engine**
_Locked s31, carried in memory + git; checkpointed here (the s31 close skipped writing it to the register). Goal: collapse the four divergent decision-logic copies — `paper_manage._evaluate` (the good, DB-driven prototype), `check.assess_position_health`, `check.generate_guidance` + per-strategy deep renderers, and `health.py`'s composite — into one book-agnostic verdict reading `strategy_settings` as the single source of truth._
Sequence 0→1→2→3→(4,5,6)→7:
- **WS0** [done · s31 `35838a6`] — `check` real/paper Phase-2 segregation (see Resolved log).
- **WS1** [done · s32] — guarded `strategy_settings` migration of three agreed levers + load-time validation (shipped: `_validate` in `models/settings.py`, fail-mode C — hard-fail trade-firing levers, warn advisory, NULL passes). Levers: CSP `dte_exit_threshold` 7→21; `profit_target_pct` →0.25 on defined-risk credit (IRON_CONDOR · BULL_PUT_SPREAD · BEAR_CALL_SPREAD · JADE_LIZARD); `stop_loss_multiplier` kept 2.0 (earmarked first paper A/B: 2× vs 3× vs no-stop). Writes live `helm.db` → DB discipline.
- **WS2** [done · s32] — extract `paper_manage._evaluate/_settings` into shared `helm/decision.py` (behaviour-preserving), point `paper_manage` at it.
- **WS3** [biggest lever · 3a+3b done · s32] — extend core past premium-only to LONG_CALL/PUT/STRADDLE, then diagonal/PMCC/covered-call; fixes the APLD no-verdict gap, unblocks both books. **3a** [done s32]: LONG_CALL/PUT family routing in `decision.py` (profit-on-premium, no stop; credit/straddle paths parity-verified unchanged). **3b** [done s32]: DEBIT_SPREAD family in `decision.py` (profit at 50% of max_profit, no stop; settings rows seeded for BEAR_PUT/BULL_CALL; other families parity-verified unchanged). **3c** [done s33]: COVERED family (profit-on-credit, no stop) + DIAGONAL family (PMCC/DIAGONAL/DIAGONAL_PUT) calendar-only off the back/long leg (no profit/stop — the income tail is the edge); DIAGONAL_PUT settings row seeded; calendar tail made family-aware (diagonal manages off the back leg, others off nearest). Front-leg roll deferred → HELM-028. All families' verdicts reach the real book at WS4.
- **WS4** [done · s35] — `check` default → core verdict; `--deep` renderers demoted from deciders to evidence formatters. Prep shipped: `dte`→`helm.dates` (decision→check_cmd cycle broken, frees `check` to import the core). Fork A locked (core is spine; renderers/guidance become evidence-only). Remaining: verdict→display map + wire `check_one`/deep renderers to `decision.evaluate`.
- **WS4 mapping** [locked s34 · fork A · P2 + distinct PROFIT_TARGET]: action axis is core-owned (`evaluate` reason: `None`=HOLD, any reason=CLOSE); band axis = reason-colour on close, evidence-graded GREEN↔YELLOW on hold. Reason→(action · band · headline): `None`→HOLD · GREEN (YELLOW if evidence stressed) · evidence; `PROFIT_TARGET`→CLOSE · GREEN “bank it”; `DTE_MANAGE`→CLOSE · YELLOW “inside DTE window”; `STOP`→CLOSE · RED; `EXPIRY`→CLOSE · RED. Health scorer (`assess_position` 0–100) survives WS4 as **evidence only** — may grade GREEN↔YELLOW on holds, never emits RED or CLOSE. Consequences: `WATCH` action retires (band-only meaning now); RED-on-hold impossible (a held position maxes at YELLOW).
- **WS4 wiring** [design locked s34 · Patch-1 (additive) + Patch-2a (the flip) shipped s34 — core verdict now authoritative via band_for(reason, evidence): reason owns RED + all action states, evidence lifts HOLD→YELLOW (underwater -15/-25, thin-buffer <3%, ITM/assignment-risk), mark_confidence ANNOTATES not masks (after-hours all-frozen board still triages by state — s34 lesson). HELM-030/031 are now stop-tuning, not flip-gates; "ITM, assignment risk" is the seam for a future ASSIGNMENT_RISK verdict. Patch-2b shipped (generate_guidance table-driven — 105-line threshold tree removed; headline + per-reason action keyed off the core verdict, evidence facts only, no thresholds). Patch-3 (strip legacy assess_position flag) DEFERRED — that flag is the gated-fallback for covered/PMCC/unmarked positions (core verdict skipped), so a full strip is gated on covered-call core coverage (parked); legacy flag retained as documented fallback. WS4 functionally complete]: verdict computed in `check_one` — `decision.evaluate(pos, legs, marks)` → `(reason, pnl)`, then a new pure `band_for(reason, evidence)` applies the P2 table. `assess_position` stops deciding → evidence-gathering only (score + buffer/ATR/P&L), feeding the GREEN↔YELLOW-on-hold nuance. `generate_guidance` → table-driven (headline from `reason`, body from evidence). Adapter: `SimpleNamespace` wrap of check's pos/leg dicts at the call site, `decision.py` untouched (canonical Position/Leg dataclass parked → HELM-029). Build prereq: read-pass of the three fns + verify check's dict keys vs `evaluate`'s 11 attrs (5 pos + 6 leg).
- **WS5** — `/health` GUI → core; repurpose `health.py` 8-indicator composite as deep/GUI evidence.
- **WS6** — schedule `manage_paper_book()` on a launchd timer (today only fires on `helm check --manage`); confirm `_decision_capture` feeds the corpus.
- **WS7** — mark-confidence (frozen/stale marks) as a first-class verdict input; gate CLOSE on frozen marks.
Feeds HELM-023 (which grades core verdicts vs outcomes).

**HELM-028 · `DESIGN` · `DEFERRED` · Diagonal front-leg roll + ITM-assignment flag**
_Born of WS3c (s33). The diagonal family (PMCC/DIAGONAL/DIAGONAL_PUT) is managed calendar-only off the
back (long) leg by design — the verdict engine speaks hold/close only and does not model the front-leg
roll campaign. Consequence: when the short front leg is expiring in-the-money, the core emits no
assignment-risk signal (the back leg keeps the structure "not near expiry"). Honest gap, not a bug:
rolling the front is a third action class the close/hold contract can't express. Trigger: a roll executor
(or a `ROLL_FRONT` advisory verdict) — the WS3-fork Option B we deferred. Until then, front-leg rolls +
ITM assignment watch are manual._

**HELM-023 · `DESIGN` · `DEFERRED` · Learning / look-back layer (the endgame)**
The core purpose: use the PAPER counterfactual corpus to score and tune HELM's entry/exit levers
against live picks — selection skill, pass-cost, and the boundary/cell choices (`bias_to_strategy`
thresholds, the neutral-sub-rich cell HELM-011 reserves for exactly this). Distinguish entry-lever
from exit-lever learning; target the variance risk premium for premium-family strategies. Trigger:
HELM-005 breadth landed **and** the PAPER book has closed trades to score. Gated by HELM-005 (the
corpus must range wider than HELM's screening taste) and calendar time (positions must close).
Sub-threads land here as the loop takes shape.

**HELM-029 · `DESIGN` · `DEFERRED` · Canonical Position/Leg object for `evaluate`**
_Born of WS4 wiring (s34). `decision.evaluate` consumes attribute-style `pos`/`legs` (11 attrs: pos `net_premium`/`account_id`/`strategy`/`max_loss`/`max_profit`; leg `id`/`direction`/`open_price`/`contracts`/`multiplier`/`expiration`). `paper_manage` feeds model objects; `check` (WS4) feeds dicts wrapped in `SimpleNamespace` at the call site — two adapter seams now exist. Consolidation: one shared `Position`/`Leg` dataclass both callers build from their rows, replacing the wraps. **Trigger:** a third consumer of `evaluate` appears. Until then the adapters are fine._

**HELM-030 · `DESIGN` · `OPEN` · Credit-family stop (2x credit) inert at live position sizes**
_Surfaced s34 by the WS4 Patch-1 full-book probe (64 pos). Credit-family stop is -min(2x credit, max_loss); across the live book only HD IC tripped it, and only because its credit is a $52 toy (stop -$104 vs P&L -$206). Every meaningfully-sized position has a 2x-credit stop wider than its drawdown: RKLB CSP -$8280 vs -$6108, OKLO -$7532 vs -$2268, WELL IC stop -$11920 sitting at 85% of its -$14040 max_loss. For defined-risk families a credit multiple is a near-useless stop. Decide: keep 2x (ride-to-management), or switch defined-risk families to a %-of-max-loss stop. Ties to the parked stop_loss_multiplier 2x/3x/no-stop paper A/B. Gates the WS4 Patch-2 flip._
_Narrowed s37: A/B measurement apparatus shipped — P1 migration (`stop_arm_events` + `stop_ab_active` flag), P2 `decision.evaluate_arms` (pure) + flag-gated acting-STOP suppression, P3 `paper_manage` recorder + natural-exit stamp. Basis-per-family: defined-risk spreads → %-of-max-loss (`ml_50`/`ml_75`), naked + jade → credit-multiple (`cr_2x`/`cr_3x`); acting arm = no-stop, thresholds frozen at entry. Flag flipped to '1' on 6/27 (s39) — experiment LIVE; arm recorder is freshness-gated (`ab_on = book_live and _stop_ab_active()`, so arms record only on live marks), now seeding the open paper credit positions; first managed pass on the next RTH. The REAL acting stop is UNCHANGED (still 2× credit, still inert for defined-risk); the fix to REAL is deferred until the A/B yields a winning arm. Chains: HELM-023 (grading) → apply winner to REAL + HELM-032 (deep-view stop basis)._

**HELM-031 · `DESIGN` · `OPEN` · LONG_DEBIT family has no loss-side exit**
_Surfaced s34 by the WS4 Patch-1 probe. The long-debit branch (LONG_CALL/PUT) has no stop by design (max loss is the premium paid), so the only exits are PROFIT_TARGET or the calendar. Live consequence: 4 long calls deep underwater all read HOLD/GREEN (AAPL -2451, APLD -2408, GOOG -2062, TSLA -1493). Decide intended stance: ride to thesis/calendar (current), or add a thesis-break / loss-fraction trigger to the long-debit branch. Gates the WS4 Patch-2 flip._

### Ops / enhancement

**HELM-033 · `DEBT` · `OPEN` · `/health` map is CSP-only and book-blind (commingles REAL + PAPER)**
_Surfaced s37. `helm health` opens `/health`, whose `gather_csp` query filters `status='OPEN' AND strategy='CSP'` with no book filter — so the browser map mixes REAL and PAPER CSPs, and the noise worsens as the paper book grows. `helm check` already defaults to REAL via `book_filter` (`--paper`/`--all` to widen); bring `/health` up to the same default (REAL, opt-in for paper). Also CSP-only, so it is not a whole-book health view regardless. Pairs with the WS5 work that wired /health to the core verdict._

**HELM-032 · `DEBT` · `OPEN` · Deep-view "Break-even & Stop" block uses a 1x-credit stop, disagrees with the core 2x verdict**
_Surfaced s35 in `helm check RKLB --deep`. The "Break-even & Stop" evidence block computes its own 1×-credit stop ("1× Stop loss $4,140 · 148% used") and reads as blown right beside a core verdict of YELLOW/HOLD — correct, because the core uses the 2×-credit stop (HELM-030). Same drift species 2b removed from `generate_guidance`, in a deep-renderer block 2b didn't touch. Render the stop on the same basis as the authoritative verdict (or label it explicitly as 1× reference). Resolve with HELM-030 (the stop-shape decision)._

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

**HELM-035 · `DATA` · `OPEN` · Check-history `pnl_pct` mis-scaled before a per-position basis flip (MCD pre-2026-06-18)**
_Found s38 while charting MCD/WELL trajectories from `checks`. MCD (`MCD-IC-20260609-MANUAL`) checks before 2026-06-18 carry `pnl_pct` of 560–1420% and `pnl_unrealized` peaks (~$3,720) that exceed the position's max profit (~$3,186); from 2026-06-18 on, the basis normalizes to a correct % of max profit (−1020 → −31.5% etc.). Smells like a re-mark / re-import on the 18th. WELL (opened 6/17) shows no break — basis consistent throughout. Risk: HELM-023's learning layer correlates entry features against historical outcome `pnl_pct`; mis-scaled early rows would poison it. Action: systemic sweep — isolated to MCD (pre-fix import artifact) or across all positions opened before some date? Promotes the s24 carried invariant (persisted `pnl_pct` > 100% on a credit structure → FAIL) from hypothetical to observed; that invariant is the natural guard + detector. Blocks nothing today; gating consideration for HELM-023._

_Sweep (6/26): **systemic, not isolated** — 152 corrupt check rows across 7 credit positions (MCD IC + CSPs IREN/EOSE/SMR/FCX/APH/INTC). **Time-bounded** — a fix landed 2026-06-18; zero corrupt rows after it. **Both fields wrong** — `pnl_unrealized` exceeds stored `max_profit` (structurally impossible) and `pnl_pct` used a ~12x-too-small divisor, so the dollar value isn't recoverable from the row. Remediation: quarantine pre-6/18 credit-position P&L from HELM-023; `_pnl_pick` (health.py) already bounds out-of-range P&L on the IC path and is the precedent to generalize. See HELM-036._

**HELM-036 · `OPS` · `OPEN` · `checks` consumers select the latest row with no GOOD/live/RTH filter — frozen marks leak into trade-facing views**
_Audit (6/26): six consumer sites read `checks` via `ORDER BY checked_at DESC LIMIT 1` / `MAX(checked_at)` / last-row-per-day, none filtering on `data_quality` / `greeks_source` / `rth_flag` (all written by `check_cmd.py`, used by no read site to select a row). Sites: `health.py:200` (/health CSP — no quality cols, P&L unguarded; worst), `health.py:833` (/health LONG_CALL), `health.py:1205` (/health IRON_CONDOR — P&L routed through `_pnl_pick`, so bounded + frozen-labeled, but spot/IV/greeks still frozen), `notify.py:58` (alerts fire on frozen `health_flag` / `action_signal`), `status_cmd.py:92` (`helm status` flag counts), `analyze.py:254/357` (charts — last-row-per-day, ignores the `rth_flag` it selects; identical to the s38 story-strip downsample bug). Trade-facing on /health (all 3 strategy views) and notify — a frozen after-hours mark shows a wrong spot or fires a stale alert: a live correctness bug, not just stale charts. Fix: one canonical accessor (`latest_good_check` + `daily_good_series`) filtering on quality and folding in the `_pnl_pick` bound, called by all six sites; subsumes `_pnl_pick`, repairs /health + notify, gives HELM-023 a clean source. Overlaps HELM-033 (the CSP-only query at L200 is the same one to fix); relates to HELM-019 (stale marks) and HELM-035 (the bound is the -035 guard)._
_Stage 1 shipped (6/28): canonical accessors landed in `models/check.py` — `Check.latest_good` + `Check.daily_good_series` (last GOOD per calendar day) + shared `bounded_pnl`/`_pnl_pick` bound. Pure addition — none of the six sites rewired, no behavior change (sha + py_compile + live-import verified). Remaining: route the six through them — Stage 2 `status_cmd`, Stage 3 `analyze`, Stage 4 `/health` (+ retire health's local `_pnl_pick`)._
_Stage 2 shipped (6/28): `status_cmd` GOOD-filtered (inline predicate) — the per-position latest-check feeding the flag tally and the global `MAX(checked_at)` last-activity read now exclude non-GOOD rows; dict row shape and all downstream access unchanged. Decision: realize the GOOD filter as an inline predicate at each SQL/dict consumer (Stages 2–4), reserving `bounded_pnl` for the dollar-P&L sites; the dataclass accessors stay the canonical GOOD definition + HELM-023 source rather than forcing every consumer into dataclass-land. Visible change: a position whose only checks are frozen/PARTIAL drops from the flag tally instead of counting off a frozen row._
_Stage 3a shipped (6/28): `analyze` GOOD-filtered (inline predicate) — the cmd_trends EXISTS gate, the cmd_trends per-position series (already last-row-per-day in Python; the selected-but-ignored `rth_flag` left as-is), and the cmd_position history read now exclude non-GOOD rows. **Register correction:** the GOOD filter does NOT clean the HELM-035 corruption by construction — a live data check found 15 rows with `pnl_unrealized > max_profit` (the impossible-value signature) stamped `data_quality='GOOD'` (plus 11 PARTIAL). The 11 PARTIAL drop out here; the 15 GOOD survivors still reach analyze's dollar-P&L display (cmd_trends L298, cmd_position L423), so the `_pnl_pick` bound (Stage 3b) is necessary, not belt-and-suspenders. Stage 3b: thread `max_loss`/`max_profit` + `greeks_source` into those two displays and clamp via the shared `_pnl_pick`._
_Stage 3b shipped (6/28): `analyze` dollar-P&L displays now clamp via the shared `_pnl_pick` (imported from `models/check.py`). Threaded `max_loss`/`max_profit` into the cmd_trends positions query and `greeks_source` into its series query (cmd_position already had `p.*` / `SELECT *`); clamped cmd_trends L298 (OPEN branch — closed uses authoritative `realized_pnl`), cmd_position L423 (per-row), and cmd_position `pnl_move` (both endpoints, None-guarded). The 15 GOOD HELM-035 survivors now render "--" instead of impossible values — closes the `analyze` side of the HELM-035 exposure. `daily_good_series` stays unused by analyze (its per-day downsample is inline) — reserved for HELM-023. Stage 3 complete; remaining: Stage 4 (`health` + notify)._
_Stage 4a shipped (6/28): `/health` GOOD-filtered + `_pnl_pick` consolidated. The four gather-fn latest-check subqueries (csp L209, longcall L840, icondor L1211, bearput L1668) now select only GOOD rows — frozen spot/IV/greeks no longer leak onto the live `/health` map. Health's local `_pnl_pick` (byte-identical to the Stage-1 shared copy) retired: removed the def, imported `from helm.models.check import _pnl_pick`; the two call sites (icondor, bearput) unchanged, now resolving to the shared bound (verified `_pnl_pick.__module__ == helm.models.check`). HELM-033 untouched — only the check subqueries changed; csp's book-blind position WHERE left as-is. Remaining: Stage 4b (bound csp/longcall unguarded P&L displays) + the notify.py wiring check (Status block says notify was de-wired from check.daily in s39 — confirm dead before patching)._
_Stage 4b shipped (6/29): csp/longcall `/health` P&L bound. `gather_csp` (CSP: max_profit = net_premium credit; max_loss = strike·100·contracts − net_premium) and `gather_longcall` (max_loss = abs(net_premium) premium paid; max_profit = None, upside uncapped) now route recorded P&L through the shared `_pnl_pick` and stash `pnl_display`/`pnl_source`; `_summary_facts` (csp) + `_summary_facts_lc` (longcall) read `r.get("pnl_display", r["pnl_unrealized"])`, matching the bearput convention. Bases computed in-loop because DB `max_loss`/`max_profit` are NULL for both strategies — pulling the (NULL) columns and wrapping would have been a silent no-op (the read-pass caught this). `greeks_source` deliberately not threaded: clamp rides on bounds, source label unused on these pills. Verified: 4 anchors + py_compile + sentinel readback; live gathers run clean (all current marks in-range, pass through as `recorded`); `_pnl_pick` unit-checked to clamp impossible profit/loss to None and leave long-call upside uncapped. Aside: PFE absent from `gather_longcall` (inner leg-role join) — pre-existing, not a 4b regression. Remaining for HELM-036: the notify.py wiring check._
_notify GOOD-filter shipped (6/29): the final 036 consumer. `build_summary` (notify.py) selected the latest check per open position via `MAX(checked_at)` with no quality filter - frozen/after-hours marks drove the RED/YELLOW alert buckets, the summed `total_pnl`, and the take-profit/stop-loss flags. Fix: `data_quality='GOOD'` added to both the outer joined row and the MAX subquery (inline predicate, matching Stage 2's status_cmd approach), so notify selects the latest GOOD mark; positions with no GOOD check now drop from the summary (inner join) rather than alerting off frozen data. Read-pass verdict: notify is NOT dead - it is a registered command (`helm notify` / `helm notify test`, workflow_cmd L104-105) and a documented 10:00am Mon-Fri automation (L41) feeding `helm-notify-watcher.py` -> osascript -> iPhone Reminders; the watcher does no DB read, so `build_summary` is notify's single checks consumer. Patched, not removed. With this, all six audited 036 consumers are GOOD-filtered -> the GOOD-filter mandate is complete; HELM-036 resolves at checkpoint. Residual handed to HELM-035: `build_summary` still reads `pnl_unrealized` unbounded for `total_pnl` and the take/stop buckets, so GOOD-but-corrupt victim rows can skew the daily notification - bound when notify routes through a shared bounded accessor rather than re-deriving per-family bases here._

**HELM-037 · `DESIGN` · `OPEN` · Persistence discipline — split compute/display from persist; only live canonical observations enter `checks`**
_Logged 6/26; scope cut to **RTH-only** (Russ, 6/27). HELM is a **journal of live RTH observations, not a real-time terminal** — off-hours frozen data is explicitly out of scope for persistence (the 6/27 read-only IB probe proved frozen marks + full model greeks ARE salvageable off-hours — 7/8 legs — but we choose not to depend on them). Spec: (1) ad-hoc `helm check` and `/health` are **read-only** — compute + display the latest live row labeled with its timestamp, never write; (2) a single **RTH-anchored scheduled writer** (~15:45 ET) is the sole writer and persists **one live mark per position per day**, only when `marketDataType`=live and fields are non-NaN — else skip ('no live mark, skipped'). No frozen/after-hours rows enter `checks` by construction; the `undPrice`-fallback / ignore-`close`-field / per-contract salvage rules the probe surfaced are NOT needed (they were for the now-dropped off-hours path). Optional midday RTH slot later only if HELM-023 wants intraday shape. Consequence for HELM-036: since the writer only persists live RTH rows, 'latest good check' ≈ 'latest row', so the accessor's quality filter becomes cheap belt-and-suspenders rather than the primary defense. Named tradeoff: off-hours, `helm check` / `/health` show the last RTH snapshot (e.g. Friday's close), labeled stale — correct by design; for live off-hours decisions, go to the broker. Forward-looking only — does NOT clean existing rows (HELM-035 quarantine + a possible frozen-duplicate purge are separate). **Writer + cadence LOCKED (6/27):** the paper manage pass is the **sole** snapshot writer; it persists at its existing three slots — **10:00 / 12:30 / 15:45 ET, Mon–Fri** — only when marks are live (else skip, no stale row), giving up to three clean live marks per paper position per day. Ad-hoc `helm check` and `/health` stay look-only. Real book still relies on `check.daily` as its writer — don't orphan it when folding agents. **Progress (6/27):** live-only gate shipped in `save_check` (check_cmd.py) — only GOOD (live + complete) marks persist; frozen / partial / yfinance reads are computed + displayed but not written. Remaining for full 037: consolidate to the single manage-pass writer (10:00/12:30/15:45) and make ad-hoc `helm check` read-only._

## Resolved log

- **2026-06-25 (s36)** - **WS7 gate paper auto-manage CLOSE on non-live marks.** `_leg_mark` now returns (mid, is_live) - live iff IBKR-live (source==ibkr and live); ibkr-frozen / yfinance / no-data → not live. `manage_paper_book` carries book-level `book_live` (weakest-link across legs); any `evaluate` close reason on a non-live book DEFERs (logged + counted) instead of `_finalize_close`. Pairs with the completeness skip-gate (skip=missing data, defer=unverified data). `evaluate` untouched - the action gate lives in the manager; `helm check --manage` inherits it. Closes the freshness seam HD walked through pre-open this morning. Dry-fire on live marks non-regressive: 4 CLOSE · 0 DEFER. Code: `paper_manage.py` (`b68780a`).

- **2026-06-25 (s36)** - **WS6 paper-book auto-manage on a launchd timer.** Added `helm paper manage` (manage-only entrypoint; `helm check --manage` left intact) and installed `com.helm.paper.manage` firing 10:00/12:30/15:45 EDT Mon-Fri → `logs/paper_manage.log`. Pure-rules, no API key. RTH-only by schedule; holidays no-op via the incomplete-marks skip-gate. Verified loaded (`launchctl print`, state=not running, runs=0) and dry-fired clean (44 HOLD · 1 CLOSE · 1 SKIP). Starts the corpus clock toward HELM-030/031 and HELM-023. Code: `paper_cmd.py` (`b2a2c8e`); plist outside the repo.

- **2026-06-23 (s31)** — **Real/paper segregation, Phase 2 (`check`).** Filtered the `helm check` display paths to real-by-default via `db.book_filter`, completing the view segregation begun in Phase 1: `cmd_check_all` (has `args`), `cmd_check_one` (no `args` param — reads `sys.argv`), and the `--deep` all-ticker scan. Routing already supported `--all`/`--paper` through the `else: cmd_check_all(args)` branch, so no control-flow change was needed. `cmd_check_integrity`'s all-book P&L/leg-recompute audits left unfiltered by design. Verified: `helm check` reads 19 real, `--all` sweeps 65, `--paper` runs the paper book alone.

- **2026-06-23 (s31)** — **Operational views default to the real book.** `helm status` and `helm positions` (and their backing premium/deployment math) queried by `account_id`+`status` only, so the 46 paper positions from a `paper generate` bled into the Fidelity-labelled cockpit — counts, premium, and deployment were summed across both books. Added `book_filter(argv)` to `db.py` (default `book='REAL'`; `--all` => both, `--paper` => paper only) and wove it into `status_cmd` and `positions_cmd`. Trader-facing views are now real-only by default with opt-in paper; the corpus stays in the background. `check_cmd` deferred to Phase 2 (it interleaves the display query with all-book P&L/leg-recompute audits that must stay unfiltered). Verified: `helm status` reads 19 real, `helm status --all` 65 combined. (Aside: the panel's open-premium is `sum(abs(net_premium))`, which is why a signed SUM didn't tie.)

- **2026-06-23 (s31)** — **Bridge `/exec` PATH fixed so `helm` and `python3` resolve.** The non-login `/bin/sh` that `helm-server.py` spawns for `/exec` loaded neither the conda env nor the user's `helm` alias, so every bridge command had to hand-type `/opt/anaconda3/envs/helm/bin/python3 helm.py`. Added a real `bin/helm` wrapper (execs the env python on `helm.py`) and a PATH prepend in `helm-server.py` (`bin/` + the env bin ahead of system dirs) that all `/exec` children inherit via `env={**os.environ,...}`. Takes effect on `helm restart` (re-exec re-reads the file; no plist touch). Verified through the bridge: bare `python3` is now 3.12.13 and bare `helm status` renders. (`'helm '` was already whitelisted; PATH was the only gap.)

- **2026-06-23 (s31)** — **Anthropic API key relocated out of the launchd plist.** The key was sitting in plaintext under `EnvironmentVariables` in `~/Library/LaunchAgents/com.helm.server.plist`. Added `helm/secrets_loader.py` (dependency-free `.env` reader that injects KEY=VALUE pairs into `os.environ` only when not already set) and wired `load_env()` into `theme_cmd.call_claude`, the sole consumer. The key now lives in `~/Projects/helm/.env` (mode 600, gitignored); the `EnvironmentVariables` block was removed from the plist and the agent re-bootstrapped via `launchctl bootout`/`bootstrap` (a plain `kickstart` would not re-read the file). Verified through the bridge: a server-spawned `call_claude` carries no env key and authenticates from `.env`. Old key rotated and revoked in the Console.

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

- **Mirror launchd plists in-repo** - keep canonical copies of the `com.helm.*` agent plists under a repo `launchd/` dir (today they live only in `~/Library/LaunchAgents`, un-versioned). Why: provenance + reproducibility; a machine rebuild currently loses the schedule definitions. Surfaced s36 installing `com.helm.paper.manage`.
- **HELM stages & workflow UI** — interactive graphic of HELM's development stages and operational loop (scan → decide → REAL/PAPER → manage → analyze). Productionize the s25 chat workflow diagram + dev-phase status into a navigable interface; build as standalone HTML (static file, or served at `helm.local`); doubles as onboarding. Why: at-a-glance orientation for where the system sits and how the loop runs.
- **COVERED_CALL gradeability** — populate `stock_positions` (underlying cost basis) so covered calls stop being skipped as "no capital basis" in `analyze edge` (surfaced s25, BSX). Why: every covered call is currently ungradeable.
- **Setup / onboarding flow** — first-run config (watchlist, broker pathway, account) per the original "built after core strategies" intent. Why: currently assumes a hand-built DB.
- **`helm status` / `helm checkpoint` CLI** — `helm status` prints the Status block + active/parked counts (flag staleness when `_Last updated_` is old); `helm checkpoint` assists the close-out. Why: the chat triggers work today, the CLI verbs make them first-class.

---

- **Trade-story visualization deep-views** — prototyped in-chat s38: (a) IC health "ladder" (profit/cushion/losing/max-loss zones + live-price marker); (b) price-vs-zone trajectory from `checks`; (c) planned price/P&L/IV three-panel story strip + a theta-vs-gamma crossover risk view (gamma climbing on a tested short as DTE shrinks). Why: turns the read-only `checks` corpus into a position narrative the trader can scan. Today they're ad-hoc bridge/chat renders; candidate to promote into real `helm` deep-view output.

## Carried threads · un-promoted follow-ups

Not yet promoted to numbered issues; pull in as they get worked.

**s39:**
- **HELM-036 build** — the read-side accessor (`latest_good_check` / `daily_good_series`); fold in `_pnl_pick`; route all check-consumers through it. Historical cleanup via read-time filtering (no purge); the 152 HELM-035 corrupt rows are excluded by the GOOD filter — add a non-destructive quarantine flag only if needed.
- **HELM-037 remainder** — consolidate to the single manage-pass writer (paper) alongside `check.daily` (real, now 3× + live-only-gated); make ad-hoc `helm check` fully read-only (a live ad-hoc check can still write a GOOD row today — harmless, not the clean ideal).
- **check.daily verify** — the live-only gate lives in shared `save_check`, so check.daily's 3× runs are already gated as of 228f995; confirm on the next RTH that real-book rows are GOOD-only.

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

**s27:**
- WELL half-link cleanup — its signal is `russ_action=OPEN` but `position_id` NULL (the s24 backfill flipped the action without stamping the position side). One-line data fix to complete or reset the link; HELM-012 prevents recurrence going forward.
- Conviction not stored — `signals` has no `conviction` column; the scan's Low/Mod/High is derived at display (off `top_fit`/fit_score). HELM-023 will need a real source when it scores conviction.

**s38:**
- Live re-pull (RTH) of MCD + WELL — extends the s24 HELM-018 thread; today's frozen check P&L (MCD −1020, WELL −4080) still diverges from live Fidelity (MCD −1934, WELL −6292), so the convergence gap is open.
- HELM-035 systemic sweep — scan all positions for persisted `pnl_pct` > 100% on credit structures; decide isolated-vs-systemic before any HELM-023 corpus use.
- Story-strip build — price/P&L/IV three-panel (from `checks`, no live marks needed) + theta-gamma crossover (needs live greeks). Anchor the IV panel on entry IV from `entry_snapshots` (`checks.iv_vs_entry` is NULL).
- WELL call-roll pricing on live quotes (roll tested 220/230 up/out) at the 21-DTE decision point.
- STILL PENDING (s37): flip `stop_ab_active` → '1' (guarded `helm_meta` write) + first managed pass — deferred through s38, not dropped.
- Workflow HTML check (s38 open, unresolved) — locate via `git ls-files '*.html'` / targeted `ls`; confirm whether it needs updating. Bridge hung on a repo-wide `find`; use targeted reads.
