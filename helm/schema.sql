-- HELM Data Model — schema.sql
-- Version: 1.3  |  Updated: 2026-05-23
--
-- Design principles:
--   Strategy is first-class: every position knows what it is
--   Legs are native: multi-leg from day one, not bolted on
--   Lifecycle is append-only: events record history, never mutate it
--   Checks are structured health assessments, not just data snapshots
--   Thresholds are user-configurable per strategy (strategy_settings)
--   Entry snapshot captures full market context at open for outcome analysis
--   Signals are per-ticker, never deleted — permanent recommendation history
--   PnL is always computed, never stored stale
--
-- v1.1: watchlist expanded with is_optionable + last_screened_at
-- v1.2: idx_entry_leg, idx_checks_dedup (soft duplicate guard)
-- v1.3: signals table added; positions gets signal_id
-- v1.4: import_pathways table; risk_pct_per_trade on strategy_settings
-- v1.5: watchlist fundamentals (market_cap, volume, 52wk, beta, dividend, earnings)
-- v1.5: watchlist fundamentals (market_cap, volume, 52wk, beta, dividend, earnings)

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============================================================
-- ACCOUNTS
-- ============================================================
CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,
    broker          TEXT NOT NULL,
    nickname        TEXT NOT NULL,
    buying_power    REAL,
    portfolio_value REAL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    notes           TEXT,
    -- W56: balances had no writer reachable after setup, so portfolio_value
    -- sat two months stale while every position was sized from it. These two
    -- are deliberately different questions: as_of is the date the MONEY is
    -- true (the CSV's own "Date downloaded"), updated_at is when HELM wrote
    -- it. Staleness you can see is a different problem from staleness you
    -- cannot.
    balances_as_of      TEXT,
    balances_updated_at TEXT
);

-- ============================================================
-- SIGNALS
-- Per-ticker strategy recommendations. Never deleted.
-- The intelligence layer between watchlist and position opening.
-- Defined before positions so positions can FK reference it.
--
-- Workflow:
--   1. helm scan  generates a Signal for each optionable ticker
--   2. HELM computes auto_bias from technicals (-3 to +3)
--   3. User accepts or overrides bias (confirmed_bias)
--   4. HELM produces ranked strategy recommendations
--   5. User opens a position and position.signal_id = this signal
--   6. Position closes and outcome is recorded here
--
-- Signals are NEVER deleted. Enforced at application layer.
-- ============================================================
CREATE TABLE IF NOT EXISTS signals (
    id                  TEXT PRIMARY KEY,
    ticker              TEXT NOT NULL,
    generated_at        TEXT NOT NULL,

    -- Volatility environment
    iv_current          REAL,
    iv_rank             REAL,
    iv_percentile       REAL,
    iv_regime           TEXT,
    -- HIGH: iv_rank >= 50 (sell premium)
    -- MODERATE: 30-49 (discretionary)
    -- LOW: < 30 (buy premium)

    -- Technical indicators
    spot_price          REAL,
    ema_20              REAL,
    sma_50              REAL,
    sma_200             REAL,
    rsi_14              REAL,
    macd_line           REAL,
    macd_signal         REAL,
    macd_histogram      REAL,
    atr_14              REAL,
    bb_width            REAL,
    bb_upper            REAL,
    bb_lower            REAL,
    bb_squeeze          INTEGER,          -- 1 = squeeze (low vol, potential breakout)

    -- Derived conditions
    price_vs_ema20      TEXT,             -- ABOVE | BELOW | AT
    price_vs_sma50      TEXT,             -- ABOVE | BELOW | AT
    price_vs_sma200     TEXT,             -- ABOVE | BELOW | AT
    price_vs_52wk_pct   REAL,             -- 0=52wk low, 100=52wk high
    rsi_condition       TEXT,             -- OVERBOUGHT | OVERSOLD | NEUTRAL
    macd_condition      TEXT,             -- BULLISH | BEARISH | NEUTRAL
    trend_strength      TEXT,             -- STRONG | MODERATE | WEAK | NONE

    -- Auto bias (computed from technicals)
    auto_bias_score     REAL,             -- -3.0 to +3.0
    auto_bias           TEXT,
    auto_bias_reasoning TEXT,             -- plain-English explanation

    -- User override (Option C: always allow override)
    user_bias_override  TEXT,             -- null = accepted auto_bias
    confirmed_bias      TEXT NOT NULL,    -- final bias used for recommendations

    -- Strategy recommendations (JSON array, ordered by fit score)
    -- Each item: {strategy, fit, fit_score, reasoning,
    --             suggested_strike, suggested_dte,
    --             atr_strikes_otm, position_size_contracts}
    recommendations     TEXT NOT NULL,

    -- Top recommendation (denormalized for fast queries)
    top_strategy        TEXT,
    top_fit             TEXT,             -- STRONG | GOOD | MODERATE | WEAK

    -- ATR-based sizing guidance
    atr_1x_price        REAL,             -- spot +/- 1 ATR
    atr_2x_price        REAL,             -- spot +/- 2 ATR
    suggested_contracts INTEGER,

    -- Earnings context
    earnings_date       TEXT,
    days_to_earnings    INTEGER,
    earnings_warning    INTEGER DEFAULT 0, -- 1 = earnings within 30 days

    -- Watchlist context at time of signal
    willing_to_own      INTEGER,
    is_optionable       INTEGER,

    -- Outcome (updated when linked position closes)
    position_opened     INTEGER DEFAULT 0,
    position_id         TEXT,             -- FK set after position created
    outcome_pnl         REAL,
    outcome_result      TEXT,             -- WIN | LOSS | BREAKEVEN | EXPIRED | ASSIGNED
    outcome_notes       TEXT,

    -- Data provenance
    data_source         TEXT DEFAULT 'yfinance',
    data_quality        TEXT DEFAULT 'GOOD',

    created_at          TEXT NOT NULL DEFAULT (datetime('now')),

    CHECK (iv_regime IN ('HIGH','MODERATE','LOW') OR iv_regime IS NULL),
    CHECK (auto_bias IN ('BEARISH','MILDLY_BEARISH','NEUTRAL','MILDLY_BULLISH','BULLISH') OR auto_bias IS NULL),
    CHECK (user_bias_override IN ('BEARISH','MILDLY_BEARISH','NEUTRAL','MILDLY_BULLISH','BULLISH') OR user_bias_override IS NULL),
    CHECK (confirmed_bias IN ('BEARISH','MILDLY_BEARISH','NEUTRAL','MILDLY_BULLISH','BULLISH')),
    CHECK (top_fit IN ('STRONG','GOOD','MODERATE','WEAK') OR top_fit IS NULL),
    CHECK (outcome_result IN ('WIN','LOSS','BREAKEVEN','EXPIRED','ASSIGNED') OR outcome_result IS NULL),
    CHECK (data_quality IN ('GOOD','PARTIAL','STALE'))
);

-- ============================================================
-- POSITIONS
-- Strategy is first-class. Every position knows what it is.
-- signal_id links back to the signal that originated this trade.
-- ============================================================
CREATE TABLE IF NOT EXISTS positions (
    id                    TEXT PRIMARY KEY,
    account_id            TEXT NOT NULL REFERENCES accounts(id),
    signal_id             TEXT REFERENCES signals(id),  -- the signal that led to this trade

    strategy              TEXT NOT NULL,
    -- CSP | COVERED_CALL | LONG_CALL | PERM
    -- BULL_PUT_SPREAD | BEAR_CALL_SPREAD | IRON_CONDOR
    -- DIAGONAL | PMCC | SHORT_STRANGLE | JADE_LIZARD

    ticker                TEXT NOT NULL,
    company_name          TEXT,

    status                TEXT NOT NULL DEFAULT 'OPEN',
    -- OPEN | CLOSED | EXPIRED | ASSIGNED | ROLLED_OUT

    opened_at             TEXT NOT NULL,
    closed_at             TEXT,
    exit_reason           TEXT,
    -- TARGET | STOP | EXPIRED | ASSIGNED | ROLLED | MANUAL (why the position closed)
    earnings_date         TEXT,

    total_contracts       INTEGER NOT NULL DEFAULT 1,
    net_premium           REAL,
    realized_pnl          REAL,
    max_profit            REAL,
    max_loss              REAL,           -- null = undefined risk
    breakeven_low         REAL,
    breakeven_high        REAL,

    spread_width          REAL,
    credit_to_width_ratio REAL,
    credit_exceeds_width  INTEGER,        -- Jade Lizard structural integrity
    willing_to_own        INTEGER,

    parent_position_id    TEXT REFERENCES positions(id),

    notes                 TEXT,
    tags                  TEXT,

    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    book                  TEXT NOT NULL DEFAULT 'REAL',  -- REAL | PAPER | SHADOW,
    fills_confirmed_at    TEXT,          -- W151: set only when EVERY leg matched a broker fill

    CHECK (strategy IN (
        'LONG_STRADDLE','CSP','COVERED_CALL','LONG_CALL','LONG_PUT','PERM',
        'BULL_PUT_SPREAD','BEAR_CALL_SPREAD','IRON_CONDOR','BEAR_PUT_SPREAD','BULL_CALL_SPREAD','LONG_CONDOR',
        'DIAGONAL','PMCC','DIAGONAL_PUT','SHORT_STRANGLE','JADE_LIZARD'
    )),
    CHECK (status IN ('PENDING','OPEN','CLOSED','EXPIRED','ASSIGNED','ROLLED_OUT'))
);

-- ============================================================
-- LEGS
-- ============================================================
CREATE TABLE IF NOT EXISTS legs (
    id              TEXT PRIMARY KEY,
    position_id     TEXT NOT NULL REFERENCES positions(id) ON DELETE CASCADE,

    leg_role        TEXT NOT NULL,
    option_type     TEXT,
    direction       TEXT NOT NULL,
    strike          REAL,
    expiration      TEXT,
    contracts       INTEGER NOT NULL DEFAULT 1,
    multiplier      INTEGER NOT NULL DEFAULT 100,

    open_price      REAL NOT NULL,
    close_price     REAL,
    open_date       TEXT NOT NULL,
    close_date      TEXT,

    status          TEXT NOT NULL DEFAULT 'OPEN',
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    entry_delta     REAL,
    commission      REAL,          -- W151: captured from the broker, never computed with,
    fees            REAL,          -- stored P&L stays GROSS by decision (2026-08-26)

    CHECK (leg_role IN (
        'SHORT_PUT','LONG_PUT','SHORT_CALL','LONG_CALL',
        'LONG_STOCK','SHORT_STOCK','LONG_LEAPS'
    )),
    CHECK (direction IN ('LONG','SHORT')),
    CHECK (option_type IN ('PUT','CALL','STOCK') OR option_type IS NULL),
    CHECK (status IN ('OPEN','CLOSED','EXPIRED','ASSIGNED','EXERCISED'))
);

-- ============================================================
-- ENTRY SNAPSHOTS
-- ============================================================
CREATE TABLE IF NOT EXISTS entry_snapshots (
    id              TEXT PRIMARY KEY,
    position_id     TEXT NOT NULL UNIQUE REFERENCES positions(id) ON DELETE CASCADE,
    leg_id          TEXT REFERENCES legs(id),
    snapshot_at     TEXT NOT NULL,

    spot_price      REAL NOT NULL,
    spot_52wk_high  REAL,
    spot_52wk_low   REAL,

    iv_current      REAL,
    iv_rank         REAL,
    iv_percentile   REAL,
    iv_52wk_high    REAL,
    iv_52wk_low     REAL,
    hv_30d          REAL,

    delta           REAL,
    gamma           REAL,
    theta           REAL,
    vega            REAL,

    atr_14          REAL,                 -- ATR at entry (strike sizing reference)
    atr_strikes_otm REAL,                 -- how many ATR units OTM the short strike is

    dte             INTEGER,
    days_to_earnings INTEGER,

    premium_collected REAL,
    theta_per_day   REAL,

    skew_put_iv     REAL,
    skew_call_iv    REAL,
    skew_value      REAL,

    leaps_delta     REAL,
    leaps_dte       INTEGER,
    extrinsic_ratio REAL,

    settings_snapshot TEXT,

    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- CHECKS
-- ============================================================
CREATE TABLE IF NOT EXISTS checks (
    id              TEXT PRIMARY KEY,
    position_id     TEXT NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    checked_at      TEXT NOT NULL,

    spot_price      REAL,
    dte_now         INTEGER,
    days_open       INTEGER,
    days_to_earnings INTEGER,

    current_bid     REAL,
    current_ask     REAL,
    current_price   REAL,

    delta           REAL,
    gamma           REAL,
    theta           REAL,
    vega            REAL,
    iv_current      REAL,

    delta_vs_entry  REAL,
    iv_vs_entry     REAL,
    spot_pct_change REAL,

    iv_rank         REAL,
    iv_percentile   REAL,

    skew_put_iv     REAL,
    skew_call_iv    REAL,
    skew_value      REAL,

    pnl_unrealized  REAL,
    pnl_pct         REAL,
    pnl_vs_theta    REAL,

    leaps_delta_now    REAL,
    spread_compression REAL,

    health_flag     TEXT NOT NULL,
    action_signal   TEXT NOT NULL,
    narrative       TEXT,

    greeks_source   TEXT,
    data_quality    TEXT DEFAULT 'GOOD',

    created_at      TEXT NOT NULL DEFAULT (datetime('now')),

    CHECK (health_flag IN ('GREEN','YELLOW','RED')),
    CHECK (action_signal IN ('HOLD','WATCH','ADJUST','CLOSE','ROLL')),
    CHECK (data_quality IN ('GOOD','PARTIAL','STALE'))
);

-- ============================================================
-- LIFECYCLE EVENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS lifecycle_events (
    id              TEXT PRIMARY KEY,
    position_id     TEXT NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    leg_id          TEXT REFERENCES legs(id),

    event_type      TEXT NOT NULL,

    occurred_at     TEXT NOT NULL,
    spot_price      REAL,
    option_price    REAL,
    contracts       INTEGER,
    pnl_at_event    REAL,
    cumulative_pnl  REAL,

    new_position_id TEXT REFERENCES positions(id),
    narrative       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),

    CHECK (event_type IN (
        'PENDING','OPENED','ROLLED','CLOSED','ASSIGNED',
        'EXPIRED','ADJUSTED','NOTE','CHECK_ALERT'
    ))
);

-- ============================================================
-- STRATEGY SETTINGS
-- ============================================================
CREATE TABLE IF NOT EXISTS strategy_settings (
    id              TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    strategy        TEXT NOT NULL,

    entry_iv_rank_min       REAL,
    entry_iv_rank_max       REAL,
    entry_delta_min         REAL,
    entry_delta_max         REAL,
    entry_dte_min           INTEGER,
    entry_dte_max           INTEGER,

    leaps_delta_min         REAL,
    leaps_dte_min           INTEGER,
    extrinsic_ratio_min     REAL,

    profit_target_pct       REAL,
    stop_loss_multiplier    REAL,
    risk_pct_per_trade      REAL,             -- max % of buying power per trade (default: 0.05)
    dte_exit_threshold      INTEGER,

    delta_drift_warning     REAL,
    delta_danger            REAL,
    iv_increase_warning     REAL,
    dte_review_threshold    INTEGER,
    net_delta_warning       REAL,

    enforce_credit_exceeds_width INTEGER DEFAULT 1,
    days_before_earnings_exit    INTEGER,
    perm_profit_target_pct       REAL,

    is_default      INTEGER NOT NULL DEFAULT 0,
    last_modified   TEXT NOT NULL DEFAULT (datetime('now')),
    notes           TEXT,

    UNIQUE(account_id, strategy),
    CHECK (strategy IN (
        'LONG_STRADDLE','CSP','COVERED_CALL','LONG_CALL','LONG_PUT','PERM',
        'BULL_PUT_SPREAD','BEAR_CALL_SPREAD','IRON_CONDOR',
        'DIAGONAL','PMCC','SHORT_STRANGLE','JADE_LIZARD',
        'BEAR_PUT_SPREAD','BULL_CALL_SPREAD','LONG_CONDOR','DIAGONAL_PUT'
    ))
);

-- ============================================================
-- ============================================================
-- ============================================================
-- WATCHLIST
-- Your trading universe in one table.
-- Tickers flow through here at three levels:
--   1. Added to watchlist (you are aware of it)
--   2. is_optionable=1 (passed the screen: liquid, IV, size)
--   3. willing_to_own=1 (qualitative: happy holding the stock)
-- HELM will flag if a position is opened on a ticker not on
-- the watchlist or not yet screened as optionable.
-- Fundamentals refreshed by helm screen on each run.
-- ============================================================
CREATE TABLE IF NOT EXISTS watchlist (
    id              TEXT PRIMARY KEY,
    ticker          TEXT NOT NULL UNIQUE,
    company_name    TEXT,
    sector          TEXT,

    -- Optionability (set by helm screen)
    is_optionable   INTEGER NOT NULL DEFAULT 0,
    last_screened_at TEXT,

    -- Qualitative judgment
    willing_to_own  INTEGER NOT NULL DEFAULT 1,
    thesis          TEXT,

    -- Fundamentals (refreshed by helm screen)
    market_cap      REAL,             -- in billions
    avg_daily_volume REAL,            -- avg daily stock volume (shares)
    week_52_high    REAL,
    week_52_low     REAL,
    beta            REAL,
    dividend_yield  REAL,             -- annual yield as decimal (e.g. 0.02 = 2%)
    next_earnings   TEXT,             -- ISO date of next earnings
    last_fundamentals_at TEXT,        -- when fundamentals were last fetched

    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    notes           TEXT,

    -- Build isolation / lifecycle (ALTER-appended in live DB)
    active          INTEGER DEFAULT 0,   -- legacy active-universe flag
    build           TEXT,  -- build tag, e.g. 'sector_v1'

    -- W136 (s108): exposure grouping that GICS sector cannot express.
    -- 19 of the 84 names are one trade; sector calls them two
    -- different things, and calls two different things Industrials.
    exposure_group  TEXT
);

-- INDEXES
-- ============================================================

-- Positions
CREATE INDEX IF NOT EXISTS idx_pos_account    ON positions(account_id);
CREATE INDEX IF NOT EXISTS idx_pos_ticker     ON positions(ticker);
CREATE INDEX IF NOT EXISTS idx_pos_strategy   ON positions(strategy);
CREATE INDEX IF NOT EXISTS idx_pos_status     ON positions(status);
CREATE INDEX IF NOT EXISTS idx_pos_opened     ON positions(opened_at);
CREATE INDEX IF NOT EXISTS idx_pos_signal     ON positions(signal_id);

-- Legs
CREATE INDEX IF NOT EXISTS idx_legs_pos       ON legs(position_id);

-- Checks
CREATE INDEX IF NOT EXISTS idx_checks_pos     ON checks(position_id);
CREATE INDEX IF NOT EXISTS idx_checks_at      ON checks(checked_at);
CREATE INDEX IF NOT EXISTS idx_checks_flag    ON checks(health_flag);
CREATE UNIQUE INDEX IF NOT EXISTS idx_checks_dedup ON checks(position_id, checked_at);

-- Lifecycle events
CREATE INDEX IF NOT EXISTS idx_events_pos     ON lifecycle_events(position_id);
CREATE INDEX IF NOT EXISTS idx_events_type    ON lifecycle_events(event_type);

-- Entry snapshots
CREATE INDEX IF NOT EXISTS idx_entry_pos      ON entry_snapshots(position_id);
CREATE INDEX IF NOT EXISTS idx_entry_leg      ON entry_snapshots(leg_id);
CREATE INDEX IF NOT EXISTS idx_entry_iv       ON entry_snapshots(iv_rank);
CREATE INDEX IF NOT EXISTS idx_entry_delta    ON entry_snapshots(delta);
CREATE INDEX IF NOT EXISTS idx_entry_strategy ON entry_snapshots(position_id, snapshot_at);

-- Signals — optimized for key analysis queries:
-- all signals for a ticker over time
-- which signals led to wins vs losses
-- IV regime vs outcome correlation
-- bias accuracy (auto vs confirmed vs outcome)
CREATE INDEX IF NOT EXISTS idx_signals_ticker    ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_generated ON signals(generated_at);
CREATE INDEX IF NOT EXISTS idx_signals_iv_regime ON signals(iv_regime);
CREATE INDEX IF NOT EXISTS idx_signals_bias      ON signals(confirmed_bias);
CREATE INDEX IF NOT EXISTS idx_signals_strategy  ON signals(top_strategy);
CREATE INDEX IF NOT EXISTS idx_signals_outcome   ON signals(outcome_result);
CREATE INDEX IF NOT EXISTS idx_signals_position  ON signals(position_id);
CREATE INDEX IF NOT EXISTS idx_signals_ticker_ts ON signals(ticker, generated_at);

-- Watchlist
CREATE INDEX IF NOT EXISTS idx_wl_optionable  ON watchlist(is_optionable);
CREATE INDEX IF NOT EXISTS idx_wl_wto         ON watchlist(willing_to_own);


-- ── Investment Themes ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS themes (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS theme_tickers (
    id           TEXT PRIMARY KEY,
    theme_id     TEXT NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    ticker       TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'ESTABLISHED',
    company_name TEXT,
    notes        TEXT,
    added_at     TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (category IN ('ESTABLISHED','EMERGING','PRE_IPO','WATCH')),
    UNIQUE(theme_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_theme_tickers_theme  ON theme_tickers(theme_id);
CREATE INDEX IF NOT EXISTS idx_theme_tickers_ticker ON theme_tickers(ticker);

-- ── HELM Events (nudge system) ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS helm_events (
    id          TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    entity_id   TEXT,
    entity_name TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
    notes       TEXT,
    CHECK (event_type IN (
        'THEME_CREATED','THEME_REFRESHED','THEME_IPO_UPDATED',
        'SCREEN_RUN','RECONCILE_RUN','FULL_CHECK_RUN',
        'IMPORT_RUN','WATCHLIST_BUILT'
    ))
);

CREATE INDEX IF NOT EXISTS idx_helm_events_type   ON helm_events(event_type);
CREATE INDEX IF NOT EXISTS idx_helm_events_entity ON helm_events(entity_id);


-- IV Rank and IV Percentile history (updated daily via helm ivr refresh)
CREATE TABLE IF NOT EXISTS iv_history (
    ticker          TEXT NOT NULL,
    date            TEXT NOT NULL,
    iv_current      REAL,
    iv_rank         REAL,
    iv_percentile   REAL,
    iv_52wk_high    REAL,
    iv_52wk_low     REAL,
    days_history    INTEGER,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_iv_history_ticker ON iv_history(ticker);
CREATE INDEX IF NOT EXISTS idx_iv_history_date   ON iv_history(date);


-- ============================================================
-- ANALYSIS LAYER: entry -> lifecycle -> exit (read-only views)
-- ============================================================
DROP VIEW IF EXISTS v_trade_summary;
CREATE VIEW v_trade_summary AS
SELECT
  p.id AS position_id, p.account_id, p.ticker, p.company_name, p.strategy, p.status,
  1 AS traded, p.opened_at, p.closed_at,
  round(julianday(coalesce(replace(substr(p.closed_at,1,19),'T',' '),'now')) - julianday(replace(substr(p.opened_at,1,19),'T',' ')),2) AS days_held,
  p.total_contracts, p.net_premium, p.max_profit, p.max_loss,
  p.breakeven_low, p.breakeven_high, p.spread_width, p.credit_to_width_ratio,
  p.realized_pnl, p.exit_reason,
  CASE p.strategy
    WHEN 'CSP' THEN (SELECT l.strike*100*l.contracts FROM legs l WHERE l.position_id=p.id AND l.leg_role='SHORT_PUT' LIMIT 1)
    WHEN 'COVERED_CALL' THEN (SELECT l.contracts*l.open_price FROM legs l WHERE l.position_id=p.id AND l.leg_role='LONG_STOCK' LIMIT 1)
    WHEN 'LONG_CALL' THEN ABS(p.net_premium)
    WHEN 'LONG_PUT' THEN ABS(p.net_premium)
    WHEN 'BEAR_PUT_SPREAD' THEN ABS(p.net_premium)
    WHEN 'PMCC' THEN ABS(p.net_premium)
    WHEN 'DIAGONAL' THEN ABS(p.net_premium)
    ELSE p.max_loss END AS capital_deployed,
  es.snapshot_at AS entry_at, es.spot_price AS entry_spot, es.iv_current AS entry_iv,
  es.iv_rank AS entry_iv_rank, es.iv_percentile AS entry_iv_pct, es.delta AS entry_delta,
  es.dte AS entry_dte, es.days_to_earnings AS entry_days_to_earnings, es.atr_14 AS entry_atr_14,
  es.hv_30d AS entry_hv_30d, es.theta_per_day AS entry_theta_per_day, es.extrinsic_ratio AS entry_extrinsic_ratio,
  (SELECT COUNT(*) FROM checks ck WHERE ck.position_id=p.id) AS n_checks,
  (SELECT MIN(ck.pnl_unrealized) FROM checks ck WHERE ck.position_id=p.id) AS mae_pnl,
  (SELECT MAX(ck.pnl_unrealized) FROM checks ck WHERE ck.position_id=p.id) AS mfe_pnl,
  (SELECT ck.health_flag FROM checks ck WHERE ck.position_id=p.id ORDER BY ck.checked_at DESC LIMIT 1) AS last_health_flag,
  p.earnings_date
FROM positions p
LEFT JOIN entry_snapshots es ON es.id=(SELECT e2.id FROM entry_snapshots e2 WHERE e2.position_id=p.id ORDER BY e2.snapshot_at ASC LIMIT 1);

DROP VIEW IF EXISTS v_trade_lifecycle;
CREATE VIEW v_trade_lifecycle AS
SELECT
  ck.position_id, p.ticker, p.strategy, p.status, 1 AS traded, p.opened_at,
  es.spot_price AS entry_spot, es.iv_current AS entry_iv, es.iv_rank AS entry_iv_rank,
  es.delta AS entry_delta, es.dte AS entry_dte,
  ck.checked_at, ck.days_open, ck.dte_now, ck.days_to_earnings,
  ck.spot_price, ck.current_bid, ck.current_ask, ck.current_price,
  ck.delta, ck.gamma, ck.theta, ck.vega, ck.iv_current, ck.iv_rank, ck.iv_percentile,
  ck.delta_vs_entry, ck.iv_vs_entry, ck.spot_pct_change,
  ck.skew_put_iv, ck.skew_call_iv, ck.skew_value,
  ck.pnl_unrealized, ck.pnl_pct, ck.buffer_dollars, ck.buffer_pct,
  ck.health_flag, ck.action_signal, ck.greeks_source, ck.data_quality, ck.rth_flag,
  p.closed_at, p.realized_pnl, p.exit_reason
FROM checks ck
JOIN positions p ON p.id=ck.position_id
LEFT JOIN entry_snapshots es ON es.id=(SELECT e2.id FROM entry_snapshots e2 WHERE e2.position_id=p.id ORDER BY e2.snapshot_at ASC LIMIT 1);

-- ============================================================
-- Decision-capture layer  (added 2026-06-09)
--   Entry decisions : signals (revived) + russ_* / spec_* / helm_policy_version
--   Exit decisions  : checks.policy_version + v_exit_decisions view
--   Counterfactuals : shadow_positions / shadow_marks
--   Market regime   : market_context
-- ============================================================

ALTER TABLE signals ADD COLUMN russ_intent TEXT CHECK(russ_intent IN ('OPEN','SKIP'));
ALTER TABLE signals ADD COLUMN russ_intent_at TEXT;
ALTER TABLE signals ADD COLUMN russ_action TEXT DEFAULT 'PENDING' CHECK(russ_action IN ('OPEN','SKIP','PENDING'));
ALTER TABLE signals ADD COLUMN russ_action_at TEXT;
ALTER TABLE signals ADD COLUMN spec_match TEXT CHECK(spec_match IN ('EXACT','MODIFIED','NA'));
ALTER TABLE signals ADD COLUMN spec_delta TEXT;
ALTER TABLE signals ADD COLUMN helm_policy_version TEXT;
ALTER TABLE checks ADD COLUMN policy_version TEXT;

-- HELM-041 - per-leg mark store. Leg-grain sibling of `checks`: one row per
-- leg per check, so multi-leg /health cards build marks = {leg.id: price}
-- off stored data (read-only display, HELM-037) and feed decision.evaluate
-- (WS5c). data_quality mirrors `checks` so the HELM-036 GOOD filter applies
-- unchanged (_leg_mark is_live -> 'GOOD'; frozen/partial -> 'STALE'/'PARTIAL').
CREATE TABLE IF NOT EXISTS leg_checks (
    id              TEXT PRIMARY KEY,
    check_id        TEXT REFERENCES checks(id) ON DELETE CASCADE,
    position_id     TEXT NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    leg_id          TEXT NOT NULL REFERENCES legs(id) ON DELETE CASCADE,
    checked_at      TEXT NOT NULL,

    current_bid     REAL,
    current_ask     REAL,
    current_price   REAL,

    delta           REAL,
    gamma           REAL,
    theta           REAL,
    vega            REAL,
    iv_current      REAL,

    greeks_source   TEXT,
    data_quality    TEXT DEFAULT 'GOOD',

    created_at      TEXT NOT NULL DEFAULT (datetime('now')),

    CHECK (data_quality IN ('GOOD','PARTIAL','STALE'))
);

CREATE INDEX IF NOT EXISTS idx_leg_checks_leg_time ON leg_checks(leg_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_leg_checks_pos_time ON leg_checks(position_id, checked_at);

CREATE TABLE market_context (
    id TEXT PRIMARY KEY,
    as_of_date TEXT NOT NULL,
    vix REAL,
    vix_regime TEXT,
    spx_price REAL,
    spx_vs_sma50 TEXT,
    spx_vs_sma200 TEXT,
    index_trend TEXT,
    term_structure TEXT,
    term_structure_value REAL,
    breadth REAL,
    notes TEXT,
    data_source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
 );


DROP VIEW IF EXISTS v_exit_decisions;
CREATE VIEW v_exit_decisions AS
 SELECT
    ch.id             AS check_id,
    ch.position_id    AS position_id,
    p.ticker          AS ticker,
    p.strategy        AS strategy,
    ch.checked_at     AS decided_at,
    ch.action_signal  AS helm_exit_call,
    ch.health_flag    AS helm_health_flag,
    ch.narrative      AS helm_reasons,
    ch.policy_version AS helm_policy_version,
    ch.dte_now        AS dte_now,
    ch.days_open      AS days_open,
    ch.pnl_unrealized AS pnl_at_check,
    ch.pnl_pct        AS pnl_pct_at_check,
    p.status          AS position_status,
    p.closed_at       AS position_closed_at,
    p.realized_pnl    AS position_realized_pnl,
    p.exit_reason     AS position_exit_reason,
    CASE WHEN p.closed_at IS NOT NULL AND p.closed_at >= ch.checked_at THEN 1 ELSE 0 END AS closed_after_check
 FROM checks ch
 JOIN positions p ON p.id = ch.position_id;


-- ---------------------------------------------------------------
-- Reconciled to live (s23) — additive: undeclared tables + columns
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS helm_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

CREATE TABLE IF NOT EXISTS import_pathways (
    id              TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    broker          TEXT NOT NULL,             -- 'fidelity' | 'ibkr' | 'tastytrade'
    broker_account  TEXT,                      -- broker-side account number (e.g. '218481565')
    watch_folder    TEXT NOT NULL,             -- path where exports land (e.g. '~/Downloads')
    file_pattern    TEXT NOT NULL,             -- glob pattern (e.g. 'Portfolio_Positions_*.csv')
    import_both_accounts INTEGER DEFAULT 1,    -- 1 = import all accounts found in file
    last_imported_at TEXT,                     -- ISO datetime of last successful import
    last_file       TEXT,                      -- filename of last imported file
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    notes           TEXT,

    CHECK (broker IN ('fidelity','ibkr','tastytrade'))
);

CREATE TABLE IF NOT EXISTS processed_transactions (
        id          TEXT PRIMARY KEY,
        run_date    TEXT NOT NULL,
        account_num TEXT,
        symbol      TEXT NOT NULL,
        action      TEXT NOT NULL,
        quantity    REAL,
        price       REAL,
        amount      REAL,
        tx_hash     TEXT UNIQUE NOT NULL,
        processed_at TEXT NOT NULL
    );

CREATE TABLE IF NOT EXISTS stock_positions (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    shares      INTEGER NOT NULL,
    cost_basis  REAL,
    acquired_at TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(account_id, ticker)
);

ALTER TABLE checks ADD COLUMN buffer_dollars REAL;
ALTER TABLE checks ADD COLUMN buffer_pct REAL;
ALTER TABLE checks ADD COLUMN rth_flag TEXT;

-- s46 (HELM-031): long-debit shadow-capture columns (nullable; informational,
-- persisted from check_cmd, never drives the acting verdict).
ALTER TABLE checks ADD COLUMN shadow_signal TEXT;
ALTER TABLE checks ADD COLUMN shadow_would_fire INTEGER;
ALTER TABLE checks ADD COLUMN shadow_loss_pct REAL;

-- HELM-008: entry_snapshots liquidity columns (open_interest / bid_ask_spread / *_pct). Introduced in code at HELM-013 (6fd56bd, entry_snapshot.py writer); live carried them ahead of the builder; back-ported here at HELM-002 Cluster B (8a9a5c3).
ALTER TABLE entry_snapshots ADD COLUMN open_interest INTEGER;
ALTER TABLE entry_snapshots ADD COLUMN bid_ask_spread REAL;
ALTER TABLE entry_snapshots ADD COLUMN bid_ask_spread_pct REAL;
-- HELM-152 (W84): the quote LEVELS, not just the gap between them.
-- Only bid_ask_spread/_pct were ever stored, so no fill can be replayed
-- against the market it was filled into, and no price threshold can be
-- set on this book's own evidence. Display/analysis only; gates nothing.
ALTER TABLE entry_snapshots ADD COLUMN bid REAL;
ALTER TABLE entry_snapshots ADD COLUMN ask REAL;

-- s25 index reconcile: forward-gap indexes (exist live, were undeclared)
CREATE INDEX IF NOT EXISTS idx_ptx_hash ON processed_transactions(tx_hash);
CREATE INDEX IF NOT EXISTS idx_ptx_date ON processed_transactions(run_date);

-- Ownership Quality cache (helm quality / HELM ownership grade)
CREATE TABLE IF NOT EXISTS ownership_quality (
    ticker        TEXT NOT NULL,
    date          TEXT NOT NULL,                       -- as-of date (YYYY-MM-DD)
    grade         TEXT NOT NULL,                       -- A-F
    composite     REAL,
    lean          TEXT,
    gates_failed  TEXT,                                -- comma-separated theme names
    confidence    TEXT,                                -- high|medium|low
    themes_json   TEXT,                                -- full breakdown, for drill-down
    source        TEXT DEFAULT 'yfinance',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, date)
);

-- ---------------------------------------------------------------------------
-- Signals scan-context migrations
--   HELM-090 p1 (2026-07-24) -- backfilled into schema 2026-07-25 (s82): these
--   four were ALTERed onto the live DB only, so a fresh init from this file was
--   missing columns the Signal dataclass declares (every save() would fail).
-- ---------------------------------------------------------------------------
ALTER TABLE signals ADD COLUMN hv_30 REAL;        -- 30d realized vol (%), scan closes
ALTER TABLE signals ADD COLUMN vrp REAL;          -- iv_current - hv_30 (vol pts)
ALTER TABLE signals ADD COLUMN vrp_ratio REAL;    -- iv_current / hv_30
ALTER TABLE signals ADD COLUMN vol_bucket TEXT;   -- rich | moderate | cheap

-- HELM-103 (2026-07-25, s82): persist the momentum_bias() regime inputs so a
-- score of 0 is distinguishable from range-gated, and ADX gating/ranking is
-- back-testable. Prerequisite for HELM-101 G2.
ALTER TABLE signals ADD COLUMN adx REAL;          -- ADX(14) trend strength
ALTER TABLE signals ADD COLUMN plus_di REAL;      -- +DI(14)
ALTER TABLE signals ADD COLUMN minus_di REAL;     -- -DI(14)
ALTER TABLE signals ADD COLUMN obv_trend INTEGER; -- +1 rising / -1 falling / 0 flat

-- HELM-101 §2/§7 (2026-07-25, s82): buy-side screen persistence. The vol-gate
-- inputs (G3/G5) and the long-call screen's own verdict/rank/gate audit trail,
-- so screen outcomes are scorable rather than eyeball-able.
ALTER TABLE signals ADD COLUMN hv_90 REAL;             -- plain 90d realized vol (%)
ALTER TABLE signals ADD COLUMN hv_90_ex_earn REAL;     -- 90d RV, earnings moves removed
ALTER TABLE signals ADD COLUMN hv_90_source TEXT;      -- dates | dates-none | plain
ALTER TABLE signals ADD COLUMN hv_252 REAL;            -- 1y realized vol (%), G5 ceiling
ALTER TABLE signals ADD COLUMN iv_hv90_ratio REAL;     -- iv_current / hv_90 raw (G3, HELM-132)
ALTER TABLE signals ADD COLUMN iv_hv90_ratio_xearn REAL;  -- HELM-133 logged, never gated
ALTER TABLE signals ADD COLUMN earn_days_since INTEGER;   -- HELM-133 days since last print
ALTER TABLE signals ADD COLUMN earn_in_hv90_window INTEGER; -- HELM-133 1 = inside trailing 90d
ALTER TABLE signals ADD COLUMN strategy_shadow TEXT;       -- HELM-136 route overridden by sell gate
ALTER TABLE signals ADD COLUMN lc_screen_pass INTEGER; -- 1 pass / 0 reject / NULL not run
ALTER TABLE signals ADD COLUMN lc_screen_rank INTEGER; -- rank within the passing field
ALTER TABLE signals ADD COLUMN lc_screen_reject TEXT;  -- first gate failed (G1..G5)
ALTER TABLE signals ADD COLUMN lc_rank_score REAL;     -- 0.6*vol cheapness + 0.4*trend
ALTER TABLE signals ADD COLUMN lc_gates_json TEXT;     -- per-gate values, audit trail

-- HELM-101 G3 support: past earnings dates, so HV90 can exclude print moves.
-- Refreshed by helm.hv_earnings.refresh_earnings_history (weekly cadence).
CREATE TABLE IF NOT EXISTS earnings_history (
    ticker      TEXT NOT NULL,
    earn_date   TEXT NOT NULL,           -- YYYY-MM-DD
    source      TEXT DEFAULT 'yfinance',
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, earn_date)
);

-- HELM-101 §4 (2026-07-25, s82): exit doctrine v2 for the LONG_* families.
-- THESIS_BREAK exits on information, so each long position needs the directional
-- read that justified it recorded at open. entry_snapshots is per-LEG and carries
-- only vol/greeks/liquidity, so the thesis lives in its own per-position table.
CREATE TABLE IF NOT EXISTS entry_thesis (
    position_id          TEXT PRIMARY KEY,
    captured_at          TEXT NOT NULL DEFAULT (datetime('now')),
    source               TEXT,    -- signals | reconstructed | live
    signals_generated_at TEXT,    -- provenance when source='signals'
    bias_score           REAL,
    spot_price           REAL,
    sma_50               REAL,
    sma_200              REAL,
    adx                  REAL,
    notes                TEXT
);

-- Daily thesis-test state, journal-native (same shape as the HELM-093 Nd counter):
-- the confirmation streak is counted from consecutive trailing checks, and every
-- LONG_* verdict records which rules were armed so alternatives stay scorable.
ALTER TABLE checks ADD COLUMN thesis_broken INTEGER;  -- 1 broken / 0 intact / NULL n/a
ALTER TABLE checks ADD COLUMN lc_arms_json TEXT;      -- counterfactual arm record

-- HELM-107 (s82): entry runway fact + artifact judgement.
ALTER TABLE positions ADD COLUMN entry_dte INTEGER;

-- HELM-121 (s90): attribution for the buy-side screen's dual-book A/B.
ALTER TABLE positions ADD COLUMN origin_screen TEXT;

-- s90: 30-day realized vol with earnings moves removed. hv_90 has had an
-- ex-earnings twin since s82; hv_30 did not, so the window that governs
-- most contracts was the earnings-contaminated one.
ALTER TABLE signals ADD COLUMN hv_30_ex_earn REAL;
ALTER TABLE signals ADD COLUMN hv_30_source TEXT;

-- W21 (s90): what each scheduled agent run actually did. Gaps are made
-- visible rather than backfilled -- see helm/agent_runs.py.
CREATE TABLE IF NOT EXISTS agent_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    slot        TEXT,
    attempted   INTEGER,
    journaled   INTEGER,
    failed      INTEGER,
    status      TEXT NOT NULL DEFAULT ('OK'),
    notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_started
    ON agent_runs(agent, started_at);

-- HELM-107 (s82): entry runway, and the artifact rule derived from it.
-- positions.entry_dte stores the FACT (days from open to the nearest expiry,
-- computed from legs). This view derives the JUDGEMENT, so the rule can be
-- changed in one place without rewriting stored data.
--
-- Why it exists: in June 2026 condors and CSPs were opened at 21-25 DTE against
-- a 21-DTE management rule and were force-closed within days, each close paying
-- a round-trip spread. ~80 of 190 closed positions are that artifact and were
-- being counted as evidence. See claude/HELM-condor-loss-root-cause.md.
--
-- NOTE: the 7-day margin mirrors open_cmd.DTE_RUNWAY_MARGIN. If one moves, move
-- the other -- this is the one place the two are deliberately coupled.
DROP VIEW IF EXISTS v_learning_positions;
CREATE VIEW v_learning_positions AS
SELECT p.*,
       (SELECT s.dte_exit_threshold FROM strategy_settings s
         WHERE s.strategy = p.strategy AND s.dte_exit_threshold IS NOT NULL
         ORDER BY s.is_default DESC LIMIT 1)                    AS manage_dte,
       p.entry_dte - (SELECT s.dte_exit_threshold FROM strategy_settings s
         WHERE s.strategy = p.strategy AND s.dte_exit_threshold IS NOT NULL
         ORDER BY s.is_default DESC LIMIT 1)                    AS entry_runway,
       CASE
         WHEN p.entry_dte IS NULL THEN NULL
         WHEN p.entry_dte - COALESCE((SELECT s.dte_exit_threshold FROM strategy_settings s
              WHERE s.strategy = p.strategy AND s.dte_exit_threshold IS NOT NULL
              ORDER BY s.is_default DESC LIMIT 1), 21) < 7 THEN 1
         ELSE 0
       END                                                      AS dte_artifact
FROM positions p;

-- HELM-107 (s82): keep entry_dte true for every future position, whichever code
-- path opens it. Legs are written after the position row, so the trigger fires
-- on leg insert and keeps the NEAREST expiry -- matching how the exit rules read
-- DTE. Self-maintaining: no open path has to remember to set it.
DROP TRIGGER IF EXISTS trg_positions_entry_dte;
CREATE TRIGGER trg_positions_entry_dte
AFTER INSERT ON legs
WHEN NEW.expiration IS NOT NULL
BEGIN
  UPDATE positions
     SET entry_dte = CAST(julianday(NEW.expiration) - julianday(date(opened_at)) AS INTEGER)
   WHERE id = NEW.position_id
     AND (entry_dte IS NULL
          OR entry_dte > CAST(julianday(NEW.expiration) - julianday(date(opened_at)) AS INTEGER));
END;


-- ---------------------------------------------------------------------------
-- W4 (s86, 2026-07-26): three tables the live DB has had for weeks and this
-- file did not, so HELM-002's "faithfully builds live" claim was no longer
-- true. DDL copied verbatim from the live sqlite_master.
--
-- check_exit_flags / check_kept_hist are ALSO created at runtime by
-- helm/exit_monitor.py::_ensure() (CREATE TABLE IF NOT EXISTS, called from
-- evaluate()), so a rebuilt database self-heals on the first `helm check` and
-- the exit monitor does NOT fail without them. They belong here anyway: a
-- schema file that omits a third of the check-side tables is not a description
-- of the database. NOTE THE DUAL DEFINITION -- change one, change the other.
--
-- stop_arm_events is the HELM-030 stop A/B recorder. It holds 198 rows frozen
-- 2026-06-29..2026-07-02 and has NO writer anywhere in live code (the recorder
-- lived in the retired paper_manage path; decision.evaluate_arms is still
-- defined but is called by nothing). Kept in the schema so a rebuild does not
-- silently lose the data model for the experiment HELM-030 is waiting on --
-- but see HELM-030: what it is waiting for stopped accruing on 2026-07-02.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS check_exit_flags (
    position_id   TEXT PRIMARY KEY,
    flag_kept_pct REAL NOT NULL,
    flag_date     TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS check_kept_hist (
    position_id  TEXT NOT NULL,
    session_date TEXT NOT NULL,
    kept_pct     REAL NOT NULL,
    PRIMARY KEY (position_id, session_date)
);

CREATE TABLE IF NOT EXISTS stop_arm_events (
    id                TEXT PRIMARY KEY,             -- '{position_id}:{arm}'
    position_id       TEXT NOT NULL REFERENCES positions(id),
    arm               TEXT NOT NULL,                -- no_stop | ml_50 | ml_75 | cr_2x | cr_3x
    basis             TEXT NOT NULL,                -- MAX_LOSS | CREDIT_MULT
    threshold_dollars REAL,                         -- frozen at entry; NULL for no_stop
    triggered_ts      TEXT,                         -- first-touch tick ts; NULL if never
    pnl_at_trigger    REAL,                         -- pnl at first-touch
    natural_exit_ts   TEXT,                         -- acting (no-stop) close ts
    natural_exit_pnl  REAL,                         -- realized pnl at natural exit
    status            TEXT NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | CLOSED
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (arm    IN ('no_stop','ml_50','ml_75','cr_2x','cr_3x')),
    CHECK (basis  IN ('MAX_LOSS','CREDIT_MULT')),
    CHECK (status IN ('ACTIVE','CLOSED'))
);

CREATE INDEX IF NOT EXISTS idx_sae_position ON stop_arm_events(position_id);
CREATE INDEX IF NOT EXISTS idx_sae_status   ON stop_arm_events(status);

-- W91 / HELM-143 -- exit-improvement alerts (notify-only; the engine also
-- creates this on first use via helm/exit_alert.py)
CREATE TABLE IF NOT EXISTS exit_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL,
    alert_date TEXT NOT NULL,
    fired_at TEXT NOT NULL,
    today_best REAL,
    best_prior REAL,
    threshold REAL,
    message TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exit_alerts_pos_day
    ON exit_alerts(position_id, alert_date);
