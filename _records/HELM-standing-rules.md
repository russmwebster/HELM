# HELM — standing rules

*Decisions that are settled, and lessons that were paid for. One line each, with who
and when. Read after `claude/HELM-handover.md`. **If a line here contradicts an item, this
file wins and the item is wrong.** A new line goes in only if it would change what a
future session builds; most sessions add nothing.*

*First cut 2026-09-20, curated from the lessons list and the decisions scattered
through `claude/HELM-worklist.md` (frozen). Russ reads it once before it is trusted.
Last updated 2026-11-03 (s149: the back-burner list is not the universe). Before that 2026-11-02 (s148: the proposal tools read strikes from the stock's IV; a card shows the rules the advice applies; device-VM installs; random chart ids). Before that 2026-11-02 (s147: new PMCC entries rank by rent cover; the delta limit kept at half the sleeve, reviewed in January). Before that 2026-11-01 (s146: one width test; put rolls keep the earnings veto; TMPDIR in the device VM). Before that 2026-11-01 (s145: IBKR's daily bars reach a year, not 120 days). Before that 2026-11-01 (s144: the book's market exposure shown in dollars, display only; device-VM harness notes). Before that 2026-10-31 (s142: no double entry as built; partial closes; called-away shares and dividends; net of fees from 10/24; retired lines moved to the history file; two harness lessons). Before that 2026-10-31 (s141: a process that called `get_ib()` holds client 11; the snapshot is on its own client numbers). Before that 2026-10-30 (s140: HELM commands are shown with `cd ~/Projects/helm &&` in front). Before that 2026-10-28 (s138: proposal tools are commands, recorded; earnings fail closed; assignment accounting; three covered-call choices; book limits built; a screen casts a wider net than its rules; -B on a perturbation). Before that 2026-10-27 (s137: Greeks follow trader practice; quality is the main risk control; the book's theta range and delta limit). Before that 2026-10-27 (rank-bar review: real trades only; CSP band 0.30 included; long-call bar review closed). Before that 2026-10-25 (s135: never REPLACE a cascading parent; a write test compares every table; HELM advises in plain words and flags every rule overstepped; paper book frozen, paper exit agent retired; how `helm settle --date` reads its date). Before that 2026-10-24 (s134: the revision — rulebook v0.2 approved: strategy set, sleeve limits, paper book retired, universe, net P&L, no double entry). Before that 2026-10-23 (s133: a named contract is a record, not a selection). Before that 2026-10-14 (s130: the device shell never writes the live database). Before that 2026-10-12 (s129: plain language, no rule or task numbers when talking to Russ). Before that 2026-10-12 (s128: explain first, wait for the go-ahead). Before that 2026-10-10 (s127: PG archived as a product; the board is HELM's interface). Before that 2026-10-10 (s126: the handover replaces the live list). Before that 2026-10-01 (s125: net rent banked, the diagonal loss limit, the four-question
diagonal card, two keep reasons; one base per percentage; live state in render harnesses). **Kept as a mirror on the Mac at
`~/Projects/helm/_records/HELM-standing-rules.md` — edit that copy, publish with
`project_write local_path`.***

---

## Who does what

*s142 (2026-10-31): lines about retired structures and superseded decisions moved, word for
word, to `claude/HELM-standing-rules-history.md` (mirror `_records/`) — the paper book, the old
sizing caps, long calls, spreads, the pinned-diagonal booking, the paper-era "actor" notes.
Any of them comes back unchanged if it is needed again.*

- **Russ trades the REAL book by hand — entry and exit, and sizes every trade. On the
  real book HELM informs; it never acts.** *(Russ, s107; HELM-193.)* **The PAPER book is
  retired** — no new paper entries from 2026-10-24, and **frozen the same day**: the 77 open
  paper positions were closed at their last marks with exit reason `RUN_OFF` (tag
  `paper_run_off_20261024`; never a rule result) and the paper exit agent retired. *(Russ,
  2026-10-24 — the 11/20 run-off was dropped once 40 paper diagonals were found to run to
  January.)*
- **HELM advises in plain words: each real position gets one recommendation (hold, take
  the profit, close or roll, decide on assignment, heads-up), with its reason and the
  number behind it. Russ decides and places every trade; HELM never acts. A
  recommendation without its reason is a defect.** *(Russ, 2026-10-24, s135 — replaces
  "anything reading as an instruction on a real-book surface is a defect" and "exit flags
  are review triggers, not sell signals". The recommendation is computed in the engine,
  one function; the board only shows it.)*
- **Whenever a position, the book or a proposed trade oversteps one of Russ's rules, HELM
  flags it for him to decide** — the rule, the number and the choices; HELM never resolves
  it. Flagged today (`helm/advice.py`): the sleeve over its share, a name over 20%, a sector
  over 30%, a held name graded below C or ungraded, earnings inside a covered call. **Earnings
  on a put or a diagonal are a mark on the holding line** (highlighted inside 7 days), not a
  "Needs you" item — no rule asks for anything then. *(Russ, 2026-10-25, s135.)*
- Russ commits and pushes. Sessions stage files by name and write the message.
- **Sessions explain any change first — what, which files, why — and wait for Russ's
  go-ahead before editing anything, records included. A question is not an
  instruction.** *(Russ, 2026-10-12, after s128 edited code on a question.)*
- **Talk to Russ in plain language — describe rules and work items by what they do,
  not by their W- or HELM-numbers** ("the spread-too-wide rule", not "W189"). Numbers
  stay in code, commits and records. *(Russ, 2026-10-12.)*
- **Every HELM command shown to Russ starts from HELM's folder:** `cd ~/Projects/helm && helm …`
  — never a bare `helm …`. *(Russ, 2026-10-30.)*
- **HELM works from REAL trades only — full stop.** The paper record is no longer evidence
  for any rule, study or decision; its rows stay in the database as history. *(Russ,
  2026-10-27.)*
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
- **The back-burner list is not the universe** (`claude/HELM-backburner.md`, mirror `_records/`). Names Russ wants
  kept in view off the watchlist; no HELM tool reads it, so nothing on it is offered. Checked only when Russ asks
  (`helm quality … --json`, run by Russ; the session updates the file). A name moves to the watchlist only by Russ's
  decision and only once it grades A–C, with a sector and an exposure group set. *(Russ, 2026-11-03, s149 — from the
  Book's GATE comparison; started with 16 data-center names.)*
- **CSPs:** 0.25–0.30 delta **(0.30 included — Russ, 2026-10-27)**, 30–45 DTE, IV rank ≥ 50, spread < 10% of mid; no entry within
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
- **New PMCC entries rank by rent cover** — the short's monthly credit ÷ the long's monthly loss of
  time value — among names graded A–C or approved funds; ties broken by yearly return on the long's
  cost; both shown, with the trend flag. *(Russ, 2026-11-02, s147 — practitioner reasoning, not
  evidence: yearly return would steer to the dearest options, where the long costs most. One PMCC per
  name builds a record slowly.)*
- **A position's card shows the rules HELM's advice applies to it, from the same place.** The PMCC card
  reads `helm/advice.py` `pmcc_rule_rows` (loss limit, 50% on the short with the resting price, 21 days,
  in the money in its last week, the long at 90 days, the next short's width floor) — the same constants
  and comparisons as the advice; the run-off diagonals keep their exit_flags rules on their cards.
  *(Russ, 2026-11-02, s148 — the NVDA card had shown the diagonals' breach, worthless and 25% harvest,
  which the advice never reads for a PMCC.)*
- **Measurement:** **net of fees is the P&L of record** (gross beside it; trades before
  7/13 stay gross, labelled "fees not recorded"); capital-weighted annualized return on
  capital at risk; benchmarks the money-market yield on the same cash and days and Cboe's
  PUT index; sample size and rule version beside every figure.

- **Greeks (Russ, 2026-10-27, s137) — from trader practice, not from HELM's own trades.**
  Per trade: **delta** picks the strike (the CSP band above); **vega** is the per-name
  "are its options rich" decision (IV rank ≥ 50) plus the size limits; **gamma** is handled
  by the 30–45 DTE entry and the 21-DTE exit; **theta** is the ranking (annualized return on
  collateral). No other per-trade Greek rule. **The ownership grade is the main risk
  control: rank by annualized return only among names graded A–C or approved funds** —
  ranked across all names, premium steers to the weak, high-volatility names that made
  the May–June losses. **Whole book:** daily **theta 0.05–0.12% of the sleeve** (the top
  is the warning: premium that rich means more risk; the bottom means the sleeve is idle)
  and **beta-weighted delta ≤ half the sleeve ($150,000 of S&P 500)**; outside either →
  a flag for Russ, like the sleeve, name and sector limits. Starting limits, reasoned not
  tested — review after a couple of months of readings. **Record gamma and vega on every
  new trade's entry snapshot.** *(Built s138: `bookview.limits` / `advice.book_limit_flags`,
  as fractions of the sleeve; HELM-held shares count in the book's delta.)*
- **The delta limit stays at half the sleeve — a flag, not a gate — and is reviewed in January with
  the theta range.** If by then it flags on most ordinary down days, judge it instead by the book's
  loss at a −5% S&P move against the sleeve. *(Russ, 2026-11-02, s147 — the s144 readings: the book's
  delta rises as the market falls, when correlations rise and assignments bunch; one week of readings
  is too few to retune on.)*
- **The book's market exposure is shown in dollars, for Russ to read, not as a rule (Russ, 2026-11-01,
  s144).** Today's market line (time decay a day · a typical S&P day's swing, from SPY's implied vol ÷ √252 ·
  the S&P fall that takes back a day's decay) and the Details risk curve (−10% … +10%, delta + gamma) are
  display only; the delta limit stays a flag as before. No delta-vs-S&P trend line (the s144 study: it tracks
  earnings and gamma). Fidelity's open orders can't be downloaded, so HELM does not check the buy-back orders.
- **Proposal tools are commands Russ runs — `helm puts`, `helm calls` — not scheduled jobs;
  every run is recorded (every name's best contract and each left-off name's reason), so
  the record builds even while the sleeve is full ("would be declined").** The regular
  check on open positions is the snapshots + `helm advise`. *(Russ, 2026-10-28.)*
- **Earnings fail closed on the proposal tools.** A stock with no upcoming date is offered
  a put only if it reported within the last 60 days; a covered call only if it expires
  before last report + 70 days. Otherwise it is left off with the reason. Funds have no
  earnings. A report later in a put's trade, even on its expiry day, is allowed and shown
  (DELL, Russ 2026-10-28).
- **An assigned put closes at $0.00 on the assignment date** (the premium is realized and
  counts in the sleeve); the position closes with exit reason ASSIGNED; the shares go in at
  the strike; the **effective basis** (strike − premium per share) is stored as a number on
  `stock_positions`. The import books it only with its share row and one matching CSP.
  *(Russ, 2026-10-28; `helm/assignment.py`.)*
- **Rolls (Russ, 2026-11-01, s146 — `helm roll`):** priced at the bid/ask, a net credit after both legs' fees;
  new short 30–45 days, delta up to 0.50 (flagged outside its band); each strategy keeps its own strike and earnings
  rules — **a put roll keeps the 10-day earnings veto**, a covered call no earnings inside, a PMCC short earnings flagged
  and the width test counting the buy-back. A below-C name's put roll is shown, flagged. HELM shows the alternatives
  beside the rolls, in dollars; it never places or books — the import books a roll.
- **Covered calls: three choices per holding**, ranked by the premium's yearly return on the
  shares' value; **the spread < 10% rule applies to calls too.** Booking called-away shares
  waits for import stage 2 (before the first covered call can expire). *(Russ, 2026-10-28.)*

- **No double entry, as built (Russ, 2026-10-31, s142).** Trades enter HELM only through
  `helm fills`. `helm open` evaluates only (`--confirm` refuses); `helm import`, `confirm-fills`
  and `activity` are retired; `helm reconcile` writes nothing. `helm help` lists 17 commands;
  the other 23 (close, assign, settle, resell among them) still run, listed by `helm legacy`,
  due to go on 2026-12-01 unless Russ keeps them.
- **A partial close splits the position (s142).** The closed contracts become their own CLOSED
  position (tag `split_from:<id>`, its share of the premium); the original stays OPEN with the
  rest and keeps its readings and entry snapshot. Same-day pieces of one order are one fill.
  Puts and covered calls only; anything else partial is reported.
- **Called-away shares and dividends (Russ's decisions 2026-10-31, built s142).** A covered call
  assigned: the call closes at $0.00, the shares leave at the call strike, and their result
  (strike − what HELM paid) is a CLOSED stock leg on the covered-call position, so the sleeve
  counts it. A dividend on HELM-held shares counts HELM's part only — dividend × HELM's shares ÷
  the account's shares from a Positions download within 7 days (else HELM asks for one).
- **Net of fees from 10/24 only (Russ, 2026-10-31 — the small version).** The sleeve and every
  trade since 10/24 are net; older trades stay gross, labelled "fees not applied" (the year's
  fees are about $16 a trade and change no study's answer). Fidelity can revise a row's fee
  after the trade: fees count **one row per trade, from the newest download** (`helm/netpnl.py`).

### Earlier settled decisions (in force unless marked superseded)

- **HELM is one track. HELM PG as a product to sell is archived; `helm-pg` is HELM's
  web board.** It holds no model (every threshold, gate and verdict is imported or read
  from the engine) and **changes anything only by calling HELM's own commands, never by
  writing the database.** Product-only work (licensing, installer, setup flow, pinned
  manifest, production server, data-provider layer) is dropped. *(Russ, 2026-10-10.)*
  **Archived, history only — never orient from them:** `claude/HELM-product-vision-and-status.md`,
  `HELM-product-development-plan.md`, `HELM-PG-wiring-pickup.md`, `HELM-trial-free-data.md`,
  `HELM-PG-where-the-product-stands.md`. Their status blocks describe July–August.
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
- **The 21-DTE deadline stays for CSPs and condors — as insurance, not edge.** The
  n≥120 re-run (W16, 2026-09-26) found 28 DTE loses on new trades and cuts no big
  losses there; the historic tail came from oversized trades, which sizing now handles.
  *(Russ, 2026-09-26; supersedes s107's "stays until the re-run".)*
- **CSPs: 50% profit target; no stop-loss, strike-breach or give-back exit.** Every
  such rule, replayed on the marks, made more trades worse than better in both the old
  and new samples. *(Russ, 2026-09-26.)* **A CSP whose bid-ask spread is ≥ 10% of mid at
  entry** (the chain row's `spread_pct`, which is what `entry_snapshots.bid_ask_spread_pct`
  stores; 10.0% counts) **is refused on paper** — the top pick only, never swapped for a
  lower-ranked contract, logged to `paper_refusals` as rule W189 — **and declined on
  real** like W160 (Enter records nothing; a typed count overrides, recorded "RULE
  OVERRIDE (W189)"). *(W189, built 2026-09-27; decline-with-override follows the W195
  pattern Russ approved.)*
- **Tilt capital toward premium selling**, where the book has shown an edge. *(Russ,
  2026-09-27.)*
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
- **Never save with `INSERT OR REPLACE` into a table other tables CASCADE from** (`positions`,
  `legs`, `checks`, `themes`). REPLACE deletes the row first, and the cascade deletes the
  children. `Leg.save` did this, so **every leg close wiped that leg's `leg_checks`**: 5,612
  rows in the s135 freeze (restored from its backup), and 161 of the 179 real option legs
  closed since 7/16 have no readings left. Fixed s135 to INSERT OR IGNORE + UPDATE, as
  `Position.save` already was. `Check.save` still REPLACEs; it is safe only because a check
  is never re-saved under the same id. *(2026-10-25.)*
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
- **The chain reader reads only strikes near the price** (`open_cmd.fetch_chain_from_ibkr`: ±3× the 14-day
  range) unless it is given a `strike_range`. On calm stocks that stops short of the bands: no 0.20–0.30
  short call on AAPL or XLE (s147), XLC/TGT/NVDA/VALE left off the put list with only a higher delta
  "nearest" (10-28 → 10-30). **Every proposal tool passes `helm/strikewindow.py`'s range, worked out from the
  stock's stored IV** (puts below the price, calls above, rolls always including the current contract;
  50% when no IV) — one function for `helm puts`, `helm calls`, `helm roll` and `helm pmcc`. *(s148.)*
- **The PMCC width test lives in one place: `helm/fillopens.py` `width_floor` / `width_test`.** The import, `helm advise`,
  `helm calls` and `helm roll` all call it; never write the arithmetic again. Three copies had grown; one let a strike
  exactly at the floor pass (a float: 62.900000000000006 > 62.9). *(s146, 2026-11-01.)*
- **A screen casts a wider net than its rules and applies the rules itself, so every
  rejection carries its true reason.** s138's put tool first borrowed `evaluate_contracts`
  with the rule band as its filter: an empty band read as "IBKR chain unavailable", fell back
  to Yahoo and was recorded "no live IBKR quote" — while IBKR was fine and the truth was
  "spread too wide" or "no 30–45 day expiry". A borrowed function brings its fallbacks.
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

- **A harness that reads the CLI's words must set `COLUMNS` wide** (rich wraps at 80 and splits the
  phrase being looked for), and **space CLI runs a second apart** — the import's backup name and its
  IMPORT_RUN event id are stamped to the second, so two runs in one second collide. *(s142.)*
- **A table comparison must count a changed column set as a change**, not crash on it (`EXCEPT`
  needs equal columns; s142's `broker_fills.downloaded_at`). *(s142.)*

- **A write test compares EVERY table's rows, not only the ones the change is meant to touch.**
  The s135 freeze was tested on a copy against positions and legs and passed; it had deleted
  5,612 `leg_checks` rows. *(2026-10-25.)*
- **A command that has never been run has not been verified.** `VACUUM INTO` a copy,
  `HELM_DB=` the copy, run the real CLI, dump the rows.
- **Verify both ways.** Perturb the defect you most fear, confirm the suite fails;
  restore and confirm it passes; check the file is byte-identical after.
- **Run a perturbation with `python3 -B`.** A same-length `sed` edit inside the same second
  leaves the `.pyc` looking current, and the old code runs: s138's first delta break "passed"
  that way.
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
  *(2026-10-01.)* **The cards' charts also draw their clipPath ids from a random number** (`ca21174`):
  normalise them too. *(s148.)*

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
- **Never restart the engine 10:00–10:35, 12:30–13:05 or 15:15–15:50** — it killed a
  running snapshot through the shared client numbers. *(W103.)* *(s141: cause fixed — the snapshot runs on
  31–33; the rule is lifted only after a restart during a snapshot goes through cleanly, recorded here.)* *(From W198 the 10:00 and 12:30
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
- **`helm settle` prints every proposal and applies the batch on one `y/N`.** **`--date=D`
  settles legs that expired BEFORE D**, so one run dated the day after the latest expiry
  covers every earlier one; an expiry on D itself is not included. *(s135: the handover's
  "10-16, then 10-23" was two runs where one dated 10-24 was needed.)*
- **IB Gateway refuses a second connection on a client number already in use** (it does not drop the
  first). **A HELM process that calls `helm.ibkr.get_ib()` keeps client 11 open until it disconnects**, so
  any later `check_connection()` in the same process is refused and reads as "IBKR not connected" — release
  it (`disconnect_ib()`) first. The scheduled snapshot runs on its own numbers 31–33 (`use_snapshot_ids()`);
  every other command uses 11–15, 17, 21, 99. A new number is agreed with the Book first. *(s141, 2026-10-31.)*
- **Bridge rules:** `python3`, `git`, `helm` only · 45s timeout · responses truncate
  near 1,000 chars · **send plain readable `python3 -c`, and have the page JS return
  only `j.output`** · reading repo SOURCE is refused · a failed `open(path,'wb')` has
  already truncated the target · absolute paths only. *(s122: running a
  session-written script through the bridge was refused; ask Russ to run it.)*
- **Market data from here:** Yahoo is blocked from the cloud container and the device
  VM; IBKR's daily bars go back at least a year (s145: NVDA 10-20-2025 → 10-30-2026 on a
  one-year request; the old "at most 120 days" was wrong for daily bars) — enough for a 200-day average. **Longer
  history comes from `_s122/px/`, refreshed by Russ on the Mac.**
- **Device VM, s146: run harnesses with `TMPDIR=/tmp`** — the session's TMPDIR is on a full disk, and SQLite's
  sort files for an `ORDER BY` over a big table fail there with "database or disk is full".
- **Device VM, s144:** its home disk can be full — put database copies in `/tmp`; install with
  `pip3 install --target /tmp/pylib rich flask` and run with `PYTHONPATH=/tmp/pylib` (s147: `--user`
  fails on the full disk); the board on a copy needs `~/.helm_profile` holding the account id
  (`get_active_account` reads it), or every page redirects to Setup.
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
