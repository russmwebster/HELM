# helm/models/pathway.py
# ImportPathway model — configured sources for portfolio imports

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import glob, uuid

from helm.db import get_conn, transaction

BROKERS = ['fidelity', 'ibkr', 'tastytrade']

@dataclass
class ImportPathway:
    id:                    str
    account_id:            str
    broker:                str
    watch_folder:          str
    file_pattern:          str
    broker_account:        Optional[str] = None
    import_both_accounts:  int = 1
    last_imported_at:      Optional[str] = None
    last_file:             Optional[str] = None
    is_active:             int = 1
    created_at:            str = field(default_factory=lambda: datetime.now().isoformat())
    notes:                 Optional[str] = None

    def __post_init__(self):
        if self.broker not in BROKERS:
            raise ValueError(f'Unknown broker: {self.broker}. Must be one of {BROKERS}')

    @classmethod
    def create(cls, account_id: str, broker: str,
               watch_folder: str, file_pattern: str, **kwargs) -> ImportPathway:
        p = cls(
            id=kwargs.pop('id', 'PTH-' + uuid.uuid4().hex[:8].upper()),
            account_id=account_id,
            broker=broker,
            watch_folder=str(Path(watch_folder).expanduser()),
            file_pattern=file_pattern,
            **kwargs
        )
        p.save()
        return p

    @classmethod
    def from_row(cls, row) -> ImportPathway:
        return cls(**dict(row))

    @classmethod
    def for_account(cls, account_id: str) -> list[ImportPathway]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM import_pathways WHERE account_id = ? AND is_active = 1 ORDER BY created_at',
                (account_id,)
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def all_active(cls) -> list[ImportPathway]:
        conn = get_conn()
        try:
            rows = conn.execute(
                'SELECT * FROM import_pathways WHERE is_active = 1 ORDER BY broker, created_at'
            ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def for_broker(cls, broker: str, account_id: Optional[str] = None) -> list[ImportPathway]:
        conn = get_conn()
        try:
            if account_id:
                rows = conn.execute(
                    'SELECT * FROM import_pathways WHERE broker = ? AND account_id = ? AND is_active = 1',
                    (broker, account_id)
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM import_pathways WHERE broker = ? AND is_active = 1',
                    (broker,)
                ).fetchall()
            return [cls.from_row(r) for r in rows]
        finally:
            conn.close()

    def resolve_folder(self) -> Path:
        return Path(self.watch_folder).expanduser()

    def find_latest_file(self) -> Optional[Path]:
        folder = self.resolve_folder()
        if not folder.exists():
            return None
        import glob as glob_module
        pattern = str(folder / self.file_pattern)
        matches = sorted(
            [Path(p) for p in glob_module.glob(pattern)],
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        return matches[0] if matches else None

    def find_all_files(self) -> list[Path]:
        folder = self.resolve_folder()
        if not folder.exists():
            return []
        import glob as glob_module
        pattern = str(folder / self.file_pattern)
        return sorted(
            [Path(p) for p in glob_module.glob(pattern)],
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

    def record_import(self, filename: str) -> ImportPathway:
        self.last_imported_at = datetime.now().isoformat()
        self.last_file = filename
        with transaction() as conn:
            conn.execute(
                'UPDATE import_pathways SET last_imported_at = ?, last_file = ? WHERE id = ?',
                (self.last_imported_at, self.last_file, self.id)
            )
        return self

    def deactivate(self) -> ImportPathway:
        self.is_active = 0
        with transaction() as conn:
            conn.execute(
                'UPDATE import_pathways SET is_active = 0 WHERE id = ?', (self.id,)
            )
        return self

    def save(self) -> ImportPathway:
        with transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO import_pathways (
                    id, account_id, broker, broker_account, watch_folder,
                    file_pattern, import_both_accounts, last_imported_at,
                    last_file, is_active, created_at, notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                self.id, self.account_id, self.broker, self.broker_account,
                self.watch_folder, self.file_pattern, self.import_both_accounts,
                self.last_imported_at, self.last_file, self.is_active,
                self.created_at, self.notes
            ))
        return self

    def __str__(self) -> str:
        latest = self.find_latest_file()
        latest_str = latest.name if latest else 'no files found'
        return f'[{self.broker}] {self.watch_folder}/{self.file_pattern} — latest: {latest_str}'
