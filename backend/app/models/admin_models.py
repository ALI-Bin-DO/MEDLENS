"""Shapes for the local administration endpoints (GET /admin/metrics, GET /admin/activity).

Events describe how each POST /chat request was handled. They never contain the question,
SQL, result rows, prompts, credentials or exception messages.
"""

from typing import Literal

from pydantic import BaseModel, Field

Window = Literal["1h", "24h", "7d"]


class EventTimings(BaseModel):
    """Milliseconds. total_ms: server-side wall time of the whole POST /chat request (monotonic
    clock, from the moment the request reached the API until the response was ready). The stage
    fields are the response's timings_ms (correction_ms overlaps llm/validation/database_ms)."""

    total_ms: float
    routing_ms: float | None = None
    context_ms: float | None = None
    llm_ms: float | None = None
    validation_ms: float | None = None
    database_ms: float | None = None
    correction_ms: float | None = None


class ActivityEvent(BaseModel):
    request_id: str
    timestamp: str = Field(description="UTC, ISO 8601.")
    route: str | None = Field(None, description="Intent route when known (greeting, data_question, ...).")
    route_source: str | None = Field(None, description="rules or classifier, when known.")
    data_pipeline: bool = Field(description="Admitted to the retrieval -> SQL pipeline.")
    http_status: int
    status: str = Field(description="The /chat response status.")
    code: str | None = Field(None, description="The /chat response code (fixed vocabulary, never free text).")
    timings: EventTimings
    attempt_count: int
    correction_attempted: bool
    sql_reached_database: bool
    sql_executed: bool
    row_count: int
    truncated: bool


class ActivityResponse(BaseModel):
    window: Window
    limit: int
    offset: int
    total: int = Field(description="Events in the window (for pagination).")
    events: list[ActivityEvent] = Field(description="Newest first; ties broken by insertion order, newest first.")


class Ratio(BaseModel):
    numerator: int
    denominator: int
    value: float | None = Field(None, description="numerator / denominator, or null when the denominator is 0.")


class LatencySummary(BaseModel):
    """Nearest-rank percentiles of total_ms (no interpolation; values are observed durations)."""

    sample_count: int
    median_ms: float | None = None
    p95_ms: float | None = None


class MetricsResponse(BaseModel):
    window: Window
    window_start: str = Field(description="UTC, ISO 8601.")
    window_end: str
    total_requests: int
    by_status: dict[str, int] = Field(description="Count per /chat status; every known status is listed.")
    data_pipeline_requests: int = Field(description="Requests admitted to the retrieval -> SQL pipeline.")
    operational_answer_success: Ratio = Field(description=(
        "answered data-pipeline requests / data-pipeline requests. Operational success (a validated query "
        "ran and returned a result), NOT model accuracy: a wrong-but-valid query still counts as answered."))
    data_pipeline_latency: LatencySummary = Field(description="total_ms of every data-pipeline request, any outcome.")
    validation_rejections: int = Field(description="Requests whose final status is rejected.")
    correction_attempts: int = Field(description="Requests where the one SQL correction was attempted.")
    answered_after_correction: int = Field(description="Of those, requests that ended answered.")
    busy: int
    timeouts: int


# --- GET /admin/system ------------------------------------------------------------------------

ComponentState = Literal["ok", "degraded", "unavailable", "unknown", "not_configured"]


class ApiStatus(BaseModel):
    status: ComponentState = "ok"
    version: str
    started_at: str = Field(description="UTC, ISO 8601.")
    uptime_seconds: float


class OllamaStatus(BaseModel):
    status: ComponentState
    reason: str | None = Field(None, description="Fixed code, e.g. unreachable, timeout, not_configured.")
    version: str | None = None
    loaded_models: list[str] = Field(default_factory=list, description="Models currently loaded in memory (/api/ps).")


class ModelStatus(BaseModel):
    """installed: present in Ollama (/api/tags). loaded: currently in memory (/api/ps); an unloaded
    model loads on first use and is not a failure. inference_verified is always false: no text is
    generated and nothing is embedded by this check."""

    role: Literal["sql", "embedding"]
    name: str | None = None
    status: ComponentState
    reason: str | None = None
    installed: bool | None = None
    loaded: bool | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    inference_verified: bool = False


class ModelsStatus(BaseModel):
    sql: ModelStatus
    embedding: ModelStatus


class MySQLStatus(BaseModel):
    status: ComponentState
    reason: str | None = None
    check: str = Field("connectivity: SELECT 1 with the read-only account", description=(
        "Connectivity only; it does not prove that every application query will succeed."))
    latency_ms: float | None = None


class IndexStatus(BaseModel):
    status: ComponentState
    reason: str | None = None
    collection: str | None = None
    points: int | None = None
    counts_by_type: dict[str, int] | None = None
    vector_size: int | None = None
    expected_vector_size: int | None = Field(None, description="From Ollama metadata for the configured embedding model.")
    vector_size_matches: bool | None = None
    stored_embedding_model: str | None = None
    expected_embedding_model: str | None = None
    embedding_model_matches: bool | None = None
    schema_matches_schema_sql: bool | None = Field(None, description=(
        "Stored schema chunks equal those built from database/schema.sql now. Documentation and "
        "example freshness is not checked."))


class SystemComponents(BaseModel):
    api: ApiStatus
    ollama: OllamaStatus
    models: ModelsStatus
    mysql: MySQLStatus
    index: IndexStatus


class SystemStatusResponse(BaseModel):
    checked_at: str = Field(description="UTC time the probes ran; the snapshot may be reused for cache_ttl_seconds.")
    cache_ttl_seconds: int
    components: SystemComponents


# --- GET /admin/config ------------------------------------------------------------------------

class AdminConfigResponse(BaseModel):
    """An explicit allowlist of non-secret settings. No credentials, hosts, URLs or paths."""

    models: dict[str, str | None]
    rag: dict[str, str | int]
    sql_generation: dict[str, int | float]
    query_execution: dict[str, int]
    charts: dict[str, int]
    monitoring: dict[str, int]
