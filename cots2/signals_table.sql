
-- ============================================================
-- SIGNALS
-- Per-ticker strategy recommendations. Never deleted.
-- The intelligence layer between watchlist and position opening.
-- Stores what HELM recommended, why, and what the user decided.
-- Links to positions via position.signal_id for outcome analysis.
--
-- Workflow:
--   1. helm scan → generates Signal for each optionable ticker
--   2. HELM computes auto_bias from technicals
--   3. User accepts or overrides bias (confirmed_bias)
--   4. HELM produces ranked strategy recommendations
--   5. User opens a position → position.signal_id = this signal
--   6. Position closes → outcome recorded here for analysis
-- ============================================================
CREATE TABLE IF NOT EXISTS signals (
    id                  TEXT PRIMARY KEY,
    ticker              TEXT NOT NULL,
    generated_at        TEXT NOT NULL,

    -- ── Volatility environment ───────────────────────────────
    iv_current          REAL,
    iv_rank             REAL,             -- 0-100
    iv_percentile       REAL,             -- 0-100
    iv_regime           TEXT,             -- HIGH | MODERATE | LOW
    -- HIGH: iv_rank >= 50 → sell premium
    -- MODERATE: 30-50 → discretionary
    -- LOW: < 30 → buy premium

    -- ── Technical indicators ─────────────────────────────────
    spot_price          REAL,
    ema_20              REAL,
    sma_50              REAL,
    sma_200             REAL,
    rsi_14              REAL,
    macd_line           REAL,
    macd_signal         REAL,
    macd_histogram      REAL,
    atr_14              REAL,
    bb_width            REAL,             -- Bollinger Band width (squeeze indicator)
    bb_upper            REAL,
    bb_lower            REAL,
    bb_squeeze          INTEGER,          -- 1 = bands contracting (low vol, potential breakout)

    -- ── Derived signals ──────────────────────────────────────
    price_vs_ema20      TEXT,             -- ABOVE | BELOW | AT
    price_vs_sma50      TEXT,             -- ABOVE | BELOW | AT
    price_vs_sma200     TEXT,             -- ABOVE | BELOW | AT
    rsi_condition       TEXT,             -- OVERBOUGHT | OVERSOLD | NEUTRAL
    -- OVERBOUGHT: RSI > 70, OVERSOLD: RSI < 30, NEUTRAL: 30-70
    macd_condition      TEXT,             -- BULLISH | BEARISH | NEUTRAL
    -- BULLISH: macd > signal, BEARISH: macd < signal
    trend_strength      TEXT,             -- STRONG | MODERATE | WEAK | NONE
    -- Based on price vs 50/200 SMA alignment

    -- ── Bias (the core of the signal) ────────────────────────
    auto_bias_score     REAL,             -- computed: -3.0 to +3.0
    auto_bias           TEXT,             -- BEARISH | MILDLY_BEARISH | NEUTRAL |
                                          -- MILDLY_BULLISH | BULLISH
    auto_bias_reasoning TEXT,             -- plain-English: why HELM computed this bias

    -- User override (Option C: auto-compute, always allow override)
    user_bias_override  TEXT,             -- BEARISH | MILDLY_BEARISH | NEUTRAL |
                                          -- MILDLY_BULLISH | BULLISH | null if accepted
    confirmed_bias      TEXT NOT NULL,    -- final bias used for recommendations
                                          -- = user_bias_override if set, else auto_bias

    -- ── Strategy recommendations ──────────────────────────────
    -- Stored as JSON array, ordered by fit score descending
    -- Each entry: {strategy, fit, fit_score, reasoning, suggested_strike,
    --              suggested_dte, atr_strikes_otm, position_size_contracts}
    recommendations     TEXT NOT NULL,    -- JSON array

    -- Top recommendation (denormalized for fast querying)
    top_strategy        TEXT,
    top_fit             TEXT,             -- STRONG | GOOD | MODERATE | WEAK

    -- ── ATR-based sizing guidance ─────────────────────────────
    atr_1x_price        REAL,             -- spot ± 1 ATR (typical daily range)
    atr_2x_price        REAL,             -- spot ± 2 ATR (extended move)
    suggested_contracts INTEGER,          -- based on account buying power + ATR sizing

    -- ── Earnings context ──────────────────────────────────────
    earnings_date       TEXT,             -- next earnings date if known
    days_to_earnings    INTEGER,          -- null if no earnings on horizon
    earnings_warning    INTEGER DEFAULT 0, -- 1 = earnings within 30 days, affects strategy

    -- ── Watchlist context ─────────────────────────────────────
    willing_to_own      INTEGER,          -- from watchlist at time of signal
    is_optionable       INTEGER,          -- from watchlist at time of signal

    -- ── Outcome tracking (filled when linked position closes) ──
    -- Links: position.signal_id → this signal
    -- These fields are updated when the resulting position is closed
    position_opened     INTEGER DEFAULT 0, -- 1 = a position was opened from this signal
    position_id         TEXT,              -- the position opened (if any)
    outcome_pnl         REAL,              -- realized PnL of that position
    outcome_result      TEXT,              -- WIN | LOSS | BREAKEVEN | EXPIRED | ASSIGNED
    outcome_notes       TEXT,              -- what actually happened

    -- ── Data provenance ───────────────────────────────────────
    data_source         TEXT DEFAULT 'yfinance',
    data_quality        TEXT DEFAULT 'GOOD',  -- GOOD | PARTIAL | STALE

    created_at          TEXT NOT NULL DEFAULT (datetime('now')),

    -- Signals are never deleted — this is enforced at the application layer.
    -- No CASCADE deletes. No TTL. The signal record is permanent.

    CHECK (iv_regime IN ('HIGH','MODERATE','LOW') OR iv_regime IS NULL),
    CHECK (auto_bias IN ('BEARISH','MILDLY_BEARISH','NEUTRAL','MILDLY_BULLISH','BULLISH') OR auto_bias IS NULL),
    CHECK (confirmed_bias IN ('BEARISH','MILDLY_BEARISH','NEUTRAL','MILDLY_BULLISH','BULLISH')),
    CHECK (top_fit IN ('STRONG','GOOD','MODERATE','WEAK') OR top_fit IS NULL),
    CHECK (outcome_result IN ('WIN','LOSS','BREAKEVEN','EXPIRED','ASSIGNED') OR outcome_result IS NULL),
    CHECK (data_quality IN ('GOOD','PARTIAL','STALE'))
);

-- Signals indexes — optimized for the analysis queries we want to run:
-- "show me all NVDA signals over time"
-- "which signals led to wins vs losses"
-- "did high IV rank signals outperform low IV rank signals"
-- "how accurate was the auto_bias vs confirmed_bias"
CREATE INDEX IF NOT EXISTS idx_signals_ticker    ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_generated ON signals(generated_at);
CREATE INDEX IF NOT EXISTS idx_signals_iv_regime ON signals(iv_regime);
CREATE INDEX IF NOT EXISTS idx_signals_bias      ON signals(confirmed_bias);
CREATE INDEX IF NOT EXISTS idx_signals_strategy  ON signals(top_strategy);
CREATE INDEX IF NOT EXISTS idx_signals_outcome   ON signals(outcome_result);
CREATE INDEX IF NOT EXISTS idx_signals_position  ON signals(position_id);
CREATE INDEX IF NOT EXISTS idx_signals_ticker_ts ON signals(ticker, generated_at);
