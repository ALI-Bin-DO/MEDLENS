"""Local SQLite storage for /chat monitoring events (separate from MySQL and Qdrant).

Each call opens its own short-lived connection, so it is safe from FastAPI's worker threads;
writes are additionally serialized with a lock, and the file uses WAL mode so reads do not block
writes. Only metadata is stored (see models/admin_models.ActivityEvent): never question text,
SQL, rows, prompts, credentials or exception messages.

Retention: events older than MONITORING_RETENTION_DAYS are deleted, and only the newest
MONITORING_MAX_EVENTS are kept. Cleanup runs at application startup and after every
PRUNE_EVERY inserts.
"""

import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import asdict, dataclass, fields
from pathlib import Path

PRUNE_EVERY = 100

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id           TEXT    NOT NULL UNIQUE,
    timestamp            TEXT    NOT NULL,
    created_epoch        REAL    NOT NULL,
    route                TEXT,
    route_source         TEXT,
    data_pipeline        INTEGER NOT NULL,
    http_status          INTEGER NOT NULL,
    status               TEXT    NOT NULL,
    code                 TEXT,
    total_ms             REAL    NOT NULL,
    routing_ms           REAL,
    context_ms           REAL,
    llm_ms               REAL,
    validation_ms        REAL,
    database_ms          REAL,
    correction_ms        REAL,
    attempt_count        INTEGER NOT NULL,
    correction_attempted INTEGER NOT NULL,
    sql_reached_database INTEGER NOT NULL,
    sql_executed         INTEGER NOT NULL,
    row_count            INTEGER NOT NULL,
    truncated            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_events_time ON chat_events (created_epoch, id);
"""


@dataclass(frozen=True)
class MonitoringEvent:
    request_id: str
    timestamp: str
    created_epoch: float
    route: str | None
    route_source: str | None
    data_pipeline: bool
    http_status: int
    status: str
    code: str | None
    total_ms: float
    routing_ms: float | None
    context_ms: float | None
    llm_ms: float | None
    validation_ms: float | None
    database_ms: float | None
    correction_ms: float | None
    attempt_count: int
    correction_attempted: bool
    sql_reached_database: bool
    sql_executed: bool
    row_count: int
    truncated: bool


_COLUMNS = [f.name for f in fields(MonitoringEvent)]
_BOOLS = {"data_pipeline", "correction_attempted", "sql_reached_database", "sql_executed", "truncated"}


class MonitoringRepository:
    def __init__(self, path: Path, retention_days: int, max_events: int) -> None:
        self.path = Path(path)
        self.retention_seconds = retention_days * 86400
        self.max_events = max_events
        self._write_lock = threading.Lock()
        self._schema_ready = False
        self._inserts = 0

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._write_lock:
            if self._schema_ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA)
            self._schema_ready = True

    def record(self, event: MonitoringEvent) -> None:
        self._ensure_schema()
        row = asdict(event)
        with self._write_lock, closing(self._connect()) as conn:
            conn.execute(f"INSERT INTO chat_events ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                         [int(row[c]) if c in _BOOLS else row[c] for c in _COLUMNS])
            self._inserts += 1
            if self._inserts % PRUNE_EVERY == 0:
                self._prune(conn, time.time())

    def prune(self, now: float | None = None) -> int:
        """Apply retention now; returns the number of deleted events."""
        self._ensure_schema()
        with self._write_lock, closing(self._connect()) as conn:
            return self._prune(conn, time.time() if now is None else now)

    def _prune(self, conn: sqlite3.Connection, now: float) -> int:
        deleted = conn.execute("DELETE FROM chat_events WHERE created_epoch < ?", (now - self.retention_seconds,)).rowcount
        deleted += conn.execute(
            "DELETE FROM chat_events WHERE id NOT IN "
            "(SELECT id FROM chat_events ORDER BY created_epoch DESC, id DESC LIMIT ?)", (self.max_events,)).rowcount
        return deleted

    def events_since(self, since_epoch: float) -> list[MonitoringEvent]:
        self._ensure_schema()
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM chat_events WHERE created_epoch >= ? "
                                "ORDER BY created_epoch DESC, id DESC", (since_epoch,)).fetchall()
        return [_event(r) for r in rows]

    def page_since(self, since_epoch: float, limit: int, offset: int) -> tuple[int, list[MonitoringEvent]]:
        """(events in the window, one page newest first with id as tie-breaker)."""
        self._ensure_schema()
        with closing(self._connect()) as conn:
            total = conn.execute("SELECT COUNT(*) FROM chat_events WHERE created_epoch >= ?", (since_epoch,)).fetchone()[0]
            rows = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM chat_events WHERE created_epoch >= ? "
                                "ORDER BY created_epoch DESC, id DESC LIMIT ? OFFSET ?", (since_epoch, limit, offset)).fetchall()
        return total, [_event(r) for r in rows]


def _event(row: sqlite3.Row) -> MonitoringEvent:
    return MonitoringEvent(**{c: bool(row[c]) if c in _BOOLS else row[c] for c in _COLUMNS})
