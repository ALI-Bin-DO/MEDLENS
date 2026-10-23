"""Pydantic shapes for the chat request and response (API contract, not AI model weights)."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from backend.app.config import get_settings

ChatStatus = Literal[
    "conversation", "answered", "cannot_answer", "needs_clarification", "classification_failed",
    "conversation_expired", "conversation_busy",
    "rejected", "generation_failed", "query_failed",
    "invalid_request", "busy", "unavailable", "timeout", "error",
]


class ChatRequest(BaseModel):
    """A user's question in English. Blank questions and questions longer than
    CHAT_MAX_QUESTION_CHARS (default 500) are rejected with status "invalid_request"."""

    question: str
    conversation_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{16,64}$", description=(
        "Continue this conversation (as returned by an earlier response). Omit to start a new one. "
        "Not authentication: anyone with the ID can continue or delete the conversation."))

    @field_validator("question")
    @classmethod
    def _check_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        limit = get_settings().chat_max_question_chars
        if len(value) > limit:
            raise ValueError(f"question must be at most {limit} characters")
        return value


class Timings(BaseModel):
    """Wall-clock milliseconds per step; null if the step did not run. total_ms covers everything.
    llm_ms, validation_ms and database_ms are summed over all SQL attempts; correction_ms is the
    whole correction attempt (its LLM call, validation and execution), so it overlaps those three."""

    routing_ms: float | None = None  # intent rules + (if needed) the LLM intent classifier
    context_ms: float | None = None
    llm_ms: float | None = None
    validation_ms: float | None = None
    database_ms: float | None = None
    correction_ms: float | None = None
    total_ms: float | None = None


class SqlAttemptInfo(BaseModel):
    """One SQL generation attempt (1 = initial, 2 = the single allowed correction)."""

    attempt: int
    purpose: Literal["initial", "correction"]
    outcome: Literal["sql", "cannot_answer", "needs_clarification", "generation_failed"]
    sql: str | None = None
    validation: Literal["passed", "rejected", "not_run"]
    execution: Literal["succeeded", "failed", "not_run"]
    error_code: str | None = Field(None, description="Validator code, mysql_<errno>, timeout, unavailable, ...")
    retryable: bool = Field(False, description="Whether this error was on the correction allowlist.")
    llm_ms: float | None = None
    validation_ms: float | None = None
    database_ms: float | None = None


ChartView = Literal["bar_vertical", "bar_horizontal", "line", "pie"]


class ChartAxis(BaseModel):
    label: str = Field(description="Column name(s) as returned; no units or currencies are added.")
    fields: list[str] = Field(description="Result columns this axis is built from.")
    kind: Literal["category", "date", "month", "number"] = Field(
        description="month: x values are 'YYYY-MM' from DATE_FORMAT(col, '%Y-%m').")


class ChartPoint(BaseModel):
    x: str = Field(description="Category label (text columns joined with ' · ') or ISO date.")
    y: float | int = Field(description="Number for plotting only.")
    y_exact: str | int | float = Field(description="The exact value from rows, e.g. the Decimal string \"2663.415600\".")
    row_index: int = Field(description="Index of the source row in rows.")


class ChartSpec(BaseModel):
    """Frontend-neutral chart description built deterministically from the result (no model call)."""

    type: Literal["bar", "line"] = Field(description="Data shape: bar = categories, line = a date or month series.")
    views: list[ChartView] = Field(description=(
        "Views this result supports, in the order offered (chart_builder.py decides; clients must not "
        "offer other views). pie appears only for 2-5 categories of a non-distinct COUNT/SUM that form a whole."))
    default_view: ChartView
    notes: dict[str, str] = Field(default_factory=dict, description=(
        "Why a view is not offered, by view name (e.g. pie: the values are averages). For explanations only."))
    x: ChartAxis
    y: ChartAxis
    points: list[ChartPoint] = Field(description="bar: result order; line: sorted by date.")
    point_count: int


class ChatResponse(BaseModel):
    """Same shape for every outcome; read `status` first.

    Values in rows are JSON-safe: DECIMAL as exact strings ("173424.90"), dates/times as ISO
    strings, TIME as "HH:MM:SS", NULL as null.
    """

    status: ChatStatus
    message: str
    question: str | None = Field(None, description="The user's original message.")
    conversation_id: str | None = Field(None, description="Pass this back to continue the conversation.")
    resolved_question: str | None = Field(None, description=(
        "Standalone question actually answered (a follow-up resolved with this conversation's context)."))
    code: str | None = Field(None, description=(
        "conversation: greeting | thanks_or_goodbye | capabilities. needs_clarification: ambiguous. "
        "cannot_answer: out_of_scope | unsupported_language. rejected: validation code. "
        "unavailable/timeout: failing component."))
    sql: str | None = Field(None, description="SQL of the last attempt, if it produced SQL (see sql_executed).")
    sql_executed: bool = Field(False, description=(
        "True only when the SQL in `sql` was validated and ran successfully; its result is in rows."))
    sql_reached_database: bool = Field(False, description=(
        "True if any attempt's SQL was sent to MySQL, including one that MySQL then refused."))
    attempt_count: int = Field(0, description=(
        "SQL generation attempts made: 0 = none (routed reply or failure before SQL generation), "
        "1 = initial only, 2 = initial + one correction."))
    correction_attempted: bool = False
    sql_attempts: list[SqlAttemptInfo] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = Field(False, description="True if more rows existed than the row cap (MYSQL_ROW_CAP).")
    timings_ms: Timings = Field(default_factory=Timings)
    source_chunk_ids: list[str] = Field(default_factory=list, description="Knowledge chunks given to the model.")
    view_request: ChartView | None = Field(None, description=(
        "For a request to redraw the previous result: the view to show it in, reusing the rows the client "
        "already has (no query ran). Null when the view is not supported; message explains why."))
    chart: ChartSpec | None = Field(None, description=(
        "Optional chart for status=answered only; null when the result is not clearly chartable."))
