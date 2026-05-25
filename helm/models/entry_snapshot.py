# helm/models/entry_snapshot.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import json, uuid

from helm.db import get_conn, transaction, row_to_dict

@dataclass
class EntrySnapshot:
    id:                  str
    position_id:         str
    snapshot_at:         str
    spot_price:          float
    leg_id:              Optional[str] = None
    spot_52wk_high:      Optional[float] = None
    spot_52wk_low:       Optional[float] = None
    iv_current:          Optional[float] = None
    iv_rank:             Optional[float] = None
    iv_percentile:       Optional[float] = None
    iv_52wk_high:        Optional[float] = None
    iv_52wk_low:         Optional[float] = None
    hv_30d:              Optional[float] = None
    delta:               Optional[float] = None
    gamma:               Optional[float] = None
    theta:               Optional[float] = None
    vega:                Optional[float] = None
    dte:                 Optional[int] = None
    days_to_earnings:    Optional[int] = None
    premium_collected:   Optional[float] = None
    theta_per_day:       Optional[float] = None
    skew_put_iv:         Optional[float] = None
    skew_call_iv:        Optional[float] = None
    skew_value:          Optional[float] = None
    leaps_delta:         Optional[float] = None
    leaps_dte:           Optional[int] = None
    extrinsic_ratio:     Optional[float] = None
    settings_snapshot:   Optional[str] = None
    created_at:          str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def create(cls, position_id: str, spot_price: float, **kwargs) -> EntrySnapshot:
        snap = cls(
            id=kwargs.pop('id', 'ES-' + uuid.uuid4().hex[:8].upper()),
            position_id=position_id,
            snapshot_at=kwargs.pop('snapshot_at', datetime.now().isoformat()),
            spot_price=spot_price,
            **kwargs
        )
        snap.save()
        return snap

    @classmethod
    def from_row(cls, row) -> EntrySnapshot:
        return cls(**dict(row))

    @classmethod
    def for_position(cls, position_id: str) -> Optional[EntrySnapshot]:
        conn = get_conn()
        try:
            row = conn.execute(
                'SELECT * FROM entry_snapshots WHERE position_id = ?', (position_id,)
            ).fetchone()
            return cls.from_row(row) if row else None
        finally:
            conn.close()

    def set_settings(self, settings: dict) -> EntrySnapshot:
        self.settings_snapshot = json.dumps(settings)
        return self

    def get_settings(self) -> Optional[dict]:
        return json.loads(self.settings_snapshot) if self.settings_snapshot else None

    def save(self) -> EntrySnapshot:
        with transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO entry_snapshots (
                    id, position_id, leg_id, snapshot_at, spot_price,
                    spot_52wk_high, spot_52wk_low, iv_current, iv_rank,
                    iv_percentile, iv_52wk_high, iv_52wk_low, hv_30d,
                    delta, gamma, theta, vega, dte, days_to_earnings,
                    premium_collected, theta_per_day, skew_put_iv,
                    skew_call_iv, skew_value, leaps_delta, leaps_dte,
                    extrinsic_ratio, settings_snapshot, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                self.id, self.position_id, self.leg_id, self.snapshot_at,
                self.spot_price, self.spot_52wk_high, self.spot_52wk_low,
                self.iv_current, self.iv_rank, self.iv_percentile,
                self.iv_52wk_high, self.iv_52wk_low, self.hv_30d,
                self.delta, self.gamma, self.theta, self.vega,
                self.dte, self.days_to_earnings, self.premium_collected,
                self.theta_per_day, self.skew_put_iv, self.skew_call_iv,
                self.skew_value, self.leaps_delta, self.leaps_dte,
                self.extrinsic_ratio, self.settings_snapshot, self.created_at
            ))
        return self
