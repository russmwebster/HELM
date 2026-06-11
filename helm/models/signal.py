# helm/models/signal.py
# Signal model — per-ticker strategy recommendations
# Never deleted. The permanent intelligence record of HELM.

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import json, uuid

from helm.db import get_conn, transaction, row_to_dict

BIAS_VALUES = ['BEARISH','MILDLY_BEARISH','NEUTRAL','MILDLY_BULLISH','BULLISH']
IV_REGIMES  = ['HIGH','MODERATE','LOW']
FIT_LEVELS  = ['STRONG','GOOD','MODERATE','WEAK']
OUTCOMES    = ['WIN','LOSS','BREAKEVEN','EXPIRED','ASSIGNED']

@dataclass
class Signal:
    id:                  str
    ticker:              str
    generated_at:        str
    confirmed_bias:      str
    recommendations:     str              # JSON array

    # Volatility
    iv_current:          Optional[float] = None
    iv_rank:             Optional[float] = None
    iv_percentile:       Optional[float] = None
    iv_regime:           Optional[str]   = None

    # Technicals
    spot_price:          Optional[float] = None
    ema_20:              Optional[float] = None
    sma_50:              Optional[float] = None
    sma_200:             Optional[float] = None
    rsi_14:              Optional[float] = None
    macd_line:           Optional[float] = None
    macd_signal:         Optional[float] = None
    macd_histogram:      Optional[float] = None
    atr_14:              Optional[float] = None
    bb_width:            Optional[float] = None
    bb_upper:            Optional[float] = None
    bb_lower:            Optional[float] = None
    bb_squeeze:          Optional[int]   = None

    # Derived conditions
    price_vs_ema20:      Optional[str]   = None
    price_vs_sma50:      Optional[str]   = None
    price_vs_sma200:     Optional[str]   = None
    rsi_condition:       Optional[str]   = None
    macd_condition:      Optional[str]   = None
    trend_strength:      Optional[str]   = None

    # Auto bias
    auto_bias_score:     Optional[float] = None
    auto_bias:           Optional[str]   = None
    auto_bias_reasoning: Optional[str]   = None

    # User override
    user_bias_override:  Optional[str]   = None

    # Top recommendation (denormalized)
    top_strategy:        Optional[str]   = None
    top_fit:             Optional[str]   = None

    # ATR sizing
    atr_1x_price:        Optional[float] = None
    atr_2x_price:        Optional[float] = None
    suggested_contracts: Optional[int]   = None

    # Earnings
    earnings_date:       Optional[str]   = None
    days_to_earnings:    Optional[int]   = None
    earnings_warning:    int             = 0

    # Watchlist context
    willing_to_own:      Optional[int]   = None
    is_optionable:       Optional[int]   = None

    # Outcome
    position_opened:     int             = 0
    position_id:         Optional[str]   = None
    outcome_pnl:         Optional[float] = None
    outcome_result:      Optional[str]   = None
    outcome_notes:       Optional[str]   = None

    # Provenance
    data_source:         str             = 'yfinance'
    data_quality:        str             = 'GOOD'
    created_at:          str             = field(default_factory=lambda: datetime.now().isoformat())

    # -- Decision capture (policy v0) --
    russ_intent:         Optional[str] = None
    russ_intent_at:      Optional[str] = None
    russ_action:         Optional[str] = 'PENDING'
    russ_action_at:      Optional[str] = None
    spec_match:          Optional[str] = None
    spec_delta:          Optional[str] = None
    helm_policy_version: Optional[str] = None

    # ── Factories ────────────────────────────────────────────────────────────

    @classmethod
    def create(cls, ticker: str, confirmed_bias: str,
               recommendations: list, **kwargs) -> Signal:
        sig = cls(
            id=kwargs.pop('id', 'SIG-' + uuid.uuid4().hex[:8].upper()),
            ticker=ticker.upper(),
            generated_at=kwargs.pop('generated_at', datetime.now().isoformat()),
            confirmed_bias=confirmed_bias,
            recommendations=json.dumps(recommendations),
            **kwargs
        )
        # Denormalize top recommendation
        if recommendations:
            top = recommendations[0]
            sig.top_strategy = top.get('strategy')
            sig.top_fit = top.get('fit')
        sig.save()
        return sig

    @classmethod
    def from_row(cls, row) -> Signal:
        return cls(**dict(row))

    # ── Queries ──────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, signal_id: str) -> Optional[Signal]:
        conn = get_conn()
        try:
            row = conn.execute(
                'SELECT * FROM signals WHERE id = ?', (signal_id,)
            ).fetchone()
            return cls.from_row(row) if row else None
        finally:
            conn.close()

    @classmethod
    def for_ticker(cls, ticker: str, limit: int = 50) -> list[Signal]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM signals WHERE ticker = ? ORDER BY generated_at DESC LIMIT ?',
                (ticker.upper(), limit)
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def latest_for_ticker(cls, ticker: str) -> Optional[Signal]:
        conn = get_conn()
        try:
            row = conn.execute(
                'SELECT * FROM signals WHERE ticker = ? ORDER BY generated_at DESC LIMIT 1',
                (ticker.upper(),)
            ).fetchone()
            return cls.from_row(row) if row else None
        finally:
            conn.close()

    @classmethod
    def recent(cls, limit: int = 20) -> list[Signal]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM signals ORDER BY generated_at DESC LIMIT ?',
                (limit,)
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def by_regime(cls, regime: str, limit: int = 100) -> list[Signal]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM signals WHERE iv_regime = ? ORDER BY generated_at DESC LIMIT ?',
                (regime, limit)
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def with_outcomes(cls) -> list[Signal]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM signals WHERE outcome_result IS NOT NULL ORDER BY generated_at DESC'
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    # ── Recommendations ──────────────────────────────────────────────────────

    def get_recommendations(self) -> list[dict]:
        return json.loads(self.recommendations) if self.recommendations else []

    def top_n(self, n: int = 3) -> list[dict]:
        return self.get_recommendations()[:n]

    # ── Outcome recording ────────────────────────────────────────────────────

    def record_position_opened(self, position_id: str) -> Signal:
        self.position_opened = 1
        self.position_id = position_id
        with transaction() as conn:
            conn.execute(
                'UPDATE signals SET position_opened = 1, position_id = ? WHERE id = ?',
                (position_id, self.id)
            )
        return self

    def record_outcome(self, pnl: float, result: str, notes: Optional[str] = None) -> Signal:
        if result not in OUTCOMES:
            raise ValueError(f'Invalid outcome: {result}')
        self.outcome_pnl = pnl
        self.outcome_result = result
        self.outcome_notes = notes
        with transaction() as conn:
            conn.execute(
                'UPDATE signals SET outcome_pnl = ?, outcome_result = ?, outcome_notes = ? WHERE id = ?',
                (pnl, result, notes, self.id)
            )
        return self

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self) -> Signal:
        # Column list derived from the dataclass so it cannot drift from the table.
        from dataclasses import fields as _fields
        cols = [f.name for f in _fields(self)]
        placeholders = ','.join('?' for _ in cols)
        values = tuple(getattr(self, c) for c in cols)
        with transaction() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO signals (' + ', '.join(cols) + ') VALUES (' + placeholders + ')',
                values,
            )
        return self

    def __str__(self) -> str:
        top = self.top_strategy or 'no recommendation'
        return f'[{self.ticker}] {self.confirmed_bias} | {top} ({self.top_fit}) | IV rank {self.iv_rank} @ {self.generated_at[:10]}'
