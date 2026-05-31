"""
helm/models/iv_history.py
IV Rank and IV Percentile model — computed from IBKR historical IV data.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from helm.db import get_conn


@dataclass
class IVHistory:
    ticker:        str
    date:          str
    iv_current:    Optional[float] = None
    iv_rank:       Optional[float] = None
    iv_percentile: Optional[float] = None
    iv_52wk_high:  Optional[float] = None
    iv_52wk_low:   Optional[float] = None
    days_history:  Optional[int]   = None
    updated_at:    Optional[str]   = None

    # ── Computation ───────────────────────────────────────────────────────────

    @staticmethod
    def compute(iv_series) -> dict:
        """
        Compute IV rank and percentile from a pandas Series of historical IV values.
        IV values should already be in percentage form (e.g. 35.2 not 0.352).

        IV Rank       = (current - 52wk low) / (52wk high - 52wk low) * 100
        IV Percentile = % of days where IV was below today's IV
        """
        import numpy as np
        current = float(iv_series.iloc[-1])
        iv_min  = float(iv_series.min())
        iv_max  = float(iv_series.max())

        if iv_max == iv_min:
            iv_rank = 50.0
            iv_pct  = 50.0
        else:
            iv_rank = round((current - iv_min) / (iv_max - iv_min) * 100, 1)
            iv_rank = max(0.0, min(100.0, iv_rank))
            iv_pct  = round(float((iv_series < current).sum()) / len(iv_series) * 100, 1)

        return {
            'iv_current':    round(current, 2),
            'iv_52wk_low':   round(iv_min, 2),
            'iv_52wk_high':  round(iv_max, 2),
            'iv_rank':       iv_rank,
            'iv_percentile': iv_pct,
            'days_history':  len(iv_series),
        }

    # ── DB read ───────────────────────────────────────────────────────────────

    @classmethod
    def latest(cls, ticker: str) -> Optional[IVHistory]:
        """Get the most recent IVR record for a ticker."""
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM iv_history WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            (ticker.upper(),)
        ).fetchone()
        return cls(**dict(row)) if row else None

    @classmethod
    def for_tickers(cls, tickers: list[str]) -> dict[str, IVHistory]:
        """Get latest IVR for multiple tickers. Returns {ticker: IVHistory}."""
        if not tickers:
            return {}
        conn = get_conn()
        placeholders = ','.join('?' * len(tickers))
        rows = conn.execute(f"""
            SELECT i.* FROM iv_history i
            INNER JOIN (
                SELECT ticker, MAX(date) as max_date
                FROM iv_history
                WHERE ticker IN ({placeholders})
                GROUP BY ticker
            ) latest ON i.ticker = latest.ticker AND i.date = latest.max_date
        """, [t.upper() for t in tickers]).fetchall()
        return {row['ticker']: cls(**dict(row)) for row in rows}

    @classmethod
    def all_latest(cls) -> dict[str, IVHistory]:
        """Get latest IVR for all tickers in the table."""
        conn = get_conn()
        rows = conn.execute("""
            SELECT i.* FROM iv_history i
            INNER JOIN (
                SELECT ticker, MAX(date) as max_date
                FROM iv_history GROUP BY ticker
            ) latest ON i.ticker = latest.ticker AND i.date = latest.max_date
            ORDER BY i.ticker
        """).fetchall()
        return {row['ticker']: cls(**dict(row)) for row in rows}

    @classmethod
    def staleness_days(cls, ticker: str) -> Optional[int]:
        """How many calendar days since last update. None if never updated."""
        conn = get_conn()
        row = conn.execute(
            "SELECT date FROM iv_history WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            (ticker.upper(),)
        ).fetchone()
        if not row:
            return None
        last = datetime.strptime(row['date'], '%Y-%m-%d').date()
        return (date.today() - last).days

    # ── DB write ──────────────────────────────────────────────────────────────

    @classmethod
    def upsert(cls, ticker: str, computed: dict, as_of_date: str = None) -> None:
        """Insert or replace an IVR record."""
        conn = get_conn()
        d = as_of_date or date.today().isoformat()
        conn.execute("""
            INSERT OR REPLACE INTO iv_history
                (ticker, date, iv_current, iv_rank, iv_percentile,
                 iv_52wk_high, iv_52wk_low, days_history, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            ticker.upper(), d,
            computed.get('iv_current'),
            computed.get('iv_rank'),
            computed.get('iv_percentile'),
            computed.get('iv_52wk_high'),
            computed.get('iv_52wk_low'),
            computed.get('days_history'),
        ))
        conn.commit()

    # ── Display helpers ───────────────────────────────────────────────────────

    @property
    def rank_label(self) -> str:
        """Human-readable IV rank label with color."""
        if self.iv_rank is None:
            return '[dim]--[/dim]'
        if self.iv_rank >= 70:
            return f'[red]{self.iv_rank:.0f}[/red]'
        if self.iv_rank >= 40:
            return f'[yellow]{self.iv_rank:.0f}[/yellow]'
        return f'[green]{self.iv_rank:.0f}[/green]'

    @property
    def percentile_label(self) -> str:
        if self.iv_percentile is None:
            return '[dim]--[/dim]'
        if self.iv_percentile >= 70:
            return f'[red]{self.iv_percentile:.0f}[/red]'
        if self.iv_percentile >= 40:
            return f'[yellow]{self.iv_percentile:.0f}[/yellow]'
        return f'[green]{self.iv_percentile:.0f}[/green]'

    @property
    def stale(self) -> bool:
        """True if data is more than 2 days old (weekend-aware approx)."""
        days = self.staleness_days(self.ticker)
        return days is None or days > 3
