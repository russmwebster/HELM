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
    price_vs_52wk_pct:   Optional[float]  = None
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
    # HELM-090 p1 (display-only): realized vol + VRP context at scan time
    hv_30:        Optional[float] = None   # 30d realized vol (%), from scan closes
    vrp:          Optional[float] = None   # iv_current - hv_30 (vol pts)
    vrp_ratio:    Optional[float] = None   # iv_current / hv_30
    vol_bucket:   Optional[str] = None     # rich | moderate | cheap
    # HELM-103 (s82): momentum_bias() regime inputs, persisted for back-test
    adx:          Optional[float]   = None   # ADX(14) trend strength
    plus_di:      Optional[float]   = None   # +DI(14)
    minus_di:     Optional[float]   = None   # -DI(14)
    obv_trend:    Optional[int]     = None   # +1 rising / -1 falling / 0 flat
    # s90: the ex-earnings twin for the 30-day window. Same shape and same
    # source vocabulary as hv_90_ex_earn / hv_90_source (dates | dates-none
    # | plain), so a caller can treat the two windows identically.
    hv_30_ex_earn:    Optional[float] = None  # 30d RV, earnings moves removed
    hv_30_source:     Optional[str]   = None  # dates | dates-none | plain
    # HELM-101 (s82): buy-side vol-gate inputs + long-call screen verdict
    hv_90:            Optional[float] = None   # plain 90d realized vol (%)
    hv_90_ex_earn:    Optional[float] = None   # 90d RV, earnings moves removed
    hv_90_source:     Optional[str]   = None   # dates | dates-none | plain
    hv_252:           Optional[float] = None   # 1y realized vol (%), G5 ceiling
    iv_hv90_ratio:    Optional[float] = None   # iv_current / hv_90_ex_earn (G3)
    lc_screen_pass:   Optional[int]   = None   # 1 pass / 0 reject / None not run
    lc_screen_rank:   Optional[int]   = None
    lc_screen_reject: Optional[str]   = None   # first gate failed (G1..G5)
    lc_rank_score:    Optional[float] = None
    lc_gates_json:    Optional[str]   = None   # per-gate values, audit trail
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

    @classmethod
    def link_position_opened(cls, ticker: str, strategy: str,
                             position_id: str) -> Optional[Signal]:
        """Stamp the originating scan signal when a real position is opened.

        Resolves the latest *unlinked* signal for `ticker` (the most recent
        scan's judgment) and links it ONLY when its top_strategy equals the
        strategy actually opened -- so a deliberate exception (a structure HELM
        did not flag, or a backfill) stays unlinked rather than inheriting an
        unrelated judgment. On a match, wires BOTH sides in one transaction: the
        signal (position_opened / position_id / russ_action='OPEN') and the
        position (signal_id, which close_cmd reads for outcome back-prop).
        Returns the linked Signal, or None when nothing matches.
        """
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT id, top_strategy FROM signals "
                "WHERE ticker = ? AND position_id IS NULL "
                "ORDER BY generated_at DESC LIMIT 1",
                (ticker.upper(),)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        sig_id, sig_strategy = row[0], row[1]
        if sig_strategy is None or sig_strategy != strategy:
            return None
        now = datetime.now().isoformat()
        with transaction() as conn:
            conn.execute(
                "UPDATE signals SET position_opened = 1, position_id = ?, "
                "russ_action = 'OPEN', russ_action_at = ? WHERE id = ?",
                (position_id, now, sig_id)
            )
            conn.execute(
                "UPDATE positions SET signal_id = ? WHERE id = ?",
                (sig_id, position_id)
            )
        return cls.get(sig_id)

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
