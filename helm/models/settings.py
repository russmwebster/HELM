# helm/models/settings.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

from helm.db import get_conn, transaction, row_to_dict

@dataclass
class StrategySettings:
    id:                          str
    account_id:                  str
    strategy:                    str
    entry_iv_rank_min:           Optional[float] = None
    entry_iv_rank_max:           Optional[float] = None
    entry_delta_min:             Optional[float] = None
    entry_delta_max:             Optional[float] = None
    entry_dte_min:               Optional[int] = None
    entry_dte_max:               Optional[int] = None
    leaps_delta_min:             Optional[float] = None
    leaps_dte_min:               Optional[int] = None
    extrinsic_ratio_min:         Optional[float] = None
    profit_target_pct:           Optional[float] = None
    stop_loss_multiplier:        Optional[float] = None
    risk_pct_per_trade:          Optional[float] = None
    dte_exit_threshold:          Optional[int] = None
    delta_drift_warning:         Optional[float] = None
    delta_danger:                Optional[float] = None
    iv_increase_warning:         Optional[float] = None
    dte_review_threshold:        Optional[int] = None
    net_delta_warning:           Optional[float] = None
    enforce_credit_exceeds_width: Optional[int] = 1
    days_before_earnings_exit:   Optional[int] = None
    perm_profit_target_pct:      Optional[float] = None
    is_default:                  int = 0
    last_modified:               str = field(default_factory=lambda: datetime.now().isoformat())
    notes:                       Optional[str] = None

    @classmethod
    def from_row(cls, row) -> StrategySettings:
        return cls(**dict(row))

    @classmethod
    def get(cls, account_id: str, strategy: str) -> Optional[StrategySettings]:
        conn = get_conn()
        try:
            row = conn.execute(
                'SELECT * FROM strategy_settings WHERE account_id = ? AND strategy = ?',
                (account_id, strategy)
            ).fetchone()
            return cls.from_row(row) if row else None
        finally:
            conn.close()

    @classmethod
    def all_for_account(cls, account_id: str) -> list[StrategySettings]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM strategy_settings WHERE account_id = ? ORDER BY strategy',
                (account_id,)
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    def save(self) -> StrategySettings:
        self.last_modified = datetime.now().isoformat()
        with transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO strategy_settings (
                    id, account_id, strategy, entry_iv_rank_min, entry_iv_rank_max,
                    entry_delta_min, entry_delta_max, entry_dte_min, entry_dte_max,
                    leaps_delta_min, leaps_dte_min, extrinsic_ratio_min,
                    profit_target_pct, stop_loss_multiplier, risk_pct_per_trade, dte_exit_threshold,
                    delta_drift_warning, delta_danger, iv_increase_warning,
                    dte_review_threshold, net_delta_warning,
                    enforce_credit_exceeds_width, days_before_earnings_exit,
                    perm_profit_target_pct, is_default, last_modified, notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                self.id, self.account_id, self.strategy,
                self.entry_iv_rank_min, self.entry_iv_rank_max,
                self.entry_delta_min, self.entry_delta_max,
                self.entry_dte_min, self.entry_dte_max,
                self.leaps_delta_min, self.leaps_dte_min, self.extrinsic_ratio_min,
                self.profit_target_pct, self.stop_loss_multiplier, self.risk_pct_per_trade, self.dte_exit_threshold,
                self.delta_drift_warning, self.delta_danger, self.iv_increase_warning,
                self.dte_review_threshold, self.net_delta_warning,
                self.enforce_credit_exceeds_width, self.days_before_earnings_exit,
                self.perm_profit_target_pct, self.is_default,
                self.last_modified, self.notes
            ))
        return self

    def __str__(self) -> str:
        return f'[{self.strategy}] delta {self.entry_delta_min}-{self.entry_delta_max} | DTE {self.entry_dte_min}-{self.entry_dte_max} | target {self.profit_target_pct}'
