# HELM — ORIENTATION

**What this file is:** the structural map of HELM — what it is for, how a trade moves through it, the modules, the data model, the infrastructure and the conventions. It exists so a fresh session can get oriented fast without relying on memory.

**What this file is NOT:** status, or the source of truth. It *points* at ground truth. When this map and the code disagree, **the code wins** — correct this file.

**Where things live** (rewritten 2026-10-31, s142):

| Kind | Changes | Lives in |
|------|---------|----------|
| **Structure** — architecture, data model, conventions | rarely | **this file** |
| **State** — the book, open items, what the last session did, the next free W-number | every session | **`claude/HELM-handover.md`** in the HELM project (mirror `_records/HELM-handover.md`) — read it first |
| **Decisions and paid-for lessons** | when one is earned | **`claude/HELM-standing-rules.md`** (mirror `_records/`); retired and superseded lines in `HELM-standing-rules-history.md` |
| **History** — what changed and why | continuously | **git** |

`archive/root/ISSUES.md` (frozen 2026-09-20 at HELM-218) and the old worklists are history only: code cites their ids, nothing reads them for status.

---

## 1. What HELM is

Russ's options desk on the Fidelity **Rollover IRA**. Since the revision (2026-10-24) it does one job: **generate cash by selling premium** — **cash-secured puts**, **covered calls on HELM-held shares** (mostly after an assignment), and **PMCCs** (a LEAPS call bought, short calls sold against it). Condors, spreads, standalone long calls/puts and the short-dated diagonal route are out; their code remains for history and for the PMCC, which is built on the diagonal family.

- **One book.** Every position is a row in `positions` with `book='REAL'`. The PAPER book was frozen on 2026-10-24 (77 positions closed `RUN_OFF`); its rows are history, never evidence.
- **Russ places and sizes every trade by hand in Fidelity. HELM advises and tracks; it never acts.** It gives one plain recommendation per position, with its reason and number, and flags every rule overstepped.
- **HELM's money is a sleeve:** $300,000 + the net realized result of every real leg closed from 10/24 (+ dividends on HELM-held shares). Limits: in use ≤ sleeve, one name ≤ 20%, one sector ≤ 30%; whole-book theta 0.05–0.12% of the sleeve a day, beta-weighted delta ≤ half the sleeve. `helm/sleeve.py`, `helm/bookview.py`.

Vision, firewall and server access are in the project charter. **Never touch COTS** (`~/Projects/cots`, `cots.local:8765`, `com.cots.*`).

---

## 2. How a trade moves through HELM

| Step | Who | Where |
|------|-----|-------|
| **Choose** a put / covered call | Russ runs the tool | `helm puts` → `helm/puttool.py`; `helm calls` → `helm/calltool.py`. Universe: latest ownership grade A–C or an approved fund (`helm/ownership.py`, `approved_funds`). Every run is recorded (`put_runs`/`put_candidates`, `call_runs`/`call_candidates`). Sizing by the sleeve: `sleeve.size()`. |
| **Evaluate** a named contract | Russ | `helm open TICKER STRATEGY` (`helm/cli/open_cmd.py`, `diagonal.py` for the PMCC) — **evaluates only**; `--confirm` refuses (s142). |
| **Place** it | Russ, in Fidelity | — |
| **Book** it | Russ downloads Activity & Orders → `helm fills` (or Today's "Update book from Fidelity") | `helm/brokerfills.py` reads both download formats (Rollover IRA only; the account number is never stored — a SHA-256 in `helm_meta`). `helm/fillimport.py` plans and writes: closes, leg closes, expiries, **partial closes** (the closed part becomes its own position, tag `split_from:<id>`), entry-price corrections, assignments (`helm/assignment.py`: puts in, **calls away**), **dividends** on HELM-held shares (`helm_dividends`, split by the Positions download), and opens and rolls (`helm/fillopens.py`). Every row it acts on is recorded in `broker_fills`, so a rerun proposes nothing. Anything it can't book cleanly is a **report**, never a guess. |
| **Watch** it | scheduled | `helm snapshot --all` at 10:00 / 12:30 / 15:15 (`helm/cli/check_cmd.py`) writes `checks` and `leg_checks`; exit flags `helm/exit_flags.py`; the 16:15 audit `helm/cli/audit_cmd.py`. |
| **Advise** | Russ reads | `helm advise` / the board's Today page → `helm/advice.py` (one recommendation per position, rule flags, book-limit flags, reading coverage). |
| **Measure** | — | `helm/netpnl.py`: net of fees is the P&L of record from 10/24 (one row per trade — a fee Fidelity revised is taken from the newest download); older trades gross, labelled. |

---

## 3. The commands

`helm` → `bin/helm` (shim to the `helm` conda env's python) → **`helm.py`**, whose `COMMANDS` dict maps each name to `helm.cli.<module>.run()` (a few to `helm.<module>.run`). **`helm.py` is the authoritative list.**

- `helm help` shows the **17 commands** of today's strategies and plumbing (`MAIN` in `helm.py`); `helm legacy` lists the other 23 (`LEGACY`), which **still run** — the board calls `close`, `resell`, `scan`, `paper`, `pending` — and are due to go on `LEGACY_UNTIL` unless Russ keeps them.
- **Retired and switched off** (code kept; env switches for old harnesses on a DB copy): `import`, `confirm-fills`, `activity` (`RETIRED` in `helm.py`, `HELM_RETIRED_ON=1`); `helm open --confirm` (`OPEN_BOOKING_RETIRED`, `HELM_OPEN_BOOKING_ON=1`); paper entries (`PAPER_ENTRIES_RETIRED`, `HELM_PAPER_ENTRIES_ON=1`); the paper exit agent (`PAPER_EXITS_RETIRED`, `HELM_PAPER_EXITS_ON=1`).
- `helm reconcile` is read-only (compares HELM with a Positions download; writes nothing).

---

## 4. The board (`~/Projects/helm-pg`)

HELM's web interface (http://127.0.0.1:8770, launchd `com.helm.pg`), used as a macOS web app. **It holds no model**: every threshold and verdict is imported or read from the engine, and it **changes things only by running HELM's own CLI**, never by writing the database. Menu: Today · Details (Book) · Watchlist · Health; older pages are still served off-menu. **Never hoist a `helm` import to module scope there** — every page renders blank. Engine changes reach it after `helm restart pg`; templates don't need one. `restart pg` does not clear `.pos_cache.json`.

---

## 5. Data model

**`helm/schema.sql`** is authoritative (with a tail of `ALTER TABLE` migrations — read the whole file); some tables are also created by the module that owns them (`fillimport.ensure_schema`, `puttool`, `calltool`, `assignment.ensure_columns`). Models in `helm/models/`. 37 tables, 4 views:

- **Trades:** `positions` (one per trade; `book`, `strategy`, `parent_position_id` = a roll's predecessor, `tags` incl. `split_from:`), `legs` (option and stock legs; stock legs carry shares in `contracts`, multiplier 1), `stock_positions` (HELM-held shares, `cost_basis`, `effective_basis` = strike − put premium), `entry_snapshots` (conditions at entry, one per position), `lifecycle_events`, `accounts`.
- **Readings:** `checks` (one row per position per snapshot), `leg_checks` (per leg), `iv_history`, `earnings_history`, `market_context` (records only — nothing reads it).
- **The import's records:** `broker_fills` (every Fidelity row booked, matched or used, with commission, fees and the download's time), `helm_dividends`, `helm_events` (IMPORT_RUN and other system events), `helm_meta` (the IRA's fingerprint).
- **Proposal tools:** `put_runs`, `put_candidates`, `call_runs`, `call_candidates`.
- **Reference:** `watchlist` (universe + sector), `ownership_quality` (grades), `approved_funds` (24 funds, each with the sector bucket the limit counts), `strategy_settings` (tunable levers, one row per strategy).
- **History and dormant** (kept on purpose; mostly paper-era or the old scan): `signals`, `exit_flags`, `exit_considered`, `exit_alerts`, `check_exit_flags`, `check_kept_hist`, `entry_thesis`, `paper_refusals`, `stop_arm_events`, `agent_runs`, `themes`, `theme_tickers`, `processed_transactions`, `import_pathways`.
- **Views:** `v_trade_summary`, `v_trade_lifecycle`, `v_exit_decisions`, `v_learning_positions`.

**Two traps:** (1) several models map `SELECT *` into a constructor (`checks`, `entry_snapshots`, `iv_history`, `legs`, `lifecycle_events`, `pathways`, `settings`, `signals`, `themes`) — a new column there breaks every path that builds the model; (2) **never `INSERT OR REPLACE` into a table others CASCADE from** (`positions`, `legs`, `checks`, `themes`) — REPLACE deletes first and the cascade takes the children (it wiped 161 real legs' readings before s135).

---

## 6. Decision core

**`helm/decision.py`** — the verdict engine: `evaluate(pos, legs, marks)` routes each position to its strategy family and reads levers from `strategy_settings`. **`helm/strategies.py`** — the strategy set and families. **`helm/verdict.py`** — adapters and `band_for`. **`helm/exit_flags.py`** — the flags the snapshot raises; **the diagonal family's rules (`exit_flags._legs`/`_diag_rows`/`_live_at`) are the PMCC's** — the short-dated DIAGONAL route is retired, the family is not. Keep decision logic in the core; a rule written twice drifts.

---

## 7. Infrastructure

- **Paths** — `helm/config.py` (`DB_PATH` → `data/helm.db`; `HELM_DB` and `HELM_ROOT` override). SQLite in WAL mode; `helm/db.py`.
- **Backups** — a nightly `.backup` copy outside the repo (launchd `com.russ.helm-nightly-backup`, 2:15, `~/Backups/helm/`, 14 days, Backblaze off the Mac). Writers take their own `VACUUM INTO` copy first (`data/helm.db.bak-*`; keep the newest three).
- **Scheduled jobs** — the repo keeps `launchd/`: `com.helm.snapshot.daily` (10:00/12:30/15:15), `com.helm.audit.eod` (16:15), `com.helm.mktsampler`, `com.helm.gwwatch`; `launchd/parked/` holds the retired paper exit job. The board (`com.helm.pg`), the engine server (`com.helm.server`) and the 9:35 vol refresh are installed on the Mac; `~/Library/LaunchAgents` is the authoritative set. Jobs collect readings only — nothing scheduled acts on a trade.
- **IB Gateway** — port 4002. **The snapshot runs on its own client numbers 31–33** (`ibkr.use_snapshot_ids()`); everything else uses 11–15, 17, 21, 99. The Gateway refuses a second connection on a number in use; a process that called `get_ib()` keeps 11 open until `disconnect_ib()`. New numbers are agreed with the Book first.
- **Market data** — IBKR live entitlement (`reqMarketDataType(1)`); IBKR gives ≤ 120 days of history; long price history comes from `_s122/px/`, refreshed on the Mac. Yahoo is blocked from the cloud container and the device VM.
- **Bridge** — `http://helm.local:8766/exec`; rules in the standing rules. **Secrets** — `~/.helm/env` (never print it), read by `helm/secrets_loader.py`.

---

## 8. Conventions

- **Explain any change first — what, which files, why — and wait for Russ's go-ahead.** A question is not an instruction.
- **Before editing a file keep a `.bak-YYYYMMDD-sNNN` copy**; it goes once the change is committed and its first unattended run is clean.
- **Verify on a `VACUUM INTO` copy** with `HELM_DB=` pointed at it, through the real CLI started the way production starts it; compare every table; perturb the change to prove the test can fail (`python3 -B`). Session test scripts live in `_sNNN/`.
- **The device shell never writes the live database**; write scripts are tested on a copy and Russ runs them on the Mac.
- **The Mac's working tree is production** — a snapshot runs whatever is on disk at its next run.
- **Russ commits and pushes** — one command naming every file, ending with the session's attribution lines. Commands shown to Russ start `cd ~/Projects/helm && helm …`.

---

## 9. Keeping this file honest

- Update it when **structure** changes (a new table, a new step, a moved module), committed beside the code.
- No status, issue numbers or thresholds that live elsewhere — point at them.
- Prefer a pointer over a paraphrase. A term you can't place is suspect, not canonical: check it against the code.
