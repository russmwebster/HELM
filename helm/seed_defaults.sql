-- HELM Strategy Settings — Practitioner Defaults
-- seed_defaults.sql
--
-- These defaults are pre-populated at setup for each account.
-- Every value is user-editable via: helm settings <strategy>
-- All values sourced from practitioner research (2026).
-- is_default=1 means system default; user edits set is_default=0.
--
-- Usage: called by helm setup after account creation
-- INSERT OR IGNORE so user customizations are never overwritten.

-- Helper: replace ACCOUNT_ID with the actual account id at runtime

-- ============================================================
-- CSP — Cash-Secured Put
-- Entry: IV Rank 20-50, delta 0.20-0.30, DTE 30-45
-- Exit: 50% profit, 2x stop, roll at 21 DTE if threatened
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min, entry_iv_rank_max,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    delta_drift_warning, delta_danger,
    iv_increase_warning,
    is_default
) VALUES (
    'default_CSP_'||:account_id, :account_id, 'CSP',
    20, 70,
    0.20, 0.30,
    30, 45,
    0.50, 2.0,
    7, 21,
    0.20, 0.65,
    0.05,
    1
);

-- ============================================================
-- COVERED_CALL
-- Entry: delta 0.20-0.40, DTE 30-45, moderate-high IV
-- Exit: 50% profit, roll when delta reaches 0.50
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min, entry_iv_rank_max,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    delta_drift_warning, delta_danger,
    iv_increase_warning,
    is_default
) VALUES (
    'default_COVERED_CALL_'||:account_id, :account_id, 'COVERED_CALL',
    20, 80,
    0.20, 0.40,
    30, 45,
    0.50, NULL,
    7, 21,
    0.15, 0.60,
    0.08,
    1
);

-- ============================================================
-- LONG_CALL
-- Entry: low IV preferred, delta 0.40-0.70, DTE 60-90+
-- Exit: 50-100% profit target, 30-50% stop loss of premium
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min, entry_iv_rank_max,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    delta_drift_warning,
    iv_increase_warning,
    is_default
) VALUES (
    'default_LONG_CALL_'||:account_id, :account_id, 'LONG_CALL',
    NULL, 40,
    0.40, 0.70,
    60, 180,
    0.75, NULL,
    21, 30,
    0.20,
    NULL,
    1
);

-- ============================================================
-- PERM — Pre-Earnings Run-up
-- Entry: 5-15 days before earnings, rising IV
-- Exit: HARD RULE — 1-2 days before earnings, 25% profit target
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    days_before_earnings_exit,
    perm_profit_target_pct,
    is_default
) VALUES (
    'default_PERM_'||:account_id, :account_id, 'PERM',
    30,
    0.40, 0.70,
    7, 21,
    0.25, NULL,
    NULL, 3,
    1,
    0.25,
    1
);

-- ============================================================
-- BULL_PUT_SPREAD
-- Entry: IV Rank >=30, short delta 0.20-0.30, DTE 30-45
-- Exit: 50% profit, 2x stop, roll if short strike breached w/ 7+ DTE
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min, entry_iv_rank_max,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    delta_drift_warning, delta_danger,
    iv_increase_warning,
    is_default
) VALUES (
    'default_BULL_PUT_SPREAD_'||:account_id, :account_id, 'BULL_PUT_SPREAD',
    30, 80,
    0.20, 0.30,
    30, 45,
    0.50, 2.0,
    7, 21,
    0.15, 0.50,
    0.05,
    1
);

-- ============================================================
-- BEAR_CALL_SPREAD
-- Entry: IV Rank >=30, short delta 0.15-0.25, DTE 30-45
-- Best entered after a rally into resistance
-- Exit: 50% profit, 2x stop
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min, entry_iv_rank_max,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    delta_drift_warning, delta_danger,
    iv_increase_warning,
    is_default
) VALUES (
    'default_BEAR_CALL_SPREAD_'||:account_id, :account_id, 'BEAR_CALL_SPREAD',
    30, 80,
    0.15, 0.25,
    30, 45,
    0.50, 2.0,
    7, 21,
    0.15, 0.45,
    0.05,
    1
);

-- ============================================================
-- IRON_CONDOR
-- Entry: IV Rank >=40, both sides delta 0.10-0.20, DTE 30-45
-- Monitor: net delta ±30, tested side delta 0.30-0.35 = adjust
-- Exit: 50% profit, 2x stop, hard exit at 21 DTE
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min, entry_iv_rank_max,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    delta_drift_warning, delta_danger,
    iv_increase_warning,
    net_delta_warning,
    is_default
) VALUES (
    'default_IRON_CONDOR_'||:account_id, :account_id, 'IRON_CONDOR',
    40, 90,
    0.10, 0.20,
    30, 45,
    0.50, 2.0,
    21, 21,
    0.15, 0.35,
    0.05,
    30,
    1
);

-- ============================================================
-- DIAGONAL
-- Long: delta 0.60-0.80, DTE 75-120+
-- Short: delta 0.20-0.35, DTE 30-45
-- Exit: 50% of debit paid, stop at 30-40% of debit
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    delta_danger,
    is_default
) VALUES (
    'default_DIAGONAL_'||:account_id, :account_id, 'DIAGONAL',
    NULL,
    0.20, 0.35,
    30, 45,
    0.50, NULL,
    30, 30,
    0.45,
    1
);

-- ============================================================
-- PMCC — Poor Man's Covered Call
-- Long (LEAPS): delta >=0.75, DTE 365+
-- Short: delta 0.15-0.35, DTE 30-45
-- Critical rule: LEAPS extrinsic >= 2x short call extrinsic
-- Roll LEAPS when delta drops below 0.70 or DTE < 90
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    leaps_delta_min, leaps_dte_min, extrinsic_ratio_min,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    delta_danger,
    is_default
) VALUES (
    'default_PMCC_'||:account_id, :account_id, 'PMCC',
    NULL,
    0.15, 0.35,
    30, 45,
    0.70, 365, 2.0,
    0.50, NULL,
    21, 90,
    0.50,
    1
);

-- ============================================================
-- SHORT_STRANGLE
-- Entry: IV Rank >=40, both sides delta ~0.20, DTE 28-45
-- Monitor: net delta ±30, either side reaching 0.30 = watch
-- Exit: 25-50% of credit, hard exit at 7 DTE, 2x stop
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min, entry_iv_rank_max,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    delta_drift_warning, delta_danger,
    iv_increase_warning,
    net_delta_warning,
    is_default
) VALUES (
    'default_SHORT_STRANGLE_'||:account_id, :account_id, 'SHORT_STRANGLE',
    40, 90,
    0.18, 0.22,
    28, 45,
    0.50, 2.0,
    7, 14,
    0.12, 0.35,
    0.05,
    30,
    1
);

-- ============================================================
-- JADE_LIZARD
-- Entry: IV Rank >=50, put delta 0.20-0.30, call delta 0.15-0.20
-- Critical rule: total credit > call spread width (no upside risk)
-- Exit: 50% profit, 14-21 DTE exit
-- ============================================================
INSERT OR IGNORE INTO strategy_settings (
    id, account_id, strategy,
    entry_iv_rank_min, entry_iv_rank_max,
    entry_delta_min, entry_delta_max,
    entry_dte_min, entry_dte_max,
    profit_target_pct, stop_loss_multiplier,
    dte_exit_threshold, dte_review_threshold,
    delta_drift_warning, delta_danger,
    iv_increase_warning,
    enforce_credit_exceeds_width,
    is_default
) VALUES (
    'default_JADE_LIZARD_'||:account_id, :account_id, 'JADE_LIZARD',
    50, 90,
    0.20, 0.30,
    30, 60,
    0.50, 2.0,
    14, 21,
    0.15, 0.50,
    0.05,
    1,
    1
);
