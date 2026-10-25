# helm/models/leg.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

from helm.db import get_conn, transaction, row_to_dict
from contextlib import nullcontext

LEG_ROLES = ['SHORT_PUT','LONG_PUT','SHORT_CALL','LONG_CALL','LONG_STOCK','SHORT_STOCK','LONG_LEAPS']
DIRECTIONS = ['LONG','SHORT']
OPTION_TYPES = ['PUT','CALL','STOCK']
STATUSES = ['OPEN','CLOSED','EXPIRED','ASSIGNED','EXERCISED']

@dataclass
class Leg:
    id:           str
    position_id:  str
    leg_role:     str
    direction:    str
    open_price:   float
    open_date:    str
    contracts:    int = 1
    multiplier:   int = 100
    option_type:  Optional[str] = None
    strike:       Optional[float] = None
    expiration:   Optional[str] = None
    close_price:  Optional[float] = None
    close_date:   Optional[str] = None
    status:       str = 'OPEN'
    entry_delta:  Optional[float] = None
    notes:        Optional[str] = None
    created_at:   str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        if self.leg_role not in LEG_ROLES:
            raise ValueError(f'Invalid leg_role: {self.leg_role}')
        if self.direction not in DIRECTIONS:
            raise ValueError(f'Invalid direction: {self.direction}')
        if self.status not in STATUSES:
            raise ValueError(f'Invalid status: {self.status}')

    @classmethod
    def new_id(cls, position_id: str, role: str) -> str:
        return f'{position_id}-{role[:2]}-{uuid.uuid4().hex[:4].upper()}'

    @classmethod
    def create(cls, position_id: str, leg_role: str, direction: str,
               open_price: float, open_date: str, **kwargs) -> Leg:
        conn = kwargs.pop('conn', None)
        leg = cls(
            id=kwargs.pop('id', cls.new_id(position_id, leg_role)),
            position_id=position_id,
            leg_role=leg_role,
            direction=direction,
            open_price=open_price,
            open_date=open_date,
            **kwargs
        )
        leg.save(conn=conn)
        return leg

    @classmethod
    def from_row(cls, row) -> Leg:
        return cls(**dict(row))

    @classmethod
    def for_position(cls, position_id: str) -> list[Leg]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM legs WHERE position_id = ? ORDER BY created_at',
                (position_id,)
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def get(cls, leg_id: str) -> Optional[Leg]:
        conn = get_conn()
        try:
            row = conn.execute('SELECT * FROM legs WHERE id = ?', (leg_id,)).fetchone()
            return cls.from_row(row) if row else None
        finally:
            conn.close()

    # s135 (2026-10-25): CRITICAL -- legs use INSERT OR IGNORE + UPDATE, never
    # INSERT OR REPLACE. REPLACE deletes the row first, and leg_checks references
    # legs(id) ON DELETE CASCADE, so every leg close (helm close, close --leg,
    # settle, the paper agent, the s135 freeze) silently deleted that leg's whole
    # reading history -- 5,612 rows in the freeze alone. Position.save carries the
    # same warning for the same reason. Do not collapse this to REPLACE.
    _COLS = ("id", "position_id", "leg_role", "option_type", "direction",
             "strike", "expiration", "contracts", "multiplier",
             "open_price", "close_price", "open_date", "close_date",
             "status", "notes", "created_at", "entry_delta")
    _IMMUTABLE = ("id", "created_at")

    def save(self, conn=None) -> Leg:
        vals = {c: getattr(self, c) for c in self._COLS}
        upd = [c for c in self._COLS if c not in self._IMMUTABLE]
        with (transaction() if conn is None else nullcontext(conn)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO legs (%s) VALUES (%s)"
                % (", ".join(self._COLS), ",".join("?" * len(self._COLS))),
                tuple(vals[c] for c in self._COLS))
            conn.execute(
                "UPDATE legs SET %s WHERE id=?" % ", ".join("%s=?" % c for c in upd),
                tuple(vals[c] for c in upd) + (self.id,))
        return self

    def close(self, close_price: float, close_date: Optional[str] = None) -> Leg:
        self.close_price = close_price
        self.close_date = close_date or datetime.now().isoformat()
        self.status = 'CLOSED'
        return self.save()

    @property
    def open_value(self) -> float:
        return self.open_price * self.contracts * self.multiplier

    @property
    def close_value(self) -> Optional[float]:
        if self.close_price is None:
            return None
        return self.close_price * self.contracts * self.multiplier

    @property
    def pnl(self) -> Optional[float]:
        if self.close_value is None:
            return None
        if self.direction == 'SHORT':
            return self.open_value - self.close_value
        return self.close_value - self.open_value

    def __str__(self) -> str:
        strike_str = f' @{self.strike}' if self.strike else ''
        exp_str = f' exp {self.expiration}' if self.expiration else ''
        return f'{self.leg_role}{strike_str}{exp_str} x{self.contracts} @ {self.open_price}'
