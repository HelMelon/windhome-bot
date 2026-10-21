"""SQLite-хранилище: что уже видели и мелкий key-value (курсы, служебное).

Уникальность (source, uid) — единственная защита от повторных уведомлений,
поэтому INSERT OR IGNORE + проверка множеством идут в одной транзакции.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    source TEXT NOT NULL,
    uid TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    price_byn INTEGER,
    title TEXT,
    url TEXT,
    PRIMARY KEY (source, uid)
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def known_uids(self, source: str) -> set[str]:
        rows = self._db.execute("SELECT uid FROM seen WHERE source = ?", (source,))
        return {row[0] for row in rows}

    def mark_seen(
        self,
        source: str,
        uid: str,
        price_byn: int | None,
        title: str,
        url: str,
    ) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO seen (source, uid, first_seen, price_byn, title, url)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (source, uid, int(time.time()), price_byn, title, url),
        )
        self._db.commit()

    def count(self, source: str | None = None) -> int:
        if source is None:
            row = self._db.execute("SELECT COUNT(*) FROM seen").fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*) FROM seen WHERE source = ?", (source,)
            ).fetchone()
        return int(row[0])

    def get(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()
