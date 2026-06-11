# helm/models/watchlist.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

from helm.db import get_conn, transaction

@dataclass
class WatchlistItem:
    id:               str
    ticker:           str
    is_optionable:    int = 0
    willing_to_own:   int = 1
    active:           int = 0
    company_name:     Optional[str] = None
    sector:           Optional[str] = None
    last_screened_at: Optional[str] = None
    thesis:           Optional[str] = None
    # Fundamentals
    market_cap:       Optional[float] = None
    avg_daily_volume: Optional[float] = None
    week_52_high:     Optional[float] = None
    week_52_low:      Optional[float] = None
    beta:             Optional[float] = None
    dividend_yield:   Optional[float] = None
    next_earnings:    Optional[str] = None
    last_fundamentals_at: Optional[str] = None
    added_at:         str = field(default_factory=lambda: datetime.now().isoformat())
    notes:            Optional[str] = None

    @classmethod
    def add(cls, ticker: str, **kwargs) -> WatchlistItem:
        item = cls(
            id=kwargs.pop('id', 'WL-' + uuid.uuid4().hex[:8].upper()),
            ticker=ticker.upper(),
            **kwargs
        )
        item.save()
        return item

    @classmethod
    def from_row(cls, row) -> WatchlistItem:
        d = dict(row)
        d['is_optionable'] = int(d.get('is_optionable', 0))
        d['willing_to_own'] = int(d.get('willing_to_own', 1))
        d['active'] = int(d.get('active', 0) or 0)
        return cls(**d)

    @classmethod
    def get(cls, ticker: str) -> Optional[WatchlistItem]:
        conn = get_conn()
        try:
            row = conn.execute('SELECT * FROM watchlist WHERE ticker = ?', (ticker.upper(),)).fetchone()
            return cls.from_row(row) if row else None
        finally:
            conn.close()

    @classmethod
    def all(cls) -> list[WatchlistItem]:
        conn = get_conn()
        try:
            rows = conn.execute('SELECT * FROM watchlist ORDER BY ticker').fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def optionable(cls) -> list[WatchlistItem]:
        conn = get_conn()
        try:
            rows = conn.execute('SELECT * FROM watchlist WHERE is_optionable = 1 ORDER BY ticker').fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def willing_to_own_list(cls) -> list[WatchlistItem]:
        conn = get_conn()
        try:
            rows = conn.execute('SELECT * FROM watchlist WHERE willing_to_own = 1 AND is_optionable = 1 ORDER BY ticker').fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def active(cls) -> list[WatchlistItem]:
        conn = get_conn()
        try:
            rows = conn.execute('SELECT * FROM watchlist WHERE active = 1 AND is_optionable = 1 ORDER BY ticker').fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    def mark_screened(self, is_optionable: bool) -> WatchlistItem:
        self.is_optionable = 1 if is_optionable else 0
        self.last_screened_at = datetime.now().isoformat()
        return self.save()

    def update_fundamentals(self, **kwargs) -> WatchlistItem:
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.last_fundamentals_at = datetime.now().isoformat()
        return self.save()

    def save(self) -> WatchlistItem:
        with transaction() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO watchlist (
                    id, ticker, company_name, sector, is_optionable,
                    last_screened_at, willing_to_own, thesis,
                    market_cap, avg_daily_volume, week_52_high, week_52_low,
                    beta, dividend_yield, next_earnings, last_fundamentals_at,
                    added_at, notes, active
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                self.id, self.ticker, self.company_name, self.sector,
                self.is_optionable, self.last_screened_at, self.willing_to_own,
                self.thesis, self.market_cap, self.avg_daily_volume,
                self.week_52_high, self.week_52_low, self.beta,
                self.dividend_yield, self.next_earnings,
                self.last_fundamentals_at, self.added_at, self.notes, self.active
            ))
        return self

    def __str__(self) -> str:
        opt = 'optionable' if self.is_optionable else 'not screened'
        wto = 'WTO' if self.willing_to_own else 'no-WTO'
        cap = f' ${self.market_cap:.0f}B' if self.market_cap else ''
        return f'{self.ticker} [{opt}] [{wto}]{cap}'