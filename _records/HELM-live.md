# HELM — live list (FROZEN 2026-10-10)

**FROZEN 2026-10-10 (Russ, s126). Not maintained; not a statement about today.** The open items
now live in `claude/HELM-handover.md`, which is replaced in place each session. This file stays
as the record of every W-number's text up to W209 (search it; don't read it through). Its
"next free number" line is stale: W209 was used on 10-02, so the next is W210, tracked in the handover.

---

**Next free number: W209** · Last checkpoint: **2026-10-01 (Cowork session, s125)** — the
diagonal card review and rebuild: open legs only, the effective basis, the rule-state block,
then the **four-question diagonal card** (`thesis_diag.html`) with the dollar ledger, your
moves, the money chart, rent vs decay, can-it-recover and cost-of-keep; the diagonal
**loss limit** rule (pos_stop in dollars, both states). helm `c732dc8 · f2c4114 · 627e2d2`,
helm-pg `fb59032 · 377f443`, pushed, PG restarted 16:26. Real AMAT diagonal closed +$2,285.
Record: `HELM-archive-2026-10-01-cowork.md`. Previous: `HELM-archive-2026-09-30-cowork.md`.
· Read this first, then `claude/HELM-standing-rules.md`. Nothing else at session start.

**⚑ How this file is kept (from 2026-09-30, Russ):** the working copy lives on the Mac at
`~/Projects/helm/_records/HELM-live.md` (standing rules beside it). A checkpoint stages that
file, EDITS it (no re-typing), and publishes it to the project with `project_write
local_path`. Never edit the project copy directly; if it differs from the Mac copy, the
project copy was edited elsewhere — merge before publishing.

**⚑ Next session (Russ, 2026-10-01): W208 — card-reading practice on the real diagonals,
then the diagonal field guide written from it.** Then the big build waiting is **W180 step 7**
(paper diagonals act). Small and owed: **run `helm scan`** so W195 starts collecting, and
**commit helm-pg `templates/positions.html`** (the "Holding for recovery" keep preset).

*Open items only, one line each. Finished items leave this list; their full record lives in
the archive.*

*Archives: `HELM-archive-2026-09-20.md` (W177) · `HELM-archive-2026-09-21.md`
(W176/W179/W180) · `HELM-archive-2026-09-23.md` (W181, the three harvests) ·
`HELM-archive-2026-09-23-pm.md` (W180 step 5) · `HELM-archive-2026-09-27.md`
(s122: strategy deep dives, W16 closed, W160 decided, long-sleeve cap) ·
`HELM-archive-2026-09-27-s123.md` (s123: W160, W194, W201, the W160 CSP extension —
committed `afaefd1`/`6fd3994`/`d927124`, pushed; W200 closed; W202 parked) ·
`HELM-archive-2026-09-27-s123b.md` (s123 second half: W195 and W198 built, the
calls made in the build, verification) · `HELM-archive-2026-09-29-cowork.md` (the
strategy audit and its correction, the diagonal re-run, exit flags cleared, W203, the
diagonal chart design) · **`HELM-archive-2026-09-30-cowork.md` (first unattended W195/W198
runs read, W88 slice 5 + W204 built and shipped, four paper expiries settled) ·
**`HELM-archive-2026-10-01-cowork.md` (s125: diagonal card review → four-question card,
effective basis, loss limit, rent vs decay, recovery, cost of keep)**.*
`HELM-worklist.md` and the two `-pending-` files are frozen as the s86–s120 archive.*

---

## This week

- **s125 shipped 2026-10-01** (helm `56a2090 · eee418c · 5b27079 · c732dc8 · f2c4114 ·
  627e2d2`; helm-pg `38c2782 · a28cb9f · fb59032 · 377f443`; pushed; PG restarted 16:26).
  Diagonals now render `templates/thesis_diag.html` (every other strategy unchanged, byte
  for byte). Harnesses: `tools/verify_s125_card.py` 1,470/1,470 and
  `tools/verify_s125_loss_limit.py` 5,833/5,833 (each with a perturbation that fails);
  `verify_w180_step5` ALL PASS (its pos_stop fixture updated to dollars). **Uncommitted:**
  helm-pg `templates/positions.html` (keep preset "Holding for recovery").
  *Check at next session:* **the loss limit's first unattended slots (10-01 15:15 onward)** —
  real flags should match the 10-01 measurement (no new ones). `.bak` files from s125: helm
  `helm/thesis.py.bak-20261001-s125{a,b,c,d,f,g}`, `helm/exit_flags.py.bak-20261001-s125d`,
  `tools/verify_w180_step5.py.bak-20261001-s125d`; helm-pg `templates/thesis.html.bak-…-s125{b,c}`,
  `engine_store.py.bak-…-s125{c,e,f,g}`, `app.py.bak-…-s125e`, `templates/thesis_diag.html.bak-…-s125{f,g}`,
  `templates/positions.html.bak-…-s125f`. **The `-s125d` (helm) and `-s125e` (helm-pg) files
  are the baselines of the two live harnesses** — removing them stops those harnesses. The
  rest may go once the first unattended day is clean. The four `*.bak-20260930-w88s5` files
  are no longer needed by any live harness (diagonals left `thesis.html`). Renders in
  `helm/_s125/` (untracked).
- **Real book 10-01 (Russ):** AMAT diagonal closed — long sold 116.35, **+$2,285 realized**,
  booked from the board as PROFIT_TARGET (see Decisions). 10:00 settled AMAT breach and IBM
  harvest50 as expected; **IBM now LONG ONLY with `long_giveback` + `bare` open.**
- **Two real keeps were recorded under the old paper-rule box's wording:** AA `pos_stop`
  kept 09-28 "paper rule holds", EQT `harvest25` kept 09-29 "paper rule would hold so
  holding." They described the paper agent, not the position. AA's keep has cost −$115 to
  the 12:30 10-01 check (the card now shows it). Russ may re-decide.
- **W195 has produced NO data yet.** No paper or real position opened 09-26 → 09-30; no agent
  runs `helm scan` (candidates are on demand, Russ 09-24), so no scan footer, no W195 paper
  long calls, and `paper_refusals` does not exist yet (created on first write). **The
  ~10-27 review clock is running on nothing.** Run `helm scan` to start it.
- **W198 confirmed working** (09-28 → 09-30): the paper exit pass ran after every 10:00 and
  12:30 snapshot plus 15:35; `audit eod` shows the "post-snapshot: paper exits" PASS lines.
  Closes it made: 09-29 10:00 ISRG CSP +$580 (target), AAPL LC −$360, MSFT LC −$25 (give-back);
  09-30 12:30 PANW CSP +$612 (target), DE LC −$1,890 (give-back).
- **Exit flags, open at checkpoint (real):** DE-LONG_CALL-20260924 thesis; IBM diagonal
  `long_giveback` and `bare` (10-01). AA `pos_stop` (kept 09-28) and `harvest25` (kept 09-30)
  still firing. *(09-30's AMAT breach and IBM harvest50 were settled by 10-01 10:00, as
  expected.)*
- **09-30 09:35: IBKR unreachable and Yahoo returned empty frames** for ^GSPC/^VIX; the
  10:15 retry recovered IVR 110/110, but the 09-29 `market_context` row has VIX and breadth
  NULL (audit WARN). *Bears on W195: if ^GSPC is empty at scan time the S&P gate reads
  unknown and the screen books no long calls.*
- **⚑ W195 rank bar 0.72 is PROVISIONAL — review after a month of paper results
  (~2026-10-27).**
- **The real long-premium sleeve is OVER its cap** (≈ 8.7% vs 5% on 09-27; not re-measured
  after 09-30's closes). **Every real long call, call diagonal and PMCC is declined**
  (typed count overrides, recorded) until it is under 5%. Nothing force-closed.
- **7 REAL positions already over the $5,000/trade cap** (from before the rule; AAPL LC
  closed 09-30): META CSP $12,034, AMAT DIAGONAL $7,660, GE CSP $7,432, DE LONG_CALL $7,233,
  PANW CSP $6,307, IBM DIAGONAL $5,960, MSFT DIAGONAL $5,218. Flagged only.
- *(Still carried, not re-checked:)* the PAPER board cache (W182) needs **Refresh marks**.

## Decisions only Russ can make

- **W64** · the 83 sign-inverted journal rows: repair, flag, exclude or accept.
- **W93** · do confirmation windows count checks or days? Decide once, for all three
  readers.
- **W12** · deployment target and the book-level number. *Per-trade risk, the
  one-contract decline and the long sleeve are decided and built. Still open: the total
  deployment target.*
- **W114** · should a snapshot refuse to run when it fires far from its slot?
- **W157** · keep collecting on a laptop that travels, or move collection off it?
- **Real iron condors**: s100 said no new ones, and one is open.
- **W169** · what the give-back card says when the high was never a gain. Decide with W121.
- **AMAT's exit reason:** booked PROFIT_TARGET, but no diagonal rule has a whole-trade target;
  DISCRETIONARY describes it. A one-row update, offered, not done.
- **A whole-trade profit target for diagonals?** None exists (the stored `profit_target_pct`
  0.5 is read by nothing). Open; the long-call evidence argues for give-back, not a target.

*(Decided 2026-10-01 (Cowork, s125): net rent banked = every short sale's credit minus every
buy-back's cost; the diagonal's position stop is the LOSS LIMIT (half of what the long cost,
in dollars, both states); the long's own rules stay on its original price; the diagonal card
is organised around four questions, in dollars; "holding to keep renting" and "holding for
recovery" are legitimate keep reasons. See standing rules.)*

## In progress

- [~] **W88** · thesis panel. Slice 5 shipped 2026-09-30; **diagonals moved to their own
  four-question card 2026-10-01 (s125).** Slices 3 and 4 remain for the other strategies.
- [~] **W180** · the diagonal workflow. Steps 1–6 live (**step 4's effective basis shipped
  2026-10-01, s125**). Remaining: **(7) the paper rules act.** *Spec: `HELM-W180-diagonal-harvest-workflow-spec.md`.* *(Step 7 is the
  highest-value build for the paper book — until it lands, the paper book's diagonals are
  managed by the 21-DTE long-leg calendar alone.)*
- [~] **W208** · **reading the diagonal card (Russ, 2026-10-01): practice on the real
  diagonals one at a time, then the diagonal field guide** written in the card's terms from
  what the practice turns up. Next session.
- [~] **W207** · **carry the four-question layout to the other strategies** (long calls,
  CSPs, condors) once the diagonal card has been in daily use. Russ's call when.
- [~] **W158** · exit-flag decisions. **Tick it** unless Russ wants the seeded-row
  cosmetics fixed first.
- [~] **W163** · booking a REAL diagonal. Tick it when Russ says which surface he used.
- [~] **W151** · broker-fill confirm. Revive it with `positions.fills_confirmed_at`
  alone; no fee capture.
- [~] **W135** · exposure. The READ is on the board; the paper GATE needs a number.
- [~] **W144** · the leg-mark gate was fixed in s116. Still owed: the `audit eod`
  assertion that every open multi-leg position has leg rows. *W185 bears on it.*
- [~] **W148** part 2 · should the six yfinance-only evaluators use IBKR?
- [~] **W118** · the agents brief. Near-misses exist in `exit_considered`, but the
  page does not show them.
- [~] **W109** · diagonal entry bands. `fix_diag_bands_s103.py` is written but has never run.
- [~] **W71** · entry bands are enforced for five strategies. DIAGONAL (W109) remains.
- [~] **W42** · launchd plists in the repo: two of eight are tracked. *(s123: the repo
  copies are stale — `com.helm.paper.exits.plist` says 15:55 and
  `com.helm.snapshot.daily.plist` says 15:45; the live agents run 15:35 and 15:15.)*

## To build — decided in s122 (suggested order: guardrails, then paper rules, then records)

- **W190** · **post-close shadow marks** for paper CSPs and condors: keep marking after
  close until expiry, in its OWN table (never `checks`). Tests hold-past-50%, hold a
  winner past 21, and condor target 25% vs ~50%.
- **W191** · **pattern tags on paper entries (log only):** CSP delta 0.20–0.25, IV rank
  80+, Basic Materials; IV rank ≥ 75 at 28 DTE. Confirm or retire in ~a month.
- **W197** · **record at every long-call entry:** the market's implied probability of
  reaching breakeven, and call-vs-put IV (the untested options-market direction signal).
- **W98** · bid/ask capture is single-leg only — needed before the spread gate can be
  tested on condors and spreads.

## To analyse — owed

- **W195 review (~2026-10-27)** · after a month of paper results: is 0.72 the right
  bar? Read the W195 paper long calls (`origin_screen='LC_SCREEN'`, signal version
  `lc-screen-v2`), the W194 rows in `paper_refusals`, and the scan footers. *(None exist
  yet — see This week.)*
- **W196** · **index calls (≤ 1/3 of the long sleeve):** cost check first — index
  options' larger volatility premium, contract choice, how to compare with single names.
- **W193** · does the bid-ask spread still track CSP results below 10% (gradient or
  cliff)? Bands 0–3 / 3–6 / 6–10%, old vs new. Same for condors once W98 lands.
- **W199** · **diagonal deep dive** — the one strategy not yet studied. **Paper diagonals
  alone hold 10.6% of the book** (long legs $73,171 of $688,297, 2026-09-27), which is why
  the W195 paper cap leaves them out (Russ). *(2026-09-28 re-run:
  `HELM-diagonal-deep-dive-2026-09-28.md`. Position-level question still open. The first
  re-sold shorts settle mid-October.)*
- **W174** · is a fixed 20-point give-back band right? *s122 replay: 20 is the
  consistent band on real long calls; keep until the 15/25 counterfactual arms have data.*

## Open — can lose or misreport money

- **W184** · **The scan still does not know a long is held.** It could route a
  fresh AA diagonal. The correct path now exists (`helm resell`), and the hazard is
  booking the scan's proposal through `helm open`. Close this once the scan marks a held
  LONG ONLY ticker with "re-sell with helm resell" instead of a new entry. *(W195 covers
  long calls; diagonal routing is still uncovered. Four more real diagonals went LONG ONLY
  on 09-30: AMAT, APLD, B, IBM.)*
- **W185** · **`leg_checks` has no row for the primary leg on some positions.**
  AA, EQT and UNH each have long-leg rows and zero short-leg rows for their first shorts.
  *(2026-09-30: those first shorts DO have readings — `exit_flags._diag_rows` takes the
  primary leg's mark from `checks.current_price` (AA 43, EQT 39, UNH 31 checks, values
  match the sale and buy-back). **New: every snapshot slot re-prices CLOSED short legs** —
  19 "PARTIAL … not-live" lines per slot on 09-28/09-29, 15 of them closed legs, writing
  45 `leg_checks` rows on 09-29 alone that repeat the settle price. That is the likely
  source of the 280 post-close rows. Readers that go through `_live_at` (the rule, the
  W88 panels) ignore them. Find the snapshot's leg selection next.)*
- **W159** · place the profit-target closing order at entry, GTC. *s122: the once-a-day
  paper decision cost three CSP winners — a resting order is the real-book version of
  W198.*
- **W140** · `GOOGL-IRON_CONDOR-…` carries ticker `GOOG`.
- **W141** · the book and the broker disagree on AAPL and MRK. Get a fresh export first.
  *(AAPL LC closed 09-30.)*
- **W137** · watchlist tranche 2. It waits on a trigger, not a date.
- **W19** · has a journal gap ever changed an exit decision? *Two closed gaps with
  known bounds: ABT 09-15→09-22, VALE 09-17→09-22.*
- **W121** · the negative-peak floor is agreed doctrine but is not stated in code.
- **W94** · a point of debit is not a constant amount of stock movement.
- **W20** · rolling reopens the same strategy rather than the one signalled.
- **The audit cannot check multi-leg P&L.** `pnl arithmetic` is single-leg only, so
  every diagonal, condor and spread reading is unverified by construction.
- *(Unnumbered, seen in the W201 build:)* the **board's** suggested size for spreads and
  condors still comes from the pre-W160 strike-collateral rule (`helm_engine.evaluate`
  passes a strike and a credit), while the CLI sizes them on max loss. The two can
  disagree; the CLI prompt is what records.

## Open — silent failures and stale data

- **W206** · **The real AMAT diagonal's long never had a `leg_checks` row** (0 ever; its
  short did). Its rule readings and card worked from the whole-trade line, but the LONG ONLY
  rules could not have read it. Only AMAT among diagonals (checked 10-01). Find why — W185's
  family.
- **W205** · **`tools/verify_w180_step6.py` fails 12 cases — identically on the pre-W204
  code** (checked 2026-09-30 in a temp tree). Its fixture assumes AA is LONG ONLY with no
  short; AA was re-sold 09-25. A fixture that depends on live state: give it its own
  constructed position, or clone AA's legs as they stood. *(`verify_w203.py` also cannot
  run any more: its baseline `.bak` was removed 09-29 as agreed.)*
- **W186** · **Snapshots lose readings.** 09-24 15:15 lost 10, 09-25 15:15 lost 5 (all
  DIAGONAL), **09-30 10:00 lost 3 (paper: DE LC, IBM condor, VALE diagonal — VALE was one
  of the four unsettled expired shorts)**. Audit grades such days DEGRADED. Check the
  multi-leg reading path before accepting "quotes not live". Ties to W185.
- **W187** · **The audit's field-completeness check WARNs on DIAGONAL buffers every
  day** — correct for LONG ONLY diagonals; teach the check, or it hides a real NULL.
- **W182** · **`positions_cached` serves at ANY age and survives restarts.** A change
  to how a ROW is built needs **Refresh marks**. Is the staleness declaration loud enough?
- **W183** · **The bidless-short population was never counted.** One query, owed since 09-22.
- **W54** · the census of parameters with no writer and writers with no reader.
- **W113** · an agent that cannot connect writes no run row (snapshot agent).
- **W119** · the run-note writer silently caps at 12 names.
- **W128** · the 15:15-slot question and SO's dropped marks remain. The 15:15 ledger
  row carries no `slot`.
- **W102** · the exit agent derives SHORTFALL/EMPTY on healthy days. *(W198 adds two
  more exits rows a day with the same status words; `audit eod` reads them as
  post-snapshot passes.)*
- **W103** · `helm restart` kills a running snapshot via client id 11. *(s123: so does
  anything else that calls `helm.ibkr.get_ib()` during a slot.)*
- **W104** · `helm reconcile` and `helm activity` match without quantity or direction.
- **W107** · the ownership gate gates nothing.
- **W108** · the stop A/B is inert, and its switch still reads on.
- **W112** · newly listed tickers and the two fixed lookbacks.
- **W132** · the pin path writes no risk fields.
- **W133** · `lifecycle_events` stores UTC where `positions` stores local time.
  *(2026-09-30: `legs.created_at` is local, like `checks.checked_at` — W204 relies on it.)*
- **W136** · `watchlist.sector` cannot express thematic exposure. Blocks W135.
- **W139** · nothing tells Russ when the scan's composition has changed.
- **W142** · `helm ivr list` renders a fabricated IVR of 100 and 0.
- **W154** · `helm.py` discards every return code, and turns a `ModuleNotFoundError`
  raised anywhere inside a command into "Command X is not yet implemented".
- **W155** · ten models map `SELECT *` into a constructor. *`lifecycle_events` also
  CHECK-constrains `event_type`.*
- **W65** · a Saturday scan reads as today's context on Monday.
- **W86** · the 09:35 refresh vs the gateway warm-up. *(09-30: 09:35 could not connect;
  10:15 retry recovered.)*
- **W23 / W24 / W26** · grades, earnings and entry vol context have no refresh.
- **W27 / W18** · per-leg greeks, and what the condor panel reads.
- **W62** · net-delta swing while the market is shut.
- **W68** · the `float − NoneType` verdict fault. Zero occurrences since s93.
- **W77** · ex-earnings measure: gate or display? Revisit late October.
- **W80** · the IV-rank ceiling promotion review.
- *(Unnumbered, seen 2026-09-30:)* `close_svg` names its clip paths from Python's
  `hash()`, randomized per process — two renders of the same card differ in those ids.
  Harmless in a browser; a byte-compare harness must fix `PYTHONHASHSEED`.

## Open — housekeeping and the product

- **W202** · **PARKED (Russ, 2026-09-27) until put diagonals are traded.** Booking a
  REAL put diagonal crashed before writing (`diagonal._confirm_diagonal_put` looks up
  `accounts WHERE name = ?`; there is no `name` column). It now **refuses before any
  prompt** and the board offers no Log for it. The real fix, when wanted: the account
  lookup, and book through `open_multileg_with_snapshot` like `_confirm_diagonal`.
- **W52** · a scheduled database snapshot. *Twenty sessions have done it by hand.*
- **W55** · iron condors are opened by rank.
- **W57** · `helm settings` is registered, but the module does not exist.
- **W72** · duplicate rows on the PG open board.
- **W99** · one Safari web app, not two.
- **W138** · GOOG and GOOGL are one company to any pooled study.
- **helm-pg hygiene, unnumbered:** twenty-two `.bak` files in the repo root and a
  `_to_delete/` folder.
- **helm hygiene, unnumbered:** ~25 untracked files in `Claude outputs/` (old commit
  scripts, drafts, `W88s5-AA-diagonal-card.png`) plus untracked `_s108/`, `_s111/`,
  `_s121/`, `_s122/px/`, `_s124/`, and now `_records/` (this file's mirror — commit it or
  ignore it, Russ's call) — commit, ignore or remove.
- **PG, before any customer:** **W34** read-only is a convention · **W35** health
  signals · **W36** one broker connection · **W37** auth and CSRF · **W38** a real
  server and pinned deps.

## Standing facts

- **Agents:** snapshot 10:00 / 12:30 / 15:15 · paper exits 15:35 **plus, from W198,
  inside the 10:00 and 12:30 snapshots** (ledger note "after snapshot HH:MM"; printed
  into `snapshot_daily.log`; off switch `HELM_W198_OFF=1`) · ivr 09:35 (+10:15 retry;
  writes `market_context` first) · audit 16:15 (read-only, 19 assertions) · gwwatch
  every 10 min · sampler every 10 min. All stand down on non-session days. **The
  snapshot is a fresh `helm.py snapshot --all` process, so a code change is live at the
  next slot with no restart.** It ends with `exit_flags.settle_closed()` + `scan()`,
  then the W198 paper exit pass. **No agent runs `helm scan` (the entry scan); it is run
  by hand.**
- **The diagonal card (s125, 2026-10-01):** diagonals render `helm-pg/templates/thesis_diag.html`
  (chosen in `app.thesis_page` when `card.dview` is set; every other strategy keeps
  `thesis.html`). Built by `helm.thesis.diag_view` from `exit_flags._legs` + `_diag_rows`
  (the rule's own readings), the exit_flags rows, and the latest check's per-leg bid/ask,
  theta and IV (`engine_store.thesis_card`). Order: At a glance (where I stand · asking for
  a decision · next line · dates) → Is anything asking for a decision? (can it recover · is
  waiting paying · what has keeping cost · the rule table by layer) → Your moves → Where do I
  stand? (dollar ledger, even line, rent vs decay, money chart: whole trade vs the long
  alone, even, loss limit) → What is this trade? → Detail (the legs against their own rules
  = the W88 slice 5 panels, day by day, notes). Closed diagonals get the record version. A
  failure falls back to the old card with the error shown. **Net rent banked** = Σ short
  credits − Σ buy-backs, as of each check's time (`thesis.diag_basis`); **effective cost**
  = (long cost − net rent) per share. **Recovery** = (strike + effective cost − spot) ÷
  (spot × the long's IV × √(days/365)); bands < 0.75 / 0.75–1.25 / > 1.25, as the long-call
  card. **Rent vs decay** = leg thetas × contracts × 100 × 7. **Cost of a keep** (loss rules
  only, while the latest flag of that kind is a keep) = whole trade now − at the keep day's
  last check.
- **W204:** `exit_flags._legs` sets `live_from = legs.created_at` on a leg opened on a
  LATER day than the position's earliest leg (a re-sell); `_live_at` refuses such a leg
  before that time on its booking day. Legs booked with the position get none (AA's s111
  re-create at 18:47 keeps its 15:16 reading).
- **Long-call screen (W195):** `helm/lc_screen.py` `lc-screen-v2 (W195)`; constants
  `RANK_BAR = 0.72` (provisional), `MAX_NEW_PER_DAY = 2`. S&P from
  `market_context.spx_vs_200_now()` at scan time. Real flags:
  `risk_cap.apply_w195_real_flags` inside `suggest_contracts` (sleeve: LONG_CALL,
  DIAGONAL, PMCC; one per name: LONG_CALL). Real verdicts for the footer and the
  board: `risk_cap.w195_real_verdicts`. Paper sleeve:
  `risk_cap.w195_paper_sleeve`. Replays: `_s123/w195_rescore.py`,
  `_s123/w195_replay_daily.py [HOLD]`. *The scan footer is printed only, never logged.*
- **`helm risk`** (s123): W160/W194 report, same shape as `helm exposure` — reads
  the book as booked, gates nothing. `helm/risk_cap.py` is the shared math both this
  and the `helm open` suggestions use; find every reader there before changing a formula.
- **Sizing wording has one source** (W201): `risk_cap.size_decision` writes every
  sizing line the CLI, the board and the paper book show; `override_line`/`override_note`
  write the override the CLI prints and the notes record; `OverCapRefusal` writes the
  paper reason. `suggest_contracts` returns `(contracts, binding, note)` — **its callers
  are in both repos** (`helm-pg/helm_engine.py` too).
- **Override and refusal records:** a real override → `positions.notes LIKE
  '%OVERRIDE (W%'`; a paper refusal → one row in `paper_refusals` (created on first
  write; `rule` W160, W201, W194 or W189). No-IV CSPs: `WHERE reason LIKE 'refused: no
  IV%'`. W195 sleeve refusals: `WHERE rule='W194'`.
- **A test harness that BOOKS a position must block IBKR** (client id 11 is the
  snapshot agent's) — `_s123/verify_w201.py` shows the pattern. **A harness that runs
  `helm snapshot` must also switch off `exit_alert`** — `_s123/verify_w198.py`.
- **A harness that renders PG pages in the device VM needs an active account:** set
  `HOME` to a temp dir holding `.helm_profile` = `fidelity_9e60c8`; assert status 200 and a
  body first. **Moving HOME hides the user site-packages (flask): a child process needs
  `PYTHONPATH=site.getusersitepackages()` taken BEFORE HOME moves.** `ib_insync` is not
  installed in the VM. *(`tools/verify_w88s5.py` is the current template: baseline tree
  from the `.bak` files in a temp dir, both trees rendered in children with
  `PYTHONHASHSEED=0`.)*
- **Diagonal exit-flag kinds (W180 step 5):** SHORT ON: `pos_stop · breach · worthless
  · harvest50 · harvest25`. LONG ONLY: `pos_stop · long_stop · long_giveback · long_dte7 ·
  long_dte21 · bare`. **`pos_stop` is the LOSS LIMIT since s125 (2026-10-01):** the whole
  trade's `pnl_unrealized` ≤ −`LOSS_LIMIT_FRAC` (0.5) × what the long cost, in BOTH states;
  label "whole trade past its loss limit". Log a hold with `helm pending keep TICKER --kind bare --reason
  "..."`, or the board's **keep** panel (W203 presets). A leg close settles the short's
  flags itself — at the NEXT snapshot.
- **`helm restart pg` does NOT clear `.pos_cache.json`.** A change to how a ROW is
  built needs **Refresh marks** on each book. *(W182.)*
- **`helm close TICKER --leg ID|ROLE --price P [--reason R] [--yes] [--position-id
  ID]`** closes ONE leg and leaves the position OPEN. It refuses the last open leg.
  Reasons: `HARVEST · WORTHLESS · BREACH · DTE_MANAGE · DISCRETIONARY`. **REAL book
  only.** The PG board's "buy back short" drives it with the full leg id.
- **`helm settle` has no `--dry-run`/`--apply` pair.** One `y/N` applies the batch;
  `--date=YYYY-MM-DD` narrows it. **It is run by hand, by decision.** A session can
  predict its proposals from the last GOOD expiry-day check's spot vs strike.
- **Engine changes need `helm restart pg`**; templates and CSS do not. Never restart
  10:00–10:35, 12:30–13:05 or 15:15–15:50.
- **Bridge:** `python3`, `git` and `helm` only; 45s timeout; responses of ~1,000 chars.
  **The extension refuses base64-wrapped payloads and char-code construction.** Plain
  `python3 -c` works when the page JS returns **only `j.output`**. Reading repo SOURCE
  is refused on the bridge. *(Sessions since 09-29 use the device shell instead.)*
- **Price history:** Yahoo is blocked from both the cloud container and the device VM
  (egress policy); the IBKR connector returns at most 120 days. **Ten years of daily
  prices for the watchlist, SPY and 11 sector ETFs are in `_s122/px/`** (fetched
  2026-09-27 by Russ; `_s122/fetch_px.py START COUNT` refreshes, run on the Mac).
- **Device shell** (folder access to `~/Projects/helm` and `~/Projects/helm-pg`): the
  tool for reading and patching code. **Set `HELM_ROOT="$HOME/mnt/helm"`**; run `pip3
  install rich flask` first. It cannot delete, and its clock is UTC. **Read git with
  `git --no-optional-locks …`.** No `sqlite3` binary: use `python3` with `?mode=ro`. A
  background process does NOT survive between separate device-shell calls.
- **PG imports `helm` LAZILY, inside each function.** **Never hoist a `helm` import to
  module scope in `helm-pg`**: it blanks every page.
- **`helm` is a shell alias.** A script run with `sh` cannot see it, so a commit
  script must call `python3 ~/Projects/helm/helm.py …`.
- **Git:** name files explicitly; check for `0 0` after a fetch AND check
  `status --porcelain`. *(09-29: a push can silently not happen — `git status -sb` after
  `git fetch` is the check.)*
- **Verification rig:** `VACUUM INTO` a copy, point `HELM_DB=` at the copy and run the
  real CLI. **Start the module the way production starts it.**
