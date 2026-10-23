# HELM — handover

**This is the only document a session needs to be told to read. Do the steps below before any other work. The steps say when to open other files.**

## Start of session — do these in order
1. **Read `claude/HELM-standing-rules.md` now** (`project_read`). It holds every settled rule and paid-for lesson, and where it contradicts anything else, it wins. Don't skip it, even for a small task.
2. **There is one track: HELM.** The product idea (HELM PG) was archived 2026-10-10. `~/Projects/helm-pg` is HELM's web board, not a product; don't ask which track, and don't plan for outside traders.
3. **Before touching code or data:** ask for folder access to `~/Projects/helm` (and `~/Projects/helm-pg` for the board). Then, in the device shell, set `HELM_ROOT="$HOME/mnt/helm"` and run `pip3 install rich flask`. Read git with `git --no-optional-locks`.
4. To look up an old W-number, search `claude/HELM-live.md` (frozen) or `ISSUES.md` (frozen, HELM-ids). Never read either through.

## Rules that cause damage if missed (the full set is in the standing rules)
- **Explain any change first (what, which files, why) and wait for Russ's go-ahead before editing anything, records included. A question is not an instruction.**
- **Russ places every real trade by hand. HELM informs, never acts.** Anything on a real-book surface that reads as an instruction is a defect.
- **Firewall:** never touch `~/Projects/cots`, `cots.local:8765` or `com.cots.*`.
- **The Mac's working tree is production:** a snapshot or agent runs whatever is on disk, at its next run.
- **Never restart the board or engine during 10:00–10:35, 12:30–13:05 or 15:15–15:50** (it kills the running snapshot). Engine changes need `helm restart pg`; templates don't.
- **Before editing a file**, keep a `.bak-YYYYMMDD-sNNN` copy. **Verify** on a `VACUUM INTO` copy of the database with `HELM_DB=` pointed at it, block IBKR, and perturb the change to prove the test can fail.
- **Never print `~/.helm/env`** (it holds a live API key).
- **Russ commits.** Give him one command that names every file, checks `git status -sb` after pushing, and ends the message with the session's attribution lines.

## End of session
Update **this file** in place: edit the Mac copy `~/Projects/helm/_records/HELM-handover.md`, stage it, and publish it with `project_write local_path` to `claude/HELM-handover.md`. Never write a dated copy or an archive file. Update the state, the open items and the next free number. Add a line to the standing rules (edited and published the same way) only when the session earned one.

---

*Last updated 2026-10-23 (s132–s133: diagonal exit reads; Outcomes diagonal row on the long's cost; snapshot stops writing closed legs; reusable backdate script (GOOGL, CSCO, JPM CSP); JPM re-sell booking error corrected; `helm open` fetches a pinned expiry outside the entry window). **Next free number: W216.***

## What this desk is
HELM: Russ's options desk on the Fidelity account. **Russ places every real trade by hand; HELM advises and tracks, never acts.** HELM tracks only positions in its own `positions` table and may advise on anything; the rest of the account is Factor Lab's, GATE's or Russ's. Rules trade a separate PAPER book.

## Where things are
- `~/Projects/helm`: engine, CLI, `data/helm.db`, records mirror in `_records/`. `~/Projects/helm-pg`: HELM's web board, http://127.0.0.1:8770 (`com.helm.pg`; holds no model, changes things only by calling HELM's own commands). The engine server and Claude's exec bridge are http://helm.local:8766. Never touch `~/Projects/cots`.
- Device shell: set `HELM_ROOT="$HOME/mnt/helm"`; run `pip3 install rich flask` first. Russ commits and pushes.
- Project docs: `claude/HELM-standing-rules.md` (rules) · `claude/HELM-live.md` (frozen 10-10) · topic studies under `claude/` (search the project before recommending a change).
- Technical map: `ORIENTATION.md` in the repo.

## Current state
- Real book (11 open, re-read 10-23): **CSPs** on AMZN, CSCO, ABBV, GOOGL, HD, VST, WMT and **JPM (2 × $335, 20 Nov, 7.81 — `JPM-CSP-20261023-0630E4`, sold 10-22 by mistake as puts instead of calls; Russ kept it as a CSP, $67,000 secured; booked 10-23, backdated to 10-22 ~12:30)**; **diagonals** on EQT, IBM and JPM. **APLD diagonal closed 10-20, −$294 (STOP)** — rent covered three-quarters of the long's loss.
- **JPM diagonal** (`JPM-DIAGONAL-20261016-FE17CF`): long 12/18 $325C × 2; 11/13 $345C bought back 10-21 (+$818); **11/20 $340C × 2 sold 10-23 @ 4.82** (checked against Fidelity). The 10-22 short call booked in error (it was the put sale) was removed by `_s133/fix_jpm_resell_error.py` (leg, its event, the two 10-22 readings that priced it, 'bare' flag reopened; backup `data/helm.db.bak-s133-jpm-20261022-181200`).
- **Exit opinions given 10-20** (advice only): IBM hold through earnings (10-21 after close); JPM hold, don't buy back at the wide ask; EQT hold as a salvage with the loss limit as the line (EQT reports **10-27** after close — HELM has its earnings date as 10-20, wrong); APLD close (done).
- **Shipped this session:**
  - helm-pg `426bfa0` (pushed): Outcomes has its own **Diagonals** and **Covered calls** rows; diagonals' profit/risk is on **the long leg's cost** (was the opening net debit). Real diagonals: 8 closed, 50% wins, avg −4.8%, +$1,749.
  - helm `d3da047` / `cb3665a` (Russ): the snapshot **no longer writes `leg_checks` rows for CLOSED legs**, so open legs on those diagonals read GOOD, not PARTIAL (first run 10-21 10:00 clean, P&L unchanged); `tools/backdate_fill.py` (reusable, self-refusing; `--dte-only` for entry_dte).
  - **Uncommitted:** `helm/cli/open_cmd.py` — a pinned `--strike/--expiry` now always fetches the named expiry if the exchange lists it (JPM 11/20 was 28 DTE against the 30-DTE floor and couldn't be booked); and `_s133/` (the JPM correction script). Commit both.
- Backdated: GOOGL CSP → 10-12 ~15:00 (entry_dte 36→39); CSCO entry_dte 37→38; JPM CSP → 10-22 ~12:30 (28→29).
- **Data collection is healthy** (10-13 → 10-23: every slot covered the book bar ~1 paper multi-leg reading some days). Gaps: **Monday mornings** when IB Gateway's Sunday-midnight shutdown isn't followed by a login before 10:00 (10-06, 10-12 lost 10:00/12:30) — Russ's to do; **10-20 15:15 lost 16 diagonal readings** (GE→SU, a 3-minute broker-quote stall, real IBM and JPM included) — did not repeat 10-21.
- IB Gateway: HELM connects on **4002**; Russ's Gateway is on 4002 (paper login with live data shared — HELM asks for live/frozen data only, never delayed, so a lost data share shows as empty readings, not stale ones). HELM's code comments mislabel the ports (offer to fix: comments only).
- A nightly `helm.db` backup now runs outside HELM (Book of Books, 10-22): `com.russ.helm-nightly-backup`, 2:15, `~/Backups/helm/`, 14 days.
- **Scheduled task, Tue 10-27 9:00 ET** (read-only, on the Mac): numbers for the rank-bar review and the CSP delta band.
- s130 finding still standing: the long-call screen has passed nothing since 10-01 (calm names have rich options). s128 outcomes study: only CSPs made money in both books.
- **Paper expiries of 10-16 are NOT settled:** 11 paper diagonals still carry a short that expired 10-16 (their leg readings stay PARTIAL). Run `helm settle --date=2026-10-16`.

## Open items
**Decisions you owe:** W12 total deployment target · W64 the 83 sign-inverted journal rows · W93 do confirmation windows count checks or days · W114 refuse an off-slot snapshot · W157 data collection on a travelling laptop · W169 give-back card wording · real iron condors (s100 said none; NOW traded, closed 10-12) · AMAT's exit reason (booked PROFIT_TARGET) · a whole-trade target for diagonals.

**Next work:** **W213** a pinned expiry outside the entry window should fetch the named strike only — today it ranks every contract on that expiry, so `helm open … --strike --expiry` showed a 28-DTE $320 as "top pick" (`helm/cli/open_cmd.py`, `evaluate_contracts`; s133) · **W214** the 5% cash ceiling did not decline JPM's $67,000-secured CSP at confirm — find why before trusting it · `helm settle --date=2026-10-16` · `helm watchlist remove EVALUATE` (a junk row; the morning vol refresh fails on it daily, and it reads inactive rows too) · W195 rank-bar review 10-27 — start from the scheduled task's brief (CSP delta band in the same pass) · W208 diagonal card practice, then the field guide · W180 step 7 paper diagonal rules act · W212 `helm open` can't record a fill's real time — `tools/backdate_fill.py` now does it after booking (single-leg only); a `--filled-at` option would still be cleaner.

**Can misstate money:** **W215** EQT's stored earnings date is 10-20; the company reports 10-27 (check how `next_earnings` is refreshed) · W184 the scan can propose a new diagonal on a held long · W185/W206 missing `leg_checks` rows · W186 snapshots drop readings · the audit can't check multi-leg P&L · W159 no resting profit-target order · **W210 B diagonal (closed) booked at 1 contract against 2 filled** — its P&L is probably half the real figure.

**Housekeeping:** `.bak-20261020-s132`: `helm-pg/helm_engine.py` (after a clean day of Outcomes) and `helm/cli/check_cmd.py` (first run clean 10-21 — can go) · `.bak-20261023-s133`: `helm/cli/open_cmd.py` (after the commit and the next named-strike open) · **19 database backups in `data/`** — keep the newest three (now the JPM pair and GOOGL's 10-20), Russ deletes the rest · W211 delete the board's frozen sentinel fallback (`helm-pg/helm_engine.py:186`, a copy of part of the model behind a bare `except`) · `.bak-20261003-s126` files (3 in helm, 4 in helm-pg) can go after the first clean real spread or condor booking · `.bak-20261012-s128` (5 in `helm-pg/templates`) — the chart links have had their day on the board, so these can go · `.bak-20261014-s130`: `helm/thesis.py`, `helm/cli/open_cmd.py`, and in helm-pg `helm_engine.py`, `templates/thesis_diag.html`, `templates/positions.html`, `templates/outcomes.html` — can go after a clean day on the board and the first named-strike open from the board · `.bak-20261015-s131`: `helm-pg/helm_engine.py` — after a day on the board with the DTE fix · `data/helm.db.bak-s130-csco-20261014-124410` (pre-backdate) — keep the newest three database backups · `_to_delete/` in helm and helm-pg holds reverted s128 backups — Russ deletes.

Everything else open on 10-01 is parked. It stays in the frozen list until it's worked on; then it gets a line here.
