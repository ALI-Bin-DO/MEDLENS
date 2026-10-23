"""Application settings, read from the .env file in the project root.

Only the read-only MySQL account is exposed here. MYSQL_USER / MYSQL_PASSWORD are
for admin scripts (database/load_data.py) and are deliberately never read by the app.
Reading settings does not open any connection.
"""

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# backend/app/config.py -> parents[2] is the MedLens project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

# Single source of truth for the schema; RAG reads CREATE TABLE text from it.
SCHEMA_FILE = PROJECT_ROOT / "database" / "schema.sql"
KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "knowledge_base"
DATA_CATALOG_FILE = KNOWLEDGE_BASE_DIR / "data_catalog.md"
EXAMPLES_FILE = KNOWLEDGE_BASE_DIR / "examples.sql"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class MissingSettingError(RuntimeError):
    """Raised when a required setting is not present in .env."""


@dataclass(frozen=True)
class Settings:
    """All runtime settings. Optional values are None when unset in .env."""

    mysql_host: str
    mysql_port: int
    mysql_database: str
    mysql_readonly_user: str | None
    mysql_readonly_password: str | None
    mysql_connect_timeout_seconds: int  # opening the connection
    mysql_io_timeout_seconds: int  # connector socket read/write timeout; must exceed the execution limit
    mysql_max_execution_ms: int  # server-side SELECT time limit (SESSION max_execution_time)
    mysql_row_cap: int  # most rows returned to the caller

    ollama_base_url: str
    ollama_sql_model: str | None
    ollama_embedding_model: str | None
    ollama_timeout_seconds: float  # embedding requests
    ollama_generation_timeout_seconds: float  # SQL-generation requests (slower)
    ollama_sql_num_ctx: int  # context window requested for SQL generation, in tokens
    ollama_sql_num_predict: int  # maximum tokens the model may generate

    qdrant_path: Path
    qdrant_collection: str

    # Documentation/example chunks retrieved per question (schema chunks are always all included).
    rag_context_top_k: int

    chart_max_points: int  # results with more rows than this get chart=null

    export_max_bytes: int  # largest accepted POST /export/csv body
    export_max_columns: int
    export_max_rows: int
    export_max_cell_chars: int  # longest text cell or column name

    monitoring_db_path: Path  # local SQLite file for /chat request events (never MySQL/Qdrant)
    monitoring_retention_days: int  # events older than this are deleted
    monitoring_max_events: int  # at most this many newest events are kept

    system_status_cache_seconds: int  # GET /admin/system reuses a snapshot this long
    system_probe_timeout_seconds: int  # per-call timeout for status probes (Ollama, MySQL)

    chat_max_question_chars: int  # longest accepted question
    chat_max_waiting_requests: int  # requests allowed to wait while one is running; more get "busy"
    cors_allowed_origins: tuple[str, ...]  # explicit frontend origins; empty = no CORS
    conversation_ttl_seconds: int  # a conversation expires after this long without use
    conversation_max_count: int  # in-memory conversations kept at once (least recently used evicted)
    conversation_max_turns: int  # successful data turns kept per conversation

    def readonly_credentials(self) -> tuple[str, str]:
        """Return (user, password) for the read-only account.

        Raises MissingSettingError instead of falling back to any other account.
        """
        if not self.mysql_readonly_user or not self.mysql_readonly_password:
            raise MissingSettingError(
                "MYSQL_READONLY_USER and MYSQL_READONLY_PASSWORD must be set in .env"
            )
        return self.mysql_readonly_user, self.mysql_readonly_password


def _resolve_project_path(value: str) -> Path:
    """Treat relative paths in .env as relative to the project root, not the terminal's cwd."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _positive_seconds(name: str, default: str) -> float:
    """Read a timeout in seconds; it must be a finite number greater than zero."""
    raw = os.getenv(name, default)
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number of seconds, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number greater than 0, got {raw!r}")
    return value


def _non_negative_int(name: str, default: str) -> int:
    """Read a count; it must be a whole number >= 0."""
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{name} must be 0 or more, got {raw!r}")
    return value


def _positive_int(name: str, default: str) -> int:
    """Read a count that must be a whole number >= 1."""
    value = _non_negative_int(name, default)
    if value < 1:
        raise ValueError(f"{name} must be 1 or more, got {value}")
    return value


@lru_cache
def get_settings() -> Settings:
    """Load .env from the project root once and return the settings."""
    load_dotenv(ENV_FILE)
    return Settings(
        mysql_host=os.getenv("MYSQL_HOST", "localhost"),
        mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
        mysql_database=os.getenv("MYSQL_DATABASE", "medlens"),
        mysql_readonly_user=os.getenv("MYSQL_READONLY_USER") or None,
        mysql_readonly_password=os.getenv("MYSQL_READONLY_PASSWORD") or None,
        mysql_connect_timeout_seconds=_positive_int("MYSQL_CONNECT_TIMEOUT_SECONDS", "5"),
        mysql_io_timeout_seconds=_positive_int("MYSQL_IO_TIMEOUT_SECONDS", "10"),
        mysql_max_execution_ms=_positive_int("MYSQL_MAX_EXECUTION_MS", "5000"),
        mysql_row_cap=_positive_int("MYSQL_ROW_CAP", "200"),
        # 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 ::1 first, and Ollama
        # (IPv4 only) refuses it after ~2 s, adding that delay to every request.
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_sql_model=os.getenv("OLLAMA_SQL_MODEL") or None,
        ollama_embedding_model=os.getenv("OLLAMA_EMBEDDING_MODEL") or None,
        ollama_timeout_seconds=_positive_seconds("OLLAMA_TIMEOUT_SECONDS", "60"),
        ollama_generation_timeout_seconds=_positive_seconds("OLLAMA_GENERATION_TIMEOUT_SECONDS", "180"),
        ollama_sql_num_ctx=_positive_int("OLLAMA_SQL_NUM_CTX", "4096"),
        ollama_sql_num_predict=_positive_int("OLLAMA_SQL_NUM_PREDICT", "512"),
        qdrant_path=_resolve_project_path(os.getenv("QDRANT_PATH", "storage/qdrant")),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "medlens_knowledge"),
        rag_context_top_k=_non_negative_int("RAG_CONTEXT_TOP_K", "5"),
        chart_max_points=_positive_int("CHART_MAX_POINTS", "50"),
        export_max_bytes=_positive_int("EXPORT_MAX_BYTES", "2000000"),
        export_max_columns=_positive_int("EXPORT_MAX_COLUMNS", "100"),
        export_max_rows=_positive_int("EXPORT_MAX_ROWS", "10000"),
        export_max_cell_chars=_positive_int("EXPORT_MAX_CELL_CHARS", "10000"),
        monitoring_db_path=_resolve_project_path(os.getenv("MONITORING_DB_PATH", "storage/monitoring/medlens_monitoring.sqlite3")),
        monitoring_retention_days=_positive_int("MONITORING_RETENTION_DAYS", "30"),
        monitoring_max_events=_positive_int("MONITORING_MAX_EVENTS", "50000"),
        system_status_cache_seconds=_non_negative_int("SYSTEM_STATUS_CACHE_SECONDS", "15"),
        system_probe_timeout_seconds=_positive_int("SYSTEM_PROBE_TIMEOUT_SECONDS", "3"),
        chat_max_question_chars=_positive_int("CHAT_MAX_QUESTION_CHARS", "500"),
        chat_max_waiting_requests=_non_negative_int("CHAT_MAX_WAITING_REQUESTS", "1"),
        cors_allowed_origins=_origins(os.getenv("CORS_ALLOWED_ORIGINS", "")),
        conversation_ttl_seconds=_positive_int("CONVERSATION_TTL_SECONDS", "1800"),
        conversation_max_count=_positive_int("CONVERSATION_MAX_COUNT", "200"),
        conversation_max_turns=_positive_int("CONVERSATION_MAX_TURNS", "5"),
    )


def _origins(raw: str) -> tuple[str, ...]:
    """Comma-separated explicit origins such as http://127.0.0.1:5173. A wildcard is refused."""
    origins = tuple(o.strip().rstrip("/") for o in raw.split(",") if o.strip())
    if "*" in origins:
        raise ValueError("CORS_ALLOWED_ORIGINS must list explicit origins; '*' is not allowed")
    for origin in origins:
        if not origin.startswith(("http://", "https://")):
            raise ValueError(f"CORS_ALLOWED_ORIGINS entries must start with http:// or https://, got {origin!r}")
    return origins
