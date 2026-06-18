# helm/models/lifecycle.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

from helm.db import get_conn, transaction, row_to_dict
from contextlib import nullcontext

EVENT_TYPES = ['PENDING','OPENED','ROLLED','CLOSED','ASSIGNED','EXPIRED','ADJUSTED','NOTE','CHECK_ALERT']

@dataclass
class LifecycleEvent:
    id:              str
    position_id:     str
    event_type:      str
    occurred_at:     str
    leg_id:          Optional[str] = None
    spot_price:      Optional[float] = None
    option_price:    Optional[float] = None
    contracts:       Optional[int] = None
    pnl_at_event:    Optional[float] = None
    cumulative_pnl:  Optional[float] = None
    new_position_id: Optional[str] = None
    narrative:       Optional[str] = None
    created_at:      str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f'Invalid event_type: {self.event_type}')

    @classmethod
    def record(cls, position_id: str, event_type: str, **kwargs) -> LifecycleEvent:
        conn = kwargs.pop('conn', None)
        event = cls(
            id=kwargs.pop('id', 'EVT-' + uuid.uuid4().hex[:8].upper()),
            position_id=position_id,
            event_type=event_type,
            occurred_at=kwargs.pop('occurred_at', datetime.now().isoformat()),
            **kwargs
        )
        event.save(conn=conn)
        return event

    @classmethod
    def from_row(cls, row) -> LifecycleEvent:
        return cls(**dict(row))

    @classmethod
    def for_position(cls, position_id: str) -> list[LifecycleEvent]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM lifecycle_events WHERE position_id = ? ORDER BY occurred_at',
                (position_id,)
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def roll_chain(cls, position_id: str) -> list[str]:
        # Walk the roll chain from any position and return ordered list of position IDs
        conn = get_conn()
        try:
            chain = [position_id]
            current = position_id
            while True:
                row = conn.execute(
                    'SELECT new_position_id FROM lifecycle_events WHERE position_id = ? AND event_type = "ROLLED" LIMIT 1',
                    (current,)
                ).fetchone()
                if not row or not row['new_position_id']:
                    break
                current = row['new_position_id']
                chain.append(current)
            return chain
        finally:
            conn.close()

    def save(self, conn=None) -> LifecycleEvent:
        with (transaction() if conn is None else nullcontext(conn)) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO lifecycle_events (
                    id, position_id, leg_id, event_type, occurred_at,
                    spot_price, option_price, contracts, pnl_at_event,
                    cumulative_pnl, new_position_id, narrative, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                self.id, self.position_id, self.leg_id, self.event_type,
                self.occurred_at, self.spot_price, self.option_price,
                self.contracts, self.pnl_at_event, self.cumulative_pnl,
                self.new_position_id, self.narrative, self.created_at
            ))
        return self

    def __str__(self) -> str:
        return f'[{self.event_type}] {self.occurred_at[:10]} — {self.narrative or ""}'
