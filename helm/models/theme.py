
# helm/models/theme.py
# Investment Theme model

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

from helm.db import get_conn, transaction

CATEGORIES = ['ESTABLISHED', 'EMERGING', 'PRE_IPO', 'WATCH']


@dataclass
class Theme:
    id:          str
    name:        str
    description: Optional[str] = None
    created_at:  str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at:  str = field(default_factory=lambda: datetime.now().isoformat())
    notes:       Optional[str] = None

    @classmethod
    def create(cls, name: str, description: str = None, **kwargs) -> Theme:
        t = cls(
            id=kwargs.pop('id', 'THM-' + uuid.uuid4().hex[:8].upper()),
            name=name,
            description=description,
            **kwargs
        )
        t.save()
        return t

    @classmethod
    def all(cls) -> list[Theme]:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM themes ORDER BY name"
            ).fetchall()
            return [cls(**dict(r)) for r in rows]
        finally:
            conn.close()

    @classmethod
    def get(cls, theme_id: str) -> Optional[Theme]:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM themes WHERE id=? OR name=?",
                (theme_id, theme_id)
            ).fetchone()
            return cls(**dict(row)) if row else None
        finally:
            conn.close()

    def tickers(self, category: str = None) -> list[dict]:
        conn = get_conn()
        try:
            if category:
                rows = conn.execute(
                    "SELECT * FROM theme_tickers WHERE theme_id=? AND category=? ORDER BY category, ticker",
                    (self.id, category)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM theme_tickers WHERE theme_id=? ORDER BY category, ticker",
                    (self.id,)
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def add_ticker(self, ticker: str, category: str = 'ESTABLISHED',
                   company_name: str = None, notes: str = None) -> bool:
        try:
            with transaction() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO theme_tickers
                    (id, theme_id, ticker, category, company_name, notes)
                    VALUES (?,?,?,?,?,?)
                """, (
                    'TTK-' + uuid.uuid4().hex[:8].upper(),
                    self.id, ticker.upper(), category,
                    company_name, notes
                ))
            return True
        except Exception:
            return False

    def remove_ticker(self, ticker: str) -> bool:
        try:
            with transaction() as conn:
                conn.execute(
                    "DELETE FROM theme_tickers WHERE theme_id=? AND ticker=?",
                    (self.id, ticker.upper())
                )
            return True
        except Exception:
            return False

    def save(self) -> Theme:
        self.updated_at = datetime.now().isoformat()
        with transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO themes
                (id, name, description, created_at, updated_at, notes)
                VALUES (?,?,?,?,?,?)
            """, (self.id, self.name, self.description,
                  self.created_at, self.updated_at, self.notes))
        return self


def log_event(event_type: str, entity_id: str = None,
              entity_name: str = None, notes: str = None) -> None:
    """Log a HELM event for the nudge system."""
    try:
        with transaction() as conn:
            conn.execute("""
                INSERT INTO helm_events
                (id, event_type, entity_id, entity_name, occurred_at, notes)
                VALUES (?,?,?,?,?,?)
            """, (
                'EVT-' + uuid.uuid4().hex[:8].upper(),
                event_type, entity_id, entity_name,
                datetime.now().isoformat(), notes
            ))
    except Exception:
        pass


def days_since_event(event_type: str, entity_id: str = None) -> Optional[int]:
    """Return days since the last occurrence of an event, or None if never."""
    try:
        conn = get_conn()
        if entity_id:
            row = conn.execute(
                "SELECT occurred_at FROM helm_events WHERE event_type=? AND entity_id=? ORDER BY occurred_at DESC LIMIT 1",
                (event_type, entity_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT occurred_at FROM helm_events WHERE event_type=? ORDER BY occurred_at DESC LIMIT 1",
                (event_type,)
            ).fetchone()
        conn.close()
        if not row:
            return None
        from datetime import date
        last = datetime.fromisoformat(row[0]).date()
        return (date.today() - last).days
    except Exception:
        return None
