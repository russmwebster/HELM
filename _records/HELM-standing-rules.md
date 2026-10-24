# HELM — standing rules

*Decisions that are settled, and lessons that were paid for. One line each, with who
and when. Read after `claude/HELM-handover.md`. **If a line here contradicts an item, this
file wins and the item is wrong.** A new line goes in only if it would change what a
future session builds; most sessions add nothing.*

*First cut 2026-09-20, curated from the lessons list and the decisions scattered
through `claude/HELM-worklist.md` (frozen). Russ reads it once before it is trusted.
Last updated 2026-10-24 (s134: the revision — rulebook v0.2 approved: strategy set, sleeve limits, paper book retired, universe, net P&L, no double entry). Before that 2026-10-23 (s133: a named contract is a record, not a selection). Before that 2026-10-14 (s130: the device shell never writes the live database). Before that 2026-10-12 (s129: plain language, no rule or task numbers when talking to Russ). Before that 2026-10-12 (s128: explain first, wait for the go-ahead). Before that 2026-10-10 (s127: PG archived as a product; the board is HELM's interface). Before that 2026-10-10 (s126: the handover replaces the live list). Before that 2026-10-01 (s125: net rent banked, the diagonal loss limit, the four-question
diagonal card, two keep reasons; one base per percentage; live state in render harnesses). **Kept as a mirror on the Mac at
`~/Projects/helm/_records/HELM-standing-rules.md` — edit that copy, publish with
`project_write local_path`.***

---

## Who does what

- **Russ trades the REAL book by hand — entry and exit, and sizes every trade. On the
  real book HELM informs; it never acts.** *(Russ, s107; HELM-193.)* **The PAPER book is
  retired** — no new paper entries from 2026-10-24; open paper positions run off to the
  11/20 expiries, then are marked run off and the paper agents parked. *(Russ, 2026-10-24.)* Anything
  reading as an instruction on a real-book surface is a defect.
- `book` is therefore also the actor field: a real close was a human, a paper close
  was a rule. The two books share an `exit_reason` vocabulary so they can be pooled —
  **but the same word means different things in each.**
- Rule-precedence questions were paper-book questions; with the paper book retired, a
  rule change is a change to what HELM TELLS Russ.
- Rule work has two possible products: a change to what the paper agent DOES, or a
  change to what HELM TELLS Russ. **Say which before building.**
- Russ commits and pushes. Sessions stage files by name and write the message.
- **Sessions explain any change first — what, which files, why — and wait for Russ's
  go-ahead before editing anything, records included. A question is not an
  instruction.** *(Russ, 2026-10-12, after s128 edited code on a question.)*
- **Talk to Russ in plain language — describe rules and work items by what they do,
  not by their W- or HELM-numbers** ("the spread-too-wide rule", not "W189"). Numbers
  stay in code, commits and records. *(Russ, 2026-10-12.)*
- Russ works from HELM's web board (`~/Projects/helm-pg`), as a macOS web app, not a browser tab.

## Settled decisions

### The revision (Russ, 2026-10-24) — `claude/HELM-rulebook-v0.2.md` is the spec

- **Purpose and strategy set:** HELM generates cash in the Rollover IRA by selling
  premium — **cash-secured puts, covered calls on HELM-held shares, and PMCCs** (a LEAPS
  long with short calls). Iron condors, credit and debit spreads, standalone long calls
  and puts, and the short-dated DIAGONAL route are **out**; open positions in them finish
  under the rules they were opened under. Nothing is force-closed.
- **No double entry: trades enter HELM only from Fidelity downloads.** Russ enters
  trades in Fidelity; HELM books every entry and exit (opens, closes, leg closes,
  re-sells, expiries, assignments) from his downloads. `helm open` / `helm close` are the
  fallback only until the import works. *(Russ, 2026-10-24.)*
- **The HELM sleeve and the only size limits:** sleeve = **$300,000 + the realized result
  of every REAL leg closed from 2026-10-24** (each leg as it closes; gross until the
  import, then net; dividends on HELM-held shares count; collateral interest does not).
  **In use** = CSP collateral + diagonal/PMCC longs at cost (the run-off diagonals
  included) + assigned shares at cost. **In use ≤ sleeve; one name ≤ 20%; one sector ≤
  30% of the sleeve.** A fund's sector is its `approved_funds` bucket. A trade over a
  limit is **declined with the reason**; a typed count overrides, recorded "OVER-LIMIT
  OVERRIDE (sleeve|name|sector)" — also when typed above a non-zero suggestion. **The
  $5,000 per-trade cap, the 5% cash ceiling and the 5% long-premium limit are gone** for
  CSPs, covered calls and the diagonal family. Built `helm/sleeve.py`; reported by
  `helm sleeve` / `helm book` and the board's Book page. No beta-weighted delta band yet.
- **Universe:** offered only when the name's latest ownership grade is **A, B or C**, or
  it is an **approved fund (grade FUND)**. Ungraded, D, F and N ("can't grade") are not
  offered. Refresh monthly plus a fresh grade before a name's first trade. The always-on
  `willing_to_own` flag is retired. Trend, support and IV-vs-realized are **shown, not
  gates** (the records point against them). Grader fixes (s134): negative equity fails
  only with interest cover < 3× or negative FCF; the runway test only for unprofitable
  companies; missing data lowers confidence, not the grade; a bank method for banks and
  capital-markets firms.
- **CSPs:** 0.25–0.30 delta, 30–45 DTE, IV rank ≥ 50, spread < 10% of mid; no entry within
  10 days of earnings, earnings later in the trade allowed and shown; the contract is
  chosen (highest annualized return on collateral); the daily list is the **top 5**, one
  per name. **50% profit-take as a resting order Russ places at entry** (the Book page
  shows the price); 21 DTE; no stop. Tested → a flag showing both roll and assignment.
  **Assignment accepted by default while the name grades A–C.**
- **Covered calls:** strike ≥ the **effective basis** (strike paid − put premium); 0.20–
  0.35 Δ, 30–45 DTE; **no earnings inside the trade**; ex-dividend flag; 50% resting order;
  **21 DTE** (was 7).
- **PMCC:** long 270–730 DTE, 0.75–0.85 Δ, mostly intrinsic; short 30–45 DTE, 0.20–0.30 Δ;
  **the width test on every short, re-sells included** (strike gap > what the long has
  cost net of rent banked); review the long at 90 days left; loss limit half the long's
  cost; one contract per name until there is a record.
- **Measurement:** **net of fees is the P&L of record** (gross beside it; trades before
  7/13 stay gross, labelled "fees not recorded"); capital-weighted annualized return on
  capital at risk; benchmarks the money-market yield on the same cash and days and Cboe's
  PUT index; sample size and rule version beside every figure.

### Earlier settled decisions (in force unless marked superseded)

- **HELM is one track. HELM PG as a product to sell is archived; `helm-pg` is HELM's
  web board.** It holds no model (every threshold, gate and verdict is imported or read
  from the engine) and **changes anything only by calling HELM's own commands, never by
  writing the database.** Product-only work (licensing, installer, setup flow, pinned
  manifest, production server, data-provider layer) is dropped. *(Russ, 2026-10-10.)*
  **Archived, history only — never orient from them:** `claude/HELM-product-vision-and-status.md`,
  `HELM-product-development-plan.md`, `HELM-PG-wiring-pickup.md`, `HELM-trial-free-data.md`,
  `HELM-PG-where-the-product-stands.md`. Their status blocks describe July–August.
- *(Superseded 2026-10-24: net of fees is now the P&L of record, from the Fidelity
  import — see the revision above. History: on 2026-08-26 a spec implemented fee capture
  as though decided and broke `helm close`; build net P&L from the import's fills, not by
  editing `helm close`.)*
- **PAPER settles expiries by inference** from the last good expiry-day spot — OTM →
  0.00, ITM → intrinsic, within 1% of the strike or no mark → flagged. **REAL is
  asked, never inferred.** *(Russ, s110.)*
- **A paper leg with NO expiry-day mark settles from the official close**
  (`expiry.settlement_intrinsic`, yfinance) as an explicit fallback, and the ±1%
  guard cases settle the same way. *(Russ, 2026-09-20 — W177.)* **The guard that keeps
  W167 intact: the fallback WRITES `close_price`, and `settled_mark` keeps reading
  `close_price` first — so a settled leg still has exactly one value.**
- *(The paper rules in this list — inference settlement, the official-close fallback,
  hand-fired settlement, the paper exit agent — hold only for the paper run-off to the
  11/20 expiries; then the paper book is marked run off and its agents parked.)*
- **Paper settlement stays hand-fired.** No agent, no tripwire. *(Russ, 2026-09-20.)*
  **Accepted consequence: every paper expiry Friday loses readings until a session
  runs `helm settle`, and the audit grades those days DEGRADED with the cause named.**
- **Diagonals stay in the model, as a rented long** — *for the three run-off real
  diagonals only (EQT, IBM, JPM; longs 12/18). New diagonal-family trades are PMCCs under
  the revision's rules (2026-10-24).* The long call is the asset; a
  short is sold against it, harvested, and the long waits as a plain long until the
  next short. *(Russ, 2026-09-21. Spec: W180.)* **A diagonal with no live short leg is
  a long call, and the long-call rules govern it.**
- **A LONG-ONLY diagonal's v3 percentages are the long's OWN move over its ORIGINAL
  debit, with no rent in them.** *(Russ, 2026-09-23.)* Rent would widen the stop with
  every harvest, which is a cap in reverse. Built into `exit_flags` (W180 step 5).
- **A leg added to an open position — a re-sold short — is live on its booking day only
  from its booking time; legs booked with the position are live all day.** *(Russ,
  2026-09-30 — W204, fixed in the rule, not in the chart.)* Day granularity had given
  re-sold shorts the long's mark on that morning's checks. The position's opening day is
  its legs' earliest `open_date`; `legs.created_at` is local time.
- **A one-sided quote is a market.** IBKR's `bid = −1` with a live ask reads as bid
  0.00, stored as 0.00 not NULL, and the leg marks at the midpoint of 0.00 × ask.
  No bid AND no ask marks nothing. *(W179, 2026-09-21.)*
- **Gate on facts, display judgments.** The stored IV-rank ceiling stays a warning,
  not a gate. *(Russ, s92.)*
- *(Superseded 2026-10-24 by the sleeve limits above: the $5,000 per-trade cap and its
  one-contract decline (W160/W201), the 5% cash ceiling, the 5% long-premium sleeve
  (W194) and the long-call entry screen (W195, `lc-screen-v2`). Their code stays in
  `helm/risk_cap.py` / `helm/lc_screen.py` for the retired structures — long calls and
  puts, spreads, condors still size the old way until they are parked. Full text: this
  file's git history (`_records/`, before s134) and `HELM-archive-2026-09-27-s123*.md`.)*
- **The 20-point give-back line is the default exit on real long calls** (any that remain open). *(Russ,
  2026-09-27. Replayed on the real book: −$6,540 vs −$27,056 actual, 23 better / 6
  worse, consistent before and after the 25 Jul switch.)*
- **The 21-DTE deadline stays for CSPs and condors — as insurance, not edge.** The
  n≥120 re-run (W16, 2026-09-26) found 28 DTE loses on new trades and cuts no big
  losses there; the historic tail came from oversized trades, which sizing now handles.
  *(Russ, 2026-09-26; supersedes s107's "stays until the re-run".)*
- **Credit spreads (bear call, bull put) close at 21 DTE, not 7.** *(Russ, 2026-09-26.
  The 7-day deadline's paper closes: 10 of 10 lost, median −363% of credit.)* **Only
  these two** *(Russ, 2026-09-27)*: the deadline is `strategy_settings.dte_exit_threshold`,
  one row per strategy, so changing it never moves condors, CSPs or covered calls.
  Switched on by `_s123/w192_apply.py` (W192), which refuses while an open spread sits
  inside the new window unless `--force`.
- **CSPs: 50% profit target; no stop-loss, strike-breach or give-back exit.** Every
  such rule, replayed on the marks, made more trades worse than better in both the old
  and new samples. *(Russ, 2026-09-26.)* **A CSP whose bid-ask spread is ≥ 10% of mid at
  entry** (the chain row's `spread_pct`, which is what `entry_snapshots.bid_ask_spread_pct`
  stores; 10.0% counts) **is refused on paper** — the top pick only, never swapped for a
  lower-ranked contract, logged to `paper_refusals` as rule W189 — **and declined on
  real** like W160 (Enter records nothing; a typed count overrides, recorded "RULE
  OVERRIDE (W189)"). *(W189, built 2026-09-27; decline-with-override follows the W195
  pattern Russ approved.)*
- **The paper exit agent decides after every snapshot, not once a day.** *(Russ,
  2026-09-27 — W198.)* **As built:** inside the snapshot process after the 10:00 and
  12:30 slots (any snapshot starting before 15:00 that covers PAPER); the scheduled
  15:35 run is the after-15:15 pass; a lock keeps two passes from overlapping; ledger
  note "after snapshot HH:MM"; off switch `HELM_W198_OFF=1`.
- **Tilt capital toward premium selling**, where the book has shown an edge. *(Russ,
  2026-09-27.)*
- **A REAL diagonal may be pinned from the chain, out of band**, booked as
  `MANUAL_PIN` with "PINNED OUT OF BAND" in its notes, permanently. *(Russ, s113.)*
- **A diagonal shows BOTH legs' DTE.** *(Since W180 step 5, 2026-09-23: a diagonal's
  flags read the legs themselves — the short in SHORT ON, the long in LONG ONLY.)*
- **A diagonal card draws what the diagonal rule reads.** The W88 slice 5 panels take
  their readings from `exit_flags._legs`/`_diag_rows`/`_live_at`, never a second copy;
  a short with zero readings is a labelled grey band, never a guessed line. *(Russ,
  2026-09-30.)*
- **`market_context` is records only.** Nothing reads it and nothing is to be wired
  to it. *(Russ, 2026-09-19.)* *(W195's S&P gate reads ^GSPC itself at scan time —
  `market_context.spx_vs_200_now` — and does not read the `market_context` table.)*
- **Fills are what you typed.** Marks are at the midpoint and a long exits at the bid,
  so targets and the paper record are somewhat optimistic. *(s115.)*
- **A `.bak` lives until its change is committed AND its first unattended run is
  clean; then it goes. Keep the newest three database backups.** *(s120.)*
- **Exit flags are review triggers, not sell signals.** The disposition is a logged
  yes/no, never an auto-exit. *(s114/s115.)*
- **A breached diagonal may be closed WHOLE rather than re-sold.** *(Russ, 2026-09-23,
  on TSLA.)*
- **A control belongs on a surface that shows what the control acts on.** *(Russ +
  session, 2026-09-23.)*
- **Net rent banked = every short sale's credit minus every buy-back's cost, losing
  buy-backs included; a diagonal's effective cost = what the long cost − net rent banked.**
  Display only (the long's own rules keep its original price). *(Russ, 2026-10-01.)*
- **A diagonal's whole-trade stop is the LOSS LIMIT: the whole trade's dollar P&L at or
  below −half of what the long cost, in BOTH states.** It watches the money; the long's own
  rules (−50% stop, 20-point give-back, 21/7 days) watch the long against its original
  price. Replaced `pnl_pct ≤ −50` of the opening net debit, which mixed bases once a short
  was re-sold (AA "−72%" of $3,325 while $3,100 was in it). *(Russ, 2026-10-01; switch day:
  no new real flag.)*
- **The diagonal card is organised around the trader's questions, in dollars:** where I
  stand · what is asking for a decision · my moves · every dollar in and out · what the
  trade is · detail. Percentages only where a rule is defined in them, always with dollars
  beside. *(Russ, 2026-10-01, from three mockups; built in `thesis_diag.html`.)*
- **"Holding the long to keep renting it out" and "Holding for recovery" are legitimate
  reasons to keep a diagonal loss flag** — the card shows the test for each (rent vs decay;
  break-even against the expected move). *(Russ, 2026-10-01.)*

## Before building

- **Re-measure the item first, and expect it to change what you build.** It has
  changed the build seventeen sessions running — on 09-27 a 9-year price test withdrew
  the session's own proposed long-call screen before anything was built; on 09-30 the
  re-measure showed the spec's "no readings" shorts were readable and found W204.
- **Re-read the constants, not your memory of them.** `TARGET_DEFAULT` was 25%, not
  the 50% an item assumed; that turned 8 flags into 11.
- **Find every reader of the thing you change** — the table, not the commands you
  happen to be thinking of.
- **Ten models map `SELECT *` into a constructor: `checks`, `entry_snapshots`,
  `iv_history`, `legs`, `lifecycle_events`, `pathways`, `settings`, `signals`,
  `themes`.** A new column on any of them breaks every path that builds the model.
  Check the list before any migration. *(W155.)*
- **A spec written by an earlier session is not authority to widen a decision the
  user has already made.**
- **A decision recorded in one line is narrower than the conversation behind it.**
  s123 built W160 from the standing-rules line, which named the one-contract decline
  for CSPs only; the conversation had also said a $16k one-lot long call "would have
  been a no." When a written rule and its source conversation could differ, check the
  archive/study doc — or ask — before choosing the narrower reading. *(2026-09-27.)*
- **Search the project before recommending a change.** `project_search` first.
- **A named contract is a record, not a selection.** `helm open --strike/--expiry` books a
  trade already made, so selection rules (strike window, delta band, entry DTE window) must
  never hide it; only the exchange listing and the money rules (spread, size, ceiling — each
  overridable, recorded) apply. Every filter between the chain and the pin is a place it can
  vanish: test the pin past each one. *(s130 strike window; s133 DTE window — JPM 11/20 at 28
  DTE was unbookable, 2026-10-23.)*
- **An insertion point is a decision.** Read the function's exits before choosing
  where to stand.
- **A `SELECT` with no `ORDER BY` is a decision made by insertion order.**
- **A gate that fires on a normal lifecycle state is not a gate, it is an outage.**
- **Name the mechanism from the code, not from a tool's output.** *(2026-09-21.)*
- **State the evidence tier when defending a number** — A (evidence), B
  (convention), C (folklore). *(HELM-129.)*
- **Before filling a never-written field, find every reader first.**
- **Do not assert a market fact you have not measured.**
- **A number that appears in three places was derived once.** Re-derive it where it
  is repeated, or say where it came from.
- **A rule written twice will drift, and the drift is silent.** When you find the same
  rule in two places, the fix is one module, not two patches. *(W181.)* **s123: the
  $5,000/trade sizing check was heading for a SIXTH copy — `suggest_contracts` plus
  four separate inline blocks in `display_spreads`/`confirm_spread`/`evaluate_condors`/
  `confirm_condor`/`display_condors`/`display_debit_spreads`, three of them hardcoding
  `0.05` directly rather than reading `risk_pct_per_trade`. One shared `helm/risk_cap.py`
  replaced all of them.**
- **A leg's P&L is not the position's.** *(W180 replay, 2026-09-21.)*
- **A long call's result is the stock's direction** (17/21 won when the stock rose,
  0/36 when it fell). **Test a stock-picking rule on years of stock prices, not on a
  few dozen option trades from one summer** — the summer's pattern (buying after a
  spike) did not hold over nine years. *(s122, 2026-09-27.)*
- **Check a proposal against trader practice as well as the literature.** The
  academic pass said "avoid strength"; traders and the 52-week-high research said
  "strength is fine, chasing a spike is not" — the distinction the first draft missed.
  *(s122.)*

## While verifying

- **A command that has never been run has not been verified.** `VACUUM INTO` a copy,
  `HELM_DB=` the copy, run the real CLI, dump the rows.
- **Verify both ways.** Perturb the defect you most fear, confirm the suite fails;
  restore and confirm it passes; check the file is byte-identical after.
- **Aim a perturbation at a case the output can see.** A series that keeps one point
  per day hides an intraday defect whenever a later check that day overwrites it: W204's
  AA case passed with the fix switched off, EQT's (booked after the day's last check)
  failed as it should. *(2026-09-30.)*
- **A check on a copy must start from a fresh copy; run the unperturbed control
  LAST.**
- **A harness must say which code it loaded.**
- **START THE MODULE THE WAY PRODUCTION STARTS IT.** *(2026-09-23: 32 cases passed
  while every PG page was blank.)* *(s123: `helm-pg/app.py` re-run directly with
  `PORT`/`HELM_DB` overrides against a DB copy, same entry point production uses.)*
- **Test a cap at the edges, not only in the middle.** The s123 self-test passed
  20/20, but no case asked what happens when ONE contract already exceeds the cap on a
  non-CSP strategy — the one case the decision was about. *(2026-09-27, W201.)*
- **A new real-book flag changes every older harness that books a real trade.** W195's
  sleeve flag (the live real sleeve is over 5%) turned every real long call in
  `verify_w201.py` into a W194 decline; that harness now switches the W195 flags off and
  says so. *(2026-09-27.)*
- **A harness that runs `helm snapshot` must switch off `exit_alert`** — on the Mac it
  raises a real macOS notification (osascript). *(2026-09-27, `verify_w198.py`.)*
- **A refactor that replaces a line RANGE must diff the module's defined names,
  before and after.** *(2026-09-24.)*
- **A fixture that depends on live state breaks when the data moves, not when the
  code does.** *(Again 2026-09-30: `verify_w180_step6` fails 12 identically on old and
  new code — W205.)*
- **The harness is usually wrong before the code is.**
- **Assert the whole line, not a substring; assert on the parsed thing.**
- **A count of zero deserves a control. A uniform result deserves the same
  suspicion.**
- **Prefer counting events to summing dollars.** Report a sum four ways — sum,
  median, share, drop-the-top — before saying what it means.
- **Split every finding into the old sample and the new one before believing it.**
  s122: the 28-DTE "big-loss" benefit, the give-back-on-CSPs gain and the 15-point
  band each held in one half and failed in the other. *(2026-09-26.)*
- **A number is about a population, and the population is half the claim.**
- **A conclusion one week old is a claim.** Recompute from primary records.
- **A behaviour metric cannot answer a performance question.**
- **Any study that replays marks must trim `checks` to `closed_at`.** 15 paper CSPs
  closed 06-29 → 07-16 carry GOOD `checks` rows up to two weeks after close (the
  journal stopped doing this ~07-21). *(Found s122, 2026-09-26.)* **The same holds for
  `leg_checks`: the snapshot still writes rows for CLOSED legs (W185) — trim through
  `exit_flags._live_at`.**
- **A hand-copied payload needs a hash gate, not a length check.**
- **A rendered claim must name the input it read.**
- **Verify a new check against a day you already know is BAD.** Known-bad fixtures:
  07-29 (reboot) · 08-06/07 (outage) · 08-09 (Sunday) · 08-12 (broker outage) ·
  06-17 and 07-23 (sign-inverted rows). Known-clean: 08-11, 08-14.
- **Regime breaks — split any cross-month study on them:** 2026-07-27, 08-03, 08-05,
  08-11. Dirty days to exclude: 07-29, 08-06, 08-07, 08-12. June entries carry the DTE
  start-up fault. Full register: `claude/HELM-regime-break-register.md`.
- **A percentage needs one base.** A ratio whose numerator includes later cash (re-sold
  rent) and whose denominator does not (the opening debit) reads as "down X% from Y" and is
  neither. State it in dollars against a fixed reference. *(2026-10-01, AA.)*
- **A render harness reads more than the database copy.** PG's card nav (previous / next /
  "N of M on the board") comes from the live board cache; a baseline and a candidate rendered
  minutes apart differ there with no code change. Strip it before a byte compare.
  *(2026-10-01.)*

## While operating

- **The handover and these rules are kept as a Mac mirror** in
  `~/Projects/helm/_records/`. At session end the handover (`HELM-handover.md`) is
  updated in place — what the desk is, where its files are, current state, open items,
  what to read first, readable in a minute — and published with `project_write
  local_path`, never re-typed whole and never as a dated copy. If the project copy
  differs from the mirror, someone edited it elsewhere: merge before publishing.
  *(Russ, 2026-09-30; handover replaces the live list and the checkpoint archive files
  2026-10-10.)* `HELM-live.md` is FROZEN at W209; W-numbers continue from W210 in the
  handover, never reused.
- **Status paragraphs are the least-checked claims in the project.** `0 0` after a
  fetch answers "pushed"; `git status --porcelain` answers "committed". Ask both.
- **Know what time it is before auditing a day.**
- **Never restart the engine 10:00–10:35, 12:30–13:05 or 15:15–15:50** — it kills a
  running snapshot via client id 11. *(W103.)* *(From W198 the 10:00 and 12:30
  snapshot processes also run the paper exit pass, ~6 more minutes; still inside those
  windows.)*
- **The working tree on the Mac IS production.** A snapshot, scan or agent runs
  whatever is on disk, committed or not — an uncommitted change is live at its next
  run. *(Restated 2026-09-27 for W195/W198.)*
- **`helm restart pg` does not clear `.pos_cache.json`.** A change to how a ROW is
  built needs "Refresh marks" on each book, not a restart. *(W182.)*
- **Never hoist a `helm` import to module scope in `helm-pg`** — every PG page renders
  blank. *(2026-09-23.)*
- **`~/.helm/env` holds a live API key. Do not print it.**
- **Never touch `com.cots.*` or `cots.local:8765`.**
- **Never address a row by the tail of its id.** Leg-id suffixes are not unique.
- **A measurement that mutates a surface the user can see is not read-only.**
- **Save the script, or the re-run becomes a re-derivation.** Any study meant to be
  repeated needs a frozen `--selftest`, not a memory. *(s122 scripts are saved but not
  yet frozen with a self-test; `helm/risk_cap.py` ships with one from day one — s123.)*
- **`helm settle` prints every proposal and applies the batch on one `y/N`.**
- **Bridge rules:** `python3`, `git`, `helm` only · 45s timeout · responses truncate
  near 1,000 chars · **send plain readable `python3 -c`, and have the page JS return
  only `j.output`** · reading repo SOURCE is refused · a failed `open(path,'wb')` has
  already truncated the target · absolute paths only. *(s122: running a
  session-written script through the bridge was refused; ask Russ to run it.)*
- **Market data from here:** Yahoo is blocked from the cloud container and the device
  VM; IBKR gives at most 120 days. **Long history comes from `_s122/px/`, refreshed by
  Russ on the Mac.**
- **Device-shell rules:** set `HELM_ROOT="$HOME/mnt/helm"` (without it `helm.config`
  falls back to a `~/Projects/helm` path that does not exist in the VM: "unable to
  open database file") · `pip3 install rich flask` first · it cannot delete · no git
  identity · clock is UTC · **a bare `git status` can strand `.git/index.lock` —
  `rm -f .git/index.lock` on the Mac before committing** · a `.bak` has no `.py`
  suffix, load it with `SourceFileLoader` · **a background process (e.g. a test server
  started with `&`) does NOT survive between separate device-shell calls — each call is
  its own sandboxed, unshared-PID invocation, so start it, use it, and stop it inside
  ONE call.** *(s123.)*
- **The device shell never writes the live database.** `helm.db` is in WAL mode and open on
  the Mac; a write from the VM is unsafe. A write script is tested on a `VACUUM INTO` copy in
  the VM (dry run, apply, second apply must refuse, diff the tables), then Russ runs it on the
  Mac; the script backs up first and refuses unless the rows are exactly as expected.
  *(s130, CSCO backdate.)*
