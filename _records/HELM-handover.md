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

*Last updated 2026-10-17 (s131: diagonal DTE fix on the board; GOOGL CSP booked, not yet backdated; desk charters v0.8 and v0.9; Theme Radar retired). **Next free number: W213.***

## What this desk is
HELM: Russ's options desk on the Fidelity account. **Russ places every real trade by hand; HELM advises and tracks, never acts.** HELM tracks only positions in its own `positions` table and may advise on anything; the rest of the account is Factor Lab's, GATE's or Russ's. Rules trade a separate PAPER book.

## Where things are
- `~/Projects/helm`: engine, CLI, `data/helm.db`, records mirror in `_records/`. `~/Projects/helm-pg`: HELM's web board, http://127.0.0.1:8770 (`com.helm.pg`; holds no model, changes things only by calling HELM's own commands). The engine server and Claude's exec bridge are http://helm.local:8766. Never touch `~/Projects/cots`.
- Device shell: set `HELM_ROOT="$HOME/mnt/helm"`; run `pip3 install rich flask` first. Russ commits and pushes.
- Project docs: `claude/HELM-standing-rules.md` (rules) · `claude/HELM-live.md` (frozen 10-10) · topic studies under `claude/` (search the project before recommending a change).
- Technical map: `ORIENTATION.md` in the repo.

## Current state
- Real book (8 open, re-read 10-17): **CSPs** on AMZN (2 × $245, 20 Nov), CSCO (3 × $105, 20 Nov; backdated to its 10-13 15:00 fill), ABBV (1 × $270, 20 Nov) and **GOOGL (1 × $335, 20 Nov, 8.37 — filled 10-12 ~15:00, booked 10-15 16:02, NOT yet backdated)**; **diagonals** on EQT, IBM, APLD (each with a short on; APLD on its fifth) and JPM (opened 10-16). No long call: **DE closed 10-16, −$3,588 (THESIS_BREAK)**. UNH diagonal closed 10-14, +$385.
- The long-premium sleeve is **$15,397 = 2.29%** of $672,560.81 (balances 10-15): four diagonal long legs. Under the 5% cap, so new real longs are no longer declined on sleeve grounds.
- **s131 shipped and pushed (helm-pg `35f6c34`):** the Positions page's diagonal "dte s/l" counts open legs only (bought-back and expired shorts had kept their old expiry: EQT/IBM read "1 / 64"). **Needs `helm restart pg` + Refresh marks on both books** — check it was done.
- **Desk charter v0.9 is current (2026-10-17)**: `~/Projects/charters/desk-charter-helm-v0.9.md` (project copy `claude/desk-charter-HELM-v0.9.md`). v0.8 (same day) brought the charter up to date from 09-27; v0.9 logs Theme Radar's retirement. §4 diffed both times.
- **Theme Radar retired (Russ, 2026-10-17).** Its three scheduled tasks are deleted. Kept as the record in the project: `claude/theme-radar-archive.md`, `claude/theme-radar-universe.md`, and the note `claude/theme-radar-RETIRED-2026-10-17.md`; the app page, latest brief and criteria docs are deleted. HELM's `themes` / `theme_tickers` tables and `helm theme` are untouched (records; nothing reads them). Russ may delete the stale "theme-radar" desktop artifact.
- s130 shipped and pushed (helm `bb5de99`, `7c9b546`; helm-pg `93601b4`, `185bb12`):
  - Diagonal cards: "If closed today" — the whole trade with every open leg closed at the quotes (long at bid, short at ask; mid when no quotes), on the money in the open legs (the long's cost less a live short's credit), as a **simple** annual rate. Closed diagonal cards show the realized result the same way on the long's cost.
  - Re-sell panel: a "/yr" column — rent at the bid ÷ what the long is worth now, annualized (needs Refresh marks to fill the long's mark).
  - Outcomes: each closed-trade ticker links to its card.
  - `helm open --strike/--expiry` now always fetches the named strike, even outside the 3×ATR chain window, and skips the delta band and 25% spread cut-off for that one contract (the 10% spread rule and the $5,000 cap still run at confirm). **Engine change: needs `helm restart pg` before the board's Open page uses it** (the terminal already does) — check it was restarted after 10-14 ~12:40.
- s130 finding (not acted on): the long-call screen has passed nothing since 10-01. Calm names have rich options and cheap options are on wild names; names both calm and cheap fell 14 → 6 → 3 (09-23, 10-01, 10-14); median IV/HV90 0.94 → 1.08; top score 0.50 every day (calm, zero cheapness). S&P above its 200-day.
- **Scheduled task, Tue 10-27 9:00 ET** (one run, read-only, on the Mac): pulls the numbers for the rank-bar review and the CSP delta band and sends Russ a brief.
- s128 outcomes study (481 closed trades; scripts in `_s128/`): only CSPs made money in both books; CSPs at 0.25–0.30 delta led in both halves (split at 08-11), under 0.25 lost in both (small samples); long calls the biggest drain. Nothing acted on.
- Paper expiries are settled up to date.

## Open items
**Decisions you owe:** W12 total deployment target · W64 the 83 sign-inverted journal rows · W93 do confirmation windows count checks or days · W114 refuse an off-slot snapshot · W157 data collection on a travelling laptop · W169 give-back card wording · real iron condors (s100 said none; NOW traded, closed 10-12) · AMAT's exit reason (booked PROFIT_TARGET) · a whole-trade target for diagonals.

**Next work:** **backdate GOOGL's CSP to 2026-10-12 15:00** — write the reusable, self-refusing version of `_s130/backdate_csco.py` (position id + fill time as inputs; prove on a copy; Russ runs it on the Mac); note in the position that ~8 checks before the booking are missing · W195 rank-bar review 10-27 — start from the scheduled task's brief (CSP delta band in the same pass) · W208 diagonal card practice, then the field guide · W180 step 7 paper diagonal rules act · W212 `helm open` can't record a fill's real time — a late booking needs a one-off script (CSCO, s130); a `--filled-at` option would replace that.

**Can misstate money:** W184 the scan can propose a new diagonal on a held long · W185/W206 missing `leg_checks` rows · W186 snapshots drop readings · the audit can't check multi-leg P&L · W159 no resting profit-target order · **W210 B diagonal (closed) booked at 1 contract against 2 filled** — its P&L is probably half the real figure.

**Housekeeping:** W211 delete the board's frozen sentinel fallback (`helm-pg/helm_engine.py:186`, a copy of part of the model behind a bare `except`) · `.bak-20261003-s126` files (3 in helm, 4 in helm-pg) can go after the first clean real spread or condor booking · `.bak-20261012-s128` (5 in `helm-pg/templates`) — the chart links have had their day on the board, so these can go · `.bak-20261014-s130`: `helm/thesis.py`, `helm/cli/open_cmd.py`, and in helm-pg `helm_engine.py`, `templates/thesis_diag.html`, `templates/positions.html`, `templates/outcomes.html` — can go after a clean day on the board and the first named-strike open from the board · `.bak-20261015-s131`: `helm-pg/helm_engine.py` — after a day on the board with the DTE fix · `data/helm.db.bak-s130-csco-20261014-124410` (pre-backdate) — keep the newest three database backups · `_to_delete/` in helm and helm-pg holds reverted s128 backups — Russ deletes.

Everything else open on 10-01 is parked. It stays in the frozen list until it's worked on; then it gets a line here.
