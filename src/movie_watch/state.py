from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    """SQLite-backed source health and run history.

    Show fingerprints are deliberately not stored: each scan reports every show
    it finds. The database only tracks per-source success/failure streaks and a
    short run log.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_state (
                    target TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    last_success_at TEXT,
                    last_status TEXT NOT NULL DEFAULT 'unknown',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (target, platform)
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alert_state (
                    key TEXT PRIMARY KEY,
                    last_sent_at TEXT NOT NULL
                );
                """
            )

    # ------------------------------------------------------------------
    # source state
    # ------------------------------------------------------------------
    def get_source_state(self, target: str, platform: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM source_state WHERE target = ? AND platform = ?",
                (target, platform),
            ).fetchone()

    def mark_success(self, target: str, platform: str) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO source_state (
                    target, platform, last_success_at,
                    last_status, consecutive_failures, last_error
                ) VALUES (?, ?, ?, 'ok', 0, '')
                ON CONFLICT(target, platform) DO UPDATE SET
                    last_success_at = excluded.last_success_at,
                    last_status = 'ok',
                    consecutive_failures = 0,
                    last_error = ''
                """,
                (target, platform, now),
            )

    def mark_failure(self, target: str, platform: str, error: str) -> int:
        """Record a failure and return the new consecutive-failure count."""
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO source_state (
                    target, platform, last_status, consecutive_failures, last_error
                ) VALUES (?, ?, 'error', 1, ?)
                ON CONFLICT(target, platform) DO UPDATE SET
                    last_status = 'error',
                    consecutive_failures = source_state.consecutive_failures + 1,
                    last_error = excluded.last_error
                """,
                (target, platform, error[:1000]),
            )
            row = conn.execute(
                "SELECT consecutive_failures FROM source_state WHERE target = ? AND platform = ?",
                (target, platform),
            ).fetchone()
            return int(row[0]) if row else 1

    # ------------------------------------------------------------------
    # one-shot / throttled alerts
    # ------------------------------------------------------------------
    def should_send_alert(self, key: str, cooldown_hours: float) -> bool:
        """True when no alert with this key was sent inside the cooldown window."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_sent_at FROM alert_state WHERE key = ?", (key,)
            ).fetchone()
        if not row or not row[0]:
            return True
        try:
            last = datetime.fromisoformat(row[0])
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed >= cooldown_hours * 3600

    def mark_alert_sent(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alert_state (key, last_sent_at) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET last_sent_at = excluded.last_sent_at
                """,
                (key, utc_now()),
            )

    # ------------------------------------------------------------------
    # runs
    # ------------------------------------------------------------------
    def record_run(self, started_at: str, status: str, summary: dict) -> None:
        import json

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs(started_at, finished_at, status, summary) VALUES (?, ?, ?, ?)",
                (started_at, utc_now(), status, json.dumps(summary, default=str)),
            )
