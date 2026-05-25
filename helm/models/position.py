# helm/models/position.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

from helm.db import get_conn, transaction, row_to_dict

STRATEGIES = [
    'CSP','COVERED_CALL','LONG_CALL','PERM',
    'BULL_PUT_SPREAD','BEAR_CALL_SPREAD','IRON_CONDOR',
    'DIAGONAL','PMCC','SHORT_STRANGLE','JADE_LIZARD'
]
STATUSES = ['OPEN','CLOSED','EXPIRED','ASSIGNED','ROLLED_OUT']

@dataclass
class Position:
    id:                   str
    account_id:           str
    strategy:             str
    ticker:               str
    status:               str = 'OPEN'
    opened_at:            str = field(default_factory=lambda: datetime.now().isoformat())
    signal_id:            Optional[str] = None
    closed_at:            Optional[str] = None
    earnings_date:        Optional[str] = None
    company_name:         Optional[str] = None
    total_contracts:      int = 1
    net_premium:          Optional[float] = None
    realized_pnl:         Optional[float] = None
    max_profit:           Optional[float] = None
    max_loss:             Optional[float] = None
    breakeven_low:        Optional[float] = None
    breakeven_high:       Optional[float] = None
    spread_width:         Optional[float] = None
    credit_to_width_ratio: Optional[float] = None
    credit_exceeds_width: Optional[int] = None
    willing_to_own:       Optional[int] = None
    parent_position_id:   Optional[str] = None
    notes:                Optional[str] = None
    tags:                 Optional[str] = None
    created_at:           str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at:           str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        if self.strategy not in STRATEGIES:
            raise ValueError(f'Unknown strategy: {self.strategy}')
        if self.status not in STATUSES:
            raise ValueError(f'Unknown status: {self.status}')

    @classmethod
    def new_id(cls, ticker: str, strategy: str) -> str:
        ts = datetime.now().strftime('%Y%m%d')
        return f'{ticker}-{strategy}-{ts}-{uuid.uuid4().hex[:6].upper()}'

    @classmethod
    def create(cls, account_id: str, strategy: str, ticker: str, **kwargs) -> Position:
        pos = cls(
            id=kwargs.pop('id', cls.new_id(ticker, strategy)),
            account_id=account_id,
            strategy=strategy,
            ticker=ticker,
            **kwargs
        )
        pos.save()
        return pos

    @classmethod
    def from_row(cls, row) -> Position:
        return cls(**dict(row))

    @classmethod
    def get(cls, position_id: str) -> Optional[Position]:
        conn = get_conn()
        try:
            row = conn.execute('SELECT * FROM positions WHERE id = ?', (position_id,)).fetchone()
            return cls.from_row(row) if row else None
        finally:
            conn.close()

    @classmethod
    def open_positions(cls, account_id: Optional[str] = None,
                       strategy: Optional[str] = None) -> list[Position]:
        conn = get_conn()
        try:
            sql = 'SELECT * FROM positions WHERE status = ?'
            params = ['OPEN']
            if account_id:
                sql += ' AND account_id = ?'
                params.append(account_id)
            if strategy:
                sql += ' AND strategy = ?'
                params.append(strategy)
            sql += ' ORDER BY opened_at DESC'
            rows = conn.execute(sql, params).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def by_ticker(cls, ticker: str, status: Optional[str] = None) -> list[Position]:
        conn = get_conn()
        try:
            if status:
                rows = conn.execute(
                    'SELECT * FROM positions WHERE ticker = ? AND status = ? ORDER BY opened_at DESC',
                    (ticker, status)
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM positions WHERE ticker = ? ORDER BY opened_at DESC',
                    (ticker,)
                ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def history(cls, account_id: str, limit: int = 50) -> list[Position]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM positions WHERE account_id = ? ORDER BY opened_at DESC LIMIT ?',
                (account_id, limit)
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    def save(self) -> Position:
        self.updated_at = datetime.now().isoformat()
        with transaction() as conn:
            # INSERT OR IGNORE to create if not exists, then UPDATE to modify
            # Using INSERT OR IGNORE + UPDATE avoids INSERT OR REPLACE which would
            # DELETE the row first, cascading to delete all legs via ON DELETE CASCADE
            conn.execute("""
                INSERT OR IGNORE INTO positions (
                    id, account_id, signal_id, strategy, ticker, company_name,
                    status, opened_at, closed_at, earnings_date, total_contracts,
                    net_premium, realized_pnl, max_profit, max_loss,
                    breakeven_low, breakeven_high, spread_width,
                    credit_to_width_ratio, credit_exceeds_width, willing_to_own,
                    parent_position_id, notes, tags, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                self.id, self.account_id, self.signal_id, self.strategy,
                self.ticker, self.company_name, self.status, self.opened_at,
                self.closed_at, self.earnings_date, self.total_contracts,
                self.net_premium, self.realized_pnl, self.max_profit, self.max_loss,
                self.breakeven_low, self.breakeven_high, self.spread_width,
                self.credit_to_width_ratio, self.credit_exceeds_width,
                self.willing_to_own, self.parent_position_id,
                self.notes, self.tags, self.created_at, self.updated_at
            ))
            conn.execute("""
                UPDATE positions SET
                    account_id=?, signal_id=?, strategy=?, ticker=?, company_name=?,
                    status=?, opened_at=?, closed_at=?, earnings_date=?, total_contracts=?,
                    net_premium=?, realized_pnl=?, max_profit=?, max_loss=?,
                    breakeven_low=?, breakeven_high=?, spread_width=?,
                    credit_to_width_ratio=?, credit_exceeds_width=?, willing_to_own=?,
                    parent_position_id=?, notes=?, tags=?, updated_at=?
                WHERE id=?
            """, (
                self.account_id, self.signal_id, self.strategy,
                self.ticker, self.company_name, self.status, self.opened_at,
                self.closed_at, self.earnings_date, self.total_contracts,
                self.net_premium, self.realized_pnl, self.max_profit, self.max_loss,
                self.breakeven_low, self.breakeven_high, self.spread_width,
                self.credit_to_width_ratio, self.credit_exceeds_width,
                self.willing_to_own, self.parent_position_id,
                self.notes, self.tags, self.updated_at, self.id
            ))
        return self

    def close(self, realized_pnl: float, closed_at: Optional[str] = None) -> Position:
        self.status = 'CLOSED'
        self.realized_pnl = realized_pnl
        self.closed_at = closed_at or datetime.now().isoformat()
        return self.save()

    def mark_rolled(self) -> Position:
        self.status = 'ROLLED_OUT'
        self.closed_at = datetime.now().isoformat()
        return self.save()

    def mark_assigned(self) -> Position:
        self.status = 'ASSIGNED'
        self.closed_at = datetime.now().isoformat()
        return self.save()

    def mark_expired(self) -> Position:
        self.status = 'EXPIRED'
        self.closed_at = datetime.now().isoformat()
        return self.save()

    @property
    def is_open(self) -> bool:
        return self.status == 'OPEN'

    @property
    def is_credit(self) -> bool:
        return self.strategy in ['CSP','COVERED_CALL','BULL_PUT_SPREAD',
                                  'BEAR_CALL_SPREAD','IRON_CONDOR',
                                  'SHORT_STRANGLE','JADE_LIZARD']

    @property
    def is_debit(self) -> bool:
        return self.strategy in ['LONG_CALL','PERM','DIAGONAL','PMCC']

    @property
    def has_undefined_risk(self) -> bool:
        return self.max_loss is None and self.strategy in ['CSP','SHORT_STRANGLE']

    def __str__(self) -> str:
        prem = f'net {self.net_premium:+.2f}' if self.net_premium else ''
        sig = f' [sig: {self.signal_id[:12]}]' if self.signal_id else ''
        return f'[{self.status}] {self.ticker} {self.strategy} {prem}{sig} (opened {self.opened_at[:10]})'
