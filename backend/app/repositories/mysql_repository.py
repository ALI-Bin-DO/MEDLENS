"""Read-only execution of validated SELECT queries against the medlens database.

Safety boundary (all inside execute(), in this order):
    1. The exact SQL text is validated again with sql_validator, even if the caller passes a
       ValidatedSQL. Rejected SQL raises SQLValidationError before any connection is opened.
    2. Only MYSQL_READONLY_USER / MYSQL_READONLY_PASSWORD are used; there is no fallback.
    3. Fixed, application-controlled session statements are run and then read back:
           SET SESSION max_execution_time = <MYSQL_MAX_EXECUTION_MS>
           SET SESSION transaction_read_only = ON
       If either value does not read back as expected, nothing is executed.
       Evaluation only: a repository built with reference_time also runs
           SET SESSION timestamp = <whole seconds>
       so CURDATE()/NOW() return that fixed time in this session (repeatable date tests). It changes
       no data and is never set by the API.
    4. The query runs unchanged on an unbuffered cursor. At most row_cap + 1 rows are fetched;
       row_cap are returned and truncated=True says more existed.

Limits: the row cap bounds the rows returned and held in memory here, not the work the server
does (a GROUP BY may scan every row) or the bytes per row. max_execution_time is the separate
server-side time limit; connection and socket timeouts bound waiting on the network.

Constructing MySQLRepository does not connect. It never reads MYSQL_USER / MYSQL_PASSWORD.
"""

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import mysql.connector
from mysql.connector import FieldType, errorcode

from backend.app.config import Settings, get_settings
from backend.app.services.sql_validator import SQLValidationError, ValidatedSQL, validate

JSONValue = None | bool | int | float | str | list[str]

# Fixed session statements; the only SQL besides the validated query this module ever sends.
_SET_EXECUTION_LIMIT = "SET SESSION max_execution_time = {ms:d}"
_SET_READ_ONLY = "SET SESSION transaction_read_only = ON"
_READ_BACK = "SELECT @@SESSION.max_execution_time, @@SESSION.transaction_read_only"
_SET_TIMESTAMP = "SET SESSION timestamp = {seconds:d}"  # evaluation only (reference_time)
_CONNECTIVITY_CHECK = "SELECT 1"  # GET /admin/system only


class ExecutionError(RuntimeError):
    """Base class for failures while running a query."""


class DatabaseConnectionError(ExecutionError):
    """MySQL could not be reached or refused the read-only login."""


class SessionSetupError(ExecutionError):
    """A required session control (time limit, read-only) could not be applied; nothing was run."""


class QueryTimeoutError(ExecutionError):
    """The query exceeded the server-side execution limit or the connector socket timeout."""


class QueryExecutionError(ExecutionError):
    """MySQL rejected or failed the query (e.g. ONLY_FULL_GROUP_BY, type errors).

    errno is MySQL's error number (None if not from MySQL); mysql_message is MySQL's own text,
    used only to build a bounded, sanitized correction diagnostic.
    """

    def __init__(self, message: str, errno: int | None = None, mysql_message: str | None = None) -> None:
        super().__init__(message)
        self.errno = errno
        self.mysql_message = mysql_message


@dataclass(frozen=True)
class QueryResult:
    """Rows from one query. rows are JSON-safe (see to_json_value); columns keep order and duplicates."""

    sql: str
    columns: list[str]
    rows: list[list[JSONValue]]
    row_count: int  # rows returned (<= row_cap)
    truncated: bool  # True if the query produced more than row_cap rows
    row_cap: int
    duration_seconds: float  # from sending the query until fetching stopped
    # Per-column type from MySQL's result metadata (see column_kind): integer, decimal, float, date,
    # datetime, time, year, string or other. None when not known (e.g. results built by tests).
    column_types: list[str] | None = None


class MySQLRepository:
    """Runs validated SELECT queries with the read-only account and fixed session limits."""

    def __init__(self, settings: Settings | None = None, connect: Callable[..., Any] = mysql.connector.connect,
                 reference_time: dt.datetime | None = None) -> None:
        self.settings = settings or get_settings()
        self._connect = connect
        # Evaluation only: pin CURDATE()/NOW() for repeatable date tests (an aware datetime).
        if reference_time is not None and reference_time.tzinfo is None:
            raise ValueError("reference_time must be timezone-aware")
        self.reference_time = reference_time
        s = self.settings
        if s.mysql_io_timeout_seconds * 1000 <= s.mysql_max_execution_ms:
            raise ValueError(
                f"MYSQL_IO_TIMEOUT_SECONDS ({s.mysql_io_timeout_seconds}) must be longer than "
                f"MYSQL_MAX_EXECUTION_MS ({s.mysql_max_execution_ms} ms) so the server limit fires first"
            )

    def execute(self, query: str | ValidatedSQL) -> QueryResult:
        """Validate, then run one SELECT read-only. Raises SQLValidationError before connecting if rejected."""
        sql = query.sql if isinstance(query, ValidatedSQL) else query
        validation = validate(sql)  # never trust a ValidatedSQL built elsewhere
        if not validation.ok:
            raise SQLValidationError(validation.code or "rejected", validation.reason or "rejected")

        user, password = self.settings.readonly_credentials()
        s = self.settings
        try:
            conn = self._connect(
                host=s.mysql_host, port=s.mysql_port, database=s.mysql_database, user=user, password=password,
                connection_timeout=s.mysql_connect_timeout_seconds, read_timeout=s.mysql_io_timeout_seconds,
                write_timeout=s.mysql_io_timeout_seconds, autocommit=True, use_pure=True,
            )
        except mysql.connector.Error as exc:
            raise DatabaseConnectionError(f"cannot connect to MySQL as {user!r}: {_mysql_message(exc)}") from exc

        drained = False  # True only when the result was read to the end and the connection can close normally
        try:
            self._setup_session(conn)
            cursor = conn.cursor(buffered=False)
            started = time.perf_counter()
            cursor.execute(sql)
            if cursor.description is None:
                raise QueryExecutionError("the statement returned no result set")
            columns = [str(d[0]) for d in cursor.description]
            # Type codes feed optional charts only; missing metadata must never fail the query.
            column_types = [column_kind(d[1] if len(d) > 1 else None) for d in cursor.description]
            cap = s.mysql_row_cap
            fetched = cursor.fetchmany(cap + 1)
            duration = time.perf_counter() - started
            truncated = len(fetched) > cap
            drained = not truncated  # fewer than cap + 1 rows means the connector reached the end
            if drained:
                cursor.close()
            rows = [[to_json_value(value) for value in row] for row in fetched[:cap]]
            return QueryResult(sql, columns, rows, len(rows), truncated, cap, duration, column_types)
        except mysql.connector.Error as exc:
            raise _query_error(exc, s.mysql_max_execution_ms) from exc
        finally:
            _release(conn, graceful=drained)

    def check_connectivity(self, timeout_seconds: int) -> None:
        """Connect with the read-only account and run the fixed statement SELECT 1 (never user or
        model SQL, never hospital data). Proves connectivity only, not that every query will work.
        Raises MissingSettingError or mysql.connector.Error; the connection is always closed."""
        user, password = self.settings.readonly_credentials()
        s = self.settings
        conn = self._connect(
            host=s.mysql_host, port=s.mysql_port, database=s.mysql_database, user=user, password=password,
            connection_timeout=timeout_seconds, read_timeout=timeout_seconds, write_timeout=timeout_seconds,
            autocommit=True, use_pure=True,
        )
        completed = False
        try:
            cursor = conn.cursor(buffered=True)
            try:
                cursor.execute(_CONNECTIVITY_CHECK)
                row = cursor.fetchone()
            finally:
                cursor.close()
            if row is None or int(row[0]) != 1:
                raise QueryExecutionError("connectivity check returned an unexpected value")
            completed = True
        finally:
            _release(conn, graceful=completed)

    def _setup_session(self, conn: Any) -> None:
        """Apply and read back the fixed session controls; refuse to continue if they did not take effect."""
        expected_ms = self.settings.mysql_max_execution_ms
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(_SET_EXECUTION_LIMIT.format(ms=int(expected_ms)))
            cursor.execute(_SET_READ_ONLY)
            if self.reference_time is not None:
                cursor.execute(_SET_TIMESTAMP.format(seconds=int(self.reference_time.timestamp())))
            cursor.execute(_READ_BACK)
            row = cursor.fetchone()
        except mysql.connector.Error as exc:
            raise SessionSetupError(f"could not apply session limits: {_mysql_message(exc)}") from exc
        finally:
            cursor.close()
        if row is None or int(row[0]) != expected_ms or int(row[1]) != 1:
            raise SessionSetupError(
                f"session limits did not take effect (max_execution_time={row and row[0]}, "
                f"transaction_read_only={row and row[1]}); query not executed"
            )


def _release(conn: Any, graceful: bool) -> None:
    """Close the connection. With unread rows (or after an error) the socket is shut down instead,
    so the connector never reads the rest of a large result just to close."""
    try:
        if graceful:
            conn.close()
        else:
            conn.shutdown()
    except Exception:  # cleanup must not hide the original error
        try:
            conn.shutdown()
        except Exception:
            pass


def _query_error(exc: mysql.connector.Error, limit_ms: int) -> ExecutionError:
    if getattr(exc, "errno", None) == errorcode.ER_QUERY_TIMEOUT:
        return QueryTimeoutError(f"query stopped by the server after the {limit_ms} ms execution limit")
    if getattr(exc, "errno", None) in (errorcode.CR_SERVER_LOST, errorcode.CR_SERVER_GONE_ERROR):
        return QueryTimeoutError(f"connection lost during the query (socket timeout or server gone): {_mysql_message(exc)}")
    return QueryExecutionError(f"MySQL error: {_mysql_message(exc)}", getattr(exc, "errno", None),
                               getattr(exc, "msg", None) or str(exc))


def _mysql_message(exc: mysql.connector.Error) -> str:
    errno = getattr(exc, "errno", None)
    msg = getattr(exc, "msg", None) or str(exc)
    return f"{errno} {msg}" if errno and errno > 0 else msg


_KINDS = {
    "integer": (FieldType.TINY, FieldType.SHORT, FieldType.LONG, FieldType.LONGLONG, FieldType.INT24),
    "decimal": (FieldType.DECIMAL, FieldType.NEWDECIMAL),
    "float": (FieldType.FLOAT, FieldType.DOUBLE),
    "date": (FieldType.DATE, FieldType.NEWDATE),
    "datetime": (FieldType.DATETIME, FieldType.TIMESTAMP),
    "time": (FieldType.TIME,),
    "year": (FieldType.YEAR,),
    "string": (FieldType.VARCHAR, FieldType.VAR_STRING, FieldType.STRING, FieldType.ENUM),
}
_KIND_BY_CODE = {code: kind for kind, codes in _KINDS.items() for code in codes}


def column_kind(type_code: int | None) -> str:
    """Normalize a MySQL result-column type code (cursor.description) to a small set of kinds."""
    return _KIND_BY_CODE.get(type_code, "other")


def to_json_value(value: Any) -> JSONValue:
    """Convert one MySQL value to a JSON-safe value without losing its meaning.

    Decimal -> exact string ("173424.90"); date/datetime/time -> ISO string; TIME (timedelta)
    -> "[-]HH:MM:SS[.ffffff]" (hours may exceed 24); bytes -> UTF-8 text or "0x..." hex;
    SET -> sorted list; NULL -> None. Unknown types raise TypeError instead of guessing.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return _format_mysql_time(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return "0x" + bytes(value).hex()
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    raise TypeError(f"cannot serialize MySQL value of type {type(value).__name__}")


def _format_mysql_time(delta: dt.timedelta) -> str:
    """MySQL TIME arrives as timedelta; render it the way MySQL prints it."""
    total_us = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    sign = "-" if total_us < 0 else ""
    total_us = abs(total_us)
    seconds, micro = divmod(total_us, 1_000_000)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{text}.{micro:06d}" if micro else text
