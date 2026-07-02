# HELM — ORIENTATION

**What this file is:** the structural map of HELM — architecture, data model, pipeline, conventions. It exists so a fresh session (human or Claude) can get oriented fast without relying on memory that doesn't survive between sessions.

**What this file is NOT:** it is not live status, and it is not the source of truth. It *points* at ground truth; it does not restate it. When this map and the code disagree, **the code wins** — and this file should be corrected.

**Where things live (the three-rate rule).** HELM's knowledge changes at three different speeds; keeping them in separate places is what prevents the drift that stale, blended handover notes used to cause:

| Kind | Changes | Lives in |
|------|---------|----------|
| **Structure** — architecture, data model, conventions | rarely | **this file** (`ORIENTATION.md`) |
| **State** — active work, what's committed/unpushed, open forks | every session | **`ISSUES.md`** (Status block) |
| **History** — what changed and why | continuously | **git** + `ISSUES.md` Resolved log |

Do not put status in this file. Do not put architecture in `ISSUES.md`. If you catch yourself re-narrating something that already lives in code or the register, stop and point at it instead.

---

## 1. What HELM is

A personal, Claude-native options-trading platform for a Fidelity Rollover IRA pursuing the variance risk premium (VRP). Two books share the *same* machinery:

- **REAL** — the live account.
- **PAPER** — a parallel, auto-traded counterfactual book that trades candidates the REAL book passed on, building a labelled corpus for a future learning layer.

Both are rows in the same `positions` table, distinguished by a `book` field, and both are driven by the same decision core.

Vision, firewall, and server-access details live in the **project charter** (the HELM project description), not here. One rule is worth repeating even so: this is **HELM only** — never touch the separate, live COTS system. (Its firewall and paths are in the charter; the old in-repo `cots2` reference copy has been removed.)

---

## 2. How to operate it (the CLI)

`helm <command> [args]` resolves to `bin/helm` (a 2-line shim to the `helm` conda-env python) → **`helm.py`**, a small dispatcher whose `COMMANDS` dict maps each command name to `helm.cli.<module>.run()`.

**`helm.py` is the authoritative command list** — read it to see every command and which module serves it. Each command module lives in `helm/cli/` and exposes a `run()` entry point.

For Claude specifically: work happens over the **bridge** — POST to `http://helm.local:8766/exec {cmd, timeout}` via the Chrome extension, reading results back through `window` variables. Claude reads code/DB and drafts guarded patch scripts; the human runs all terminal and git.

---

## 3. Lifecycle pipeline → where it lives

The trade lifecycle and the modules that implement each stage. (Command names are in `helm.py`; module roles are one-liners — read the file for detail.)

- **scan / screen** — find candidates → `helm/cli/scan_cmd.py`, `screen.py`, `helm/russ_scan.py`
- **decide** — the verdict → `helm/decision.py` (core engine) + `helm/verdict.py` (adapters) + `helm/health.py` (composite health score)
- **open / execute** — enter a position → `helm/cli/open_cmd.py`
- **monitor / check** — periodic health check that journals to the `checks` table → `helm/cli/check_cmd.py` *(the REAL check runs ~3×/day via launchd)*
- **close** — exit → `helm/cli/close_cmd.py`
- **reconcile / sync** — align with broker state → `helm/cli/reconcile_cmd.py`, `import_cmd.py`, `ibkr_cmd.py`
- **paper book** — auto-manage the PAPER corpus → `helm/cli/paper_manage.py`, `_paper_open.py`, `_paper_generate.py`, `_decision_capture.py`

---

## 4. Data model

Defined in **`helm/schema.sql`** (authoritative; includes a tail of `ALTER TABLE` migrations — read the whole file, not just the `CREATE TABLE` blocks). Row/ORM helpers live in **`helm/models/`**. 19 tables, 3 views:

**Entities**
- `accounts` — the trading account(s)
- `positions` — every position, REAL and PAPER (`book` field)
- `legs` — individual option/stock legs of a position
- `stock_positions` — stock holdings

**Journals (time series)**
- `checks` — one row per REAL health check (per position); the monitor-stage journal
- `leg_checks` — per-leg marks captured during a check
- `lifecycle_events`, `helm_events` — lifecycle / system event logs

**Entry / exit / outcome capture**
- `entry_snapshots` — conditions at entry
- `signals` — scan-signal → outcome scaffolding (links back to a position when opened)
- `processed_transactions`, `import_pathways` — broker import bookkeeping

**Config / reference**
- `strategy_settings` — the tunable levers; **single source of truth** for thresholds (guarded by `helm/models/settings.py`'s load-time fat-finger check)
- `watchlist` — the scan universe (`active` flag = the live set)
- `themes`, `theme_tickers` — thematic grouping
- `iv_history`, `market_context` — market data history
- `helm_meta` — schema version / metadata

**Views:** `v_trade_summary`, `v_trade_lifecycle`, `v_exit_decisions`.

---

## 5. Decision core

**`helm/decision.py`** — the universe-agnostic, book-agnostic verdict engine. Entry point `evaluate(pos, legs, marks) -> (reason, total_pnl)`; it reads levers from `strategy_settings` and routes each position to its strategy family. Siblings in the same file capture counterfactuals without acting: `evaluate_arms` (stop A/B experiment) and `evaluate_shadow_debit_stop` (long-debit shadow capture) — both pure, neither mutates the verdict.

**`helm/verdict.py`** — the `_ns_pos` / `_ns_leg` adapters that wrap DB dicts as attribute objects for the core, plus `band_for`.

The core is the single home for decision logic — earlier there were multiple divergent copies (in the check command, health, paper manager); they were unified into `decision.py`. Keep it that way: don't re-implement verdict logic elsewhere, call the core.

---

## 6. Infrastructure

- **Paths & config** — `helm/config.py` (`DB_PATH` → `data/helm.db`, `SCHEMA_PATH`, etc.). Always resolve the DB via `helm.config.DB_PATH`, never a hardcoded path.
- **DB layer** — `helm/db.py` (`get_conn`, `transaction`, `init_db`, migration helpers). SQLite in WAL mode.
- **Secrets** — `helm/secrets_loader.py` reads `~/.helm/env` (chmod 600, outside the repo). Credentials are never hardcoded in plists/dotfiles/repo.
- **Scheduling** — four `com.helm.*` launchd agents (check, paper-manage, server, notify). See `~/Library/LaunchAgents` for the authoritative set.
- **Bridge** — a local server at `helm.local:8766` exposing `/exec`; how Claude reads state and runs read-only checks.

---

## 7. Conventions & guardrails

Authoritative detail lives in `ISSUES.md` (top-of-file conventions) — this is a pointer summary.

- **Firewall** — HELM only; never COTS. Bright line.
- **Register discipline** — `ISSUES.md` is the single source of truth for issues/status (Status block · Active · Parking lot · Resolved log). Reconcile from the live register + git, never from recollection or a past session's prose. A source change commits *with* its `ISSUES.md` update, named files only (never `git add -A`).
- **Division of labor** — Claude reads code/DB and drafts guarded patch scripts; the human runs all terminal/git.
- **Patch discipline** — every patch: idempotency sentinel · anchor-count assert · `/tmp` stage + validate (`py_compile`, or an in-memory build for schema) · timestamped backup · dry-run default → `--apply` · post-write readback. For live-DB migrations: read-only probe → WAL-safe `.backup()` copy → `integrity_check` → trial on the copy → then apply.

---

## 8. Keeping this file honest

- Update it when **structure** changes (new table, new pipeline stage, moved module) — via the normal guarded-patch flow, committed beside the code.
- Never add status, issue numbers, or thresholds — those drift; they belong in `ISSUES.md` or `strategy_settings`.
- Prefer a pointer over a paraphrase. If a section starts re-describing what a file does in detail, shorten it to "read `<file>`."
- If a past note or this file uses a term you can't place, treat it as suspect, not canonical — check it against the code before acting on it.
