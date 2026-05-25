# helm/models/check.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

from helm.db import get_conn, transaction, row_to_dict

HEALTH_FLAGS = ['GREEN', 'YELLOW', 'RED']
ACTION_SIGNALS = ['HOLD', 'WATCH', 'ADJUST', 'CLOSE', 'ROLL']
DATA_QUALITY = ['GOOD', 'PARTIAL', 'STALE']

@dataclass
class Check:
    id:                str
    position_id:       str
    checked_at:        str
    health_flag:       str
    action_signal:     str
    spot_price:        Optional[float] = None
    dte_now:           Optional[int] = None
    days_open:         Optional[int] = None
    days_to_earnings:  Optional[int] = None
    current_bid:       Optional[float] = None
    current_ask:       Optional[float] = None
    current_price:     Optional[float] = None
    delta:             Optional[float] = None
    gamma:             Optional[float] = None
    theta:             Optional[float] = None
    vega:              Optional[float] = None
    iv_current:        Optional[float] = None
    delta_vs_entry:    Optional[float] = None
    iv_vs_entry:       Optional[float] = None
    spot_pct_change:   Optional[float] = None
    iv_rank:           Optional[float] = None
    iv_percentile:     Optional[float] = None
    skew_put_iv:       Optional[float] = None
    skew_call_iv:      Optional[float] = None
    skew_value:        Optional[float] = None
    pnl_unrealized:    Optional[float] = None
    pnl_pct:           Optional[float] = None
    pnl_vs_theta:      Optional[float] = None
    leaps_delta_now:   Optional[float] = None
    spread_compression: Optional[float] = None
    narrative:         Optional[str] = None
    greeks_source:     Optional[str] = None
    data_quality:      str = 'GOOD'
    created_at:        str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        if self.health_flag not in HEALTH_FLAGS:
            raise ValueError(f'Invalid health_flag: {self.health_flag}')
        if self.action_signal not in ACTION_SIGNALS:
            raise ValueError(f'Invalid action_signal: {self.action_signal}')
        if self.data_quality not in DATA_QUALITY:
            raise ValueError(f'Invalid data_quality: {self.data_quality}')

    @classmethod
    def create(cls, position_id: str, health_flag: str, action_signal: str, **kwargs) -> Check:
        checked_at = kwargs.pop('checked_at', datetime.now().isoformat())
        check = cls(
            id=kwargs.pop('id', 'CHK-' + uuid.uuid4().hex[:8].upper()),
            position_id=position_id,
            checked_at=checked_at,
            health_flag=health_flag,
            action_signal=action_signal,
            **kwargs
        )
        check.save()
        return check

    @classmethod
    def from_row(cls, row) -> Check:
        return cls(**dict(row))

    @classmethod
    def for_position(cls, position_id: str, limit: int = 50) -> list[Check]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM checks WHERE position_id = ? ORDER BY checked_at DESC LIMIT ?',
                (position_id, limit)
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def latest(cls, position_id: str) -> Optional[Check]:
        conn = get_conn()
        try:
            row = conn.execute(
                'SELECT * FROM checks WHERE position_id = ? ORDER BY checked_at DESC LIMIT 1',
                (position_id,)
            ).fetchone()
            return cls.from_row(row) if row else None
        finally:
            conn.close()

    @classmethod
    def flagged(cls, flag: str = 'RED', account_id: Optional[str] = None) -> list[Check]:
        conn = get_conn()
        try:
            if account_id:
                rows = conn.execute("""
                    SELECT c.* FROM checks c
                    JOIN positions p ON c.position_id = p.id
                    WHERE c.health_flag = ? AND p.account_id = ? AND p.status = 'OPEN'
                    ORDER BY c.checked_at DESC
                """, (flag, account_id)).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM checks WHERE health_flag = ? ORDER BY checked_at DESC',
                    (flag,)
                ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    def save(self) -> Check:
        # Soft duplicate guard: if a check already exists for this position+timestamp,
        # raise clearly rather than silently failing or overwriting.
        conn = get_conn()
        try:
            existing = conn.execute(
                'SELECT id FROM checks WHERE position_id = ? AND checked_at = ?',
                (self.position_id, self.checked_at)
            ).fetchone()
            if existing and existing['id'] != self.id:
                raise ValueError(
                    f'A check already exists for position {self.position_id} '
                    f'at {self.checked_at}. Delete it first to replace.'
                )
        finally:
            conn.close()

        with transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO checks (
                    id, position_id, checked_at, spot_price, dte_now, days_open,
                    days_to_earnings, current_bid, current_ask, current_price,
                    delta, gamma, theta, vega, iv_current, delta_vs_entry,
                    iv_vs_entry, spot_pct_change, iv_rank, iv_percentile,
                    skew_put_iv, skew_call_iv, skew_value, pnl_unrealized,
                    pnl_pct, pnl_vs_theta, leaps_delta_now, spread_compression,
                    health_flag, action_signal, narrative, greeks_source,
                    data_quality, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                self.id, self.position_id, self.checked_at, self.spot_price,
                self.dte_now, self.days_open, self.days_to_earnings,
                self.current_bid, self.current_ask, self.current_price,
                self.delta, self.gamma, self.theta, self.vega, self.iv_current,
                self.delta_vs_entry, self.iv_vs_entry, self.spot_pct_change,
                self.iv_rank, self.iv_percentile, self.skew_put_iv,
                self.skew_call_iv, self.skew_value, self.pnl_unrealized,
                self.pnl_pct, self.pnl_vs_theta, self.leaps_delta_now,
                self.spread_compression, self.health_flag, self.action_signal,
                self.narrative, self.greeks_source, self.data_quality, self.created_at
            ))
        return self

    def emoji(self) -> str:
        return {'GREEN': 'OK', 'YELLOW': '!!', 'RED': '!!!'}.get(self.health_flag, '??')

    def __str__(self) -> str:
        return f'[{self.health_flag}] {self.action_signal} @ {self.checked_at[:10]} — {self.narrative or "no narrative"}'
