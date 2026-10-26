"""Persistent TTL cache backed by SQLite.

Stores compressed page HTML; entries older than `ttl` seconds are ignored and
pruned. WAL mode keeps concurrent readers (multi-worker) happy.
"""
import sqlite3
import threading
import time
import zlib
from typing import Optional


class SqliteCache:
    def __init__(self, path: str = "scholar2api.db", ttl: float = 86400.0):
        self.path, self.ttl = path, ttl
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS pages ("
                " key TEXT PRIMARY KEY, value BLOB NOT NULL, fetched_at REAL NOT NULL)")
            self._conn.commit()
        self.prune()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, fetched_at FROM pages WHERE key = ?", (key,)).fetchone()
        if not row or time.time() - row[1] > self.ttl:
            return None
        return zlib.decompress(row[0]).decode()

    def set(self, key: str, html: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO pages VALUES (?, ?, ?)",
                (key, zlib.compress(html.encode()), time.time()))
            self._conn.commit()

    def prune(self) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM pages WHERE fetched_at < ?", (time.time() - self.ttl,))
            self._conn.commit()

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            oldest = self._conn.execute("SELECT MIN(fetched_at) FROM pages").fetchone()[0]
        return {"entries": total, "ttl_seconds": int(self.ttl),
                "oldest_age_seconds": int(time.time() - oldest) if oldest else None,
                "db_path": self.path}

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM pages")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
