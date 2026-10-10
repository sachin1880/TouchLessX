"""Local action history (SQLite) behind the insights page.

One row per performed action: time, gesture, action, confidence. Nothing else - no
frames, no landmarks, no window titles. The user can switch it off, set how long rows
are kept, and clear it; it never leaves this computer.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path


class History:
    def __init__(self, path: Path | None = None, enabled: bool = True) -> None:
        self.enabled = enabled
        self._db: sqlite3.Connection | None = None
        try:
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path) if path is not None else ":memory:")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS actions ("
                "ts REAL NOT NULL, gesture TEXT NOT NULL, action TEXT NOT NULL, confidence REAL NOT NULL)"
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS actions_ts ON actions (ts)")
            self._db.commit()
        except (sqlite3.Error, OSError):
            self._db = None  # history is optional - the app works without it

    def record(self, gesture: str, action: str, confidence: float, ts: float | None = None) -> None:
        if not self.enabled or self._db is None:
            return
        try:
            self._db.execute("INSERT INTO actions VALUES (?, ?, ?, ?)",
                             (time.time() if ts is None else ts, gesture, action, float(confidence)))
            self._db.commit()
        except sqlite3.Error:
            pass

    def _query(self, sql: str, args: tuple = ()) -> list[tuple]:
        if self._db is None:
            return []
        try:
            return self._db.execute(sql, args).fetchall()
        except sqlite3.Error:
            return []

    def total(self, since: float = 0.0) -> int:
        rows = self._query("SELECT COUNT(*) FROM actions WHERE ts >= ?", (since,))
        return int(rows[0][0]) if rows else 0

    def top(self, column: str, since: float = 0.0, limit: int = 6) -> list[tuple[str, int]]:
        """Most frequent gestures or actions since a timestamp."""
        if column not in ("gesture", "action"):
            raise ValueError(column)
        return self._query(
            f"SELECT {column}, COUNT(*) AS n FROM actions WHERE ts >= ? GROUP BY {column} "
            "ORDER BY n DESC, 1 LIMIT ?", (since, limit))

    def daily(self, days: int = 7, today: date | None = None) -> list[tuple[str, int]]:
        """(weekday label, action count) for each of the last `days` days, oldest first."""
        today = today or date.today()
        start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time())
        counts = dict(self._query(
            "SELECT date(ts, 'unixepoch', 'localtime'), COUNT(*) FROM actions WHERE ts >= ? GROUP BY 1",
            (start.timestamp(),)))
        result = []
        for offset in range(days):
            day = start.date() + timedelta(days=offset)
            result.append((day.strftime("%a"), int(counts.get(day.isoformat(), 0))))
        return result

    def low_confidence_share(self, since: float = 0.0, below: float = 75.0) -> float:
        """Percentage of recorded actions that were taken on a shaky detection."""
        rows = self._query(
            "SELECT COUNT(*), SUM(confidence < ?) FROM actions WHERE ts >= ?", (below, since))
        if not rows or not rows[0][0]:
            return 0.0
        return 100.0 * (rows[0][1] or 0) / rows[0][0]

    def prune(self, keep_days: int) -> None:
        if self._db is None or keep_days <= 0:
            return
        try:
            self._db.execute("DELETE FROM actions WHERE ts < ?", (time.time() - keep_days * 86400,))
            self._db.commit()
        except sqlite3.Error:
            pass

    def clear(self) -> None:
        if self._db is None:
            return
        try:
            self._db.execute("DELETE FROM actions")
            self._db.commit()
        except sqlite3.Error:
            pass

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
