# helm/models/account.py
# Account model

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

from helm.db import get_conn, transaction, row_to_dict

@dataclass
class Account:
    id:              str
    broker:          str
    nickname:        str
    buying_power:    Optional[float] = None
    portfolio_value: Optional[float] = None
    currency:        str = 'USD'
    is_active:       bool = True
    created_at:      str = field(default_factory=lambda: datetime.now().isoformat())
    notes:           Optional[str] = None

    @classmethod
    def create(cls, broker: str, nickname: str, **kwargs) -> Account:
        account = cls(
            id=kwargs.pop('id', broker.lower() + '_' + uuid.uuid4().hex[:8]),
            broker=broker,
            nickname=nickname,
            **kwargs
        )
        account.save()
        return account

    @classmethod
    def from_row(cls, row) -> Account:
        d = dict(row)
        d['is_active'] = bool(d.get('is_active', 1))
        return cls(**d)

    @classmethod
    def get(cls, account_id: str) -> Optional[Account]:
        conn = get_conn()
        try:
            row = conn.execute('SELECT * FROM accounts WHERE id = ?', (account_id,)).fetchone()
            return cls.from_row(row) if row else None
        finally:
            conn.close()

    @classmethod
    def all(cls, active_only: bool = True) -> list[Account]:
        conn = get_conn()
        try:
            sql = 'SELECT * FROM accounts WHERE is_active = 1 ORDER BY nickname' if active_only else 'SELECT * FROM accounts ORDER BY nickname'
            rows = conn.execute(sql).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def first(cls) -> Optional[Account]:
        accounts = cls.all()
        return accounts[0] if accounts else None

    def save(self) -> Account:
        with transaction() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO accounts (id, broker, nickname, buying_power, portfolio_value, currency, is_active, created_at, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (self.id, self.broker, self.nickname, self.buying_power, self.portfolio_value, self.currency, int(self.is_active), self.created_at, self.notes)
            )
        return self

    def update_balances(self, buying_power: float, portfolio_value: float) -> Account:
        self.buying_power = buying_power
        self.portfolio_value = portfolio_value
        with transaction() as conn:
            conn.execute('UPDATE accounts SET buying_power = ?, portfolio_value = ? WHERE id = ?', (buying_power, portfolio_value, self.id))
        return self

    def deactivate(self) -> Account:
        self.is_active = False
        with transaction() as conn:
            conn.execute('UPDATE accounts SET is_active = 0 WHERE id = ?', (self.id,))
        return self

    def __str__(self) -> str:
        bp = f'$' + f'{self.buying_power:,.0f}' if self.buying_power else 'N/A'
        pv = f'$' + f'{self.portfolio_value:,.0f}' if self.portfolio_value else 'N/A'
        return f'[{self.id}] {self.nickname} ({self.broker}) BP:{bp} PV:{pv}'