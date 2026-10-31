# HELM — standing rules, history

*Lines moved word for word from `claude/HELM-standing-rules.md` on 2026-10-31 (s142): decisions
about retired structures (the paper book, the old per-trade caps, long calls, spreads, the
pinned-diagonal booking) and decisions already marked superseded. Kept so the reasoning
behind today's rules can be found. **Nothing here is in force.** A line that is needed again
moves back to the current file unchanged. Mirror `~/Projects/helm/_records/`.*

## Who does what

- `book` is therefore also the actor field: a real close was a human, a paper close
  was a rule. The two books share an `exit_reason` vocabulary so they can be pooled —
  **but the same word means different things in each.**
- Rule-precedence questions were paper-book questions; with the paper book retired, a
  rule change is a change to what HELM TELLS Russ.
- Rule work has two possible products: a change to what the paper agent DOES, or a
  change to what HELM TELLS Russ. **Say which before building.**

## Settled decisions

- *(Superseded 2026-10-24: net of fees is now the P&L of record, from the Fidelity
  import — see the revision above. History: on 2026-08-26 a spec implemented fee capture
  as though decided and broke `helm close`; build net P&L from the import's fills, not by
  editing `helm close`.)*
- *(Superseded 2026-10-24 by "HELM advises in plain words" above.)* Exit flags were review
  triggers, not sell signals; the disposition is still a logged yes/no, never an
  auto-exit. *(s114/s115.)*

## Paper book

- **PAPER settles expiries by inference** from the last good expiry-day spot — OTM →
  0.00, ITM → intrinsic, within 1% of the strike or no mark → flagged. **REAL is
  asked, never inferred.** *(Russ, s110.)*
- **A paper leg with NO expiry-day mark settles from the official close**
  (`expiry.settlement_intrinsic`, yfinance) as an explicit fallback, and the ±1%
  guard cases settle the same way. *(Russ, 2026-09-20 — W177.)* **The guard that keeps
  W167 intact: the fallback WRITES `close_price`, and `settled_mark` keeps reading
  `close_price` first — so a settled leg still has exactly one value.**
- *(The paper rules in this list — inference settlement, the official-close fallback,
  hand-fired settlement, the paper exit agent — are history: the paper book was frozen on
  2026-10-24 and has no open positions. Their code stays.)*
- **Paper settlement stays hand-fired.** No agent, no tripwire. *(Russ, 2026-09-20.)*
  **Accepted consequence: every paper expiry Friday loses readings until a session
  runs `helm settle`, and the audit grades those days DEGRADED with the cause named.**
- *(Retired 2026-10-24 with the paper book: `PAPER_EXITS_RETIRED` in `paper_exit_agent.py`;
  the launchd job unloaded and its plist parked.)* **The paper exit agent decides after every snapshot, not once a day.** *(Russ,
  2026-09-27 — W198.)* **As built:** inside the snapshot process after the 10:00 and
  12:30 slots (any snapshot starting before 15:00 that covers PAPER); the scheduled
  15:35 run is the after-15:15 pass; a lock keeps two passes from overlapping; ledger
  note "after snapshot HH:MM"; off switch `HELM_W198_OFF=1`.

## Sizing

- *(Superseded 2026-10-24 by the sleeve limits above: the $5,000 per-trade cap and its
  one-contract decline (W160/W201), the 5% cash ceiling, the 5% long-premium sleeve
  (W194) and the long-call entry screen (W195, `lc-screen-v2`). Their code stays in
  `helm/risk_cap.py` / `helm/lc_screen.py` for the retired structures — long calls and
  puts, spreads, condors still size the old way until they are parked. Full text: this
  file's git history (`_records/`, before s134) and `HELM-archive-2026-09-27-s123*.md`.)*
  The long-call bar review is closed. *(Russ, 2026-10-27.)*

## Retired structures

- **The 20-point give-back line is the default exit on real long calls** (any that remain open). *(Russ,
  2026-09-27. Replayed on the real book: −$6,540 vs −$27,056 actual, 23 better / 6
  worse, consistent before and after the 25 Jul switch.)*
- **Credit spreads (bear call, bull put) close at 21 DTE, not 7.** *(Russ, 2026-09-26.
  The 7-day deadline's paper closes: 10 of 10 lost, median −363% of credit.)* **Only
  these two** *(Russ, 2026-09-27)*: the deadline is `strategy_settings.dte_exit_threshold`,
  one row per strategy, so changing it never moves condors, CSPs or covered calls.
  Switched on by `_s123/w192_apply.py` (W192), which refuses while an open spread sits
  inside the new window unless `--force`.
- **A REAL diagonal may be pinned from the chain, out of band**, booked as
  `MANUAL_PIN` with "PINNED OUT OF BAND" in its notes, permanently. *(Russ, s113.)*
