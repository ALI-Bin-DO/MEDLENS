"""Builds monitoring events for POST /chat and computes the admin metrics.

One event per POST /chat request, built from the final ChatResponse (and the AnswerTrace when the
pipeline ran). Only metadata is kept; the question, SQL, rows, prompts and error messages are
never read into the event.

Metric definitions (all over events whose timestamp is inside the window):
    data-pipeline request      admitted to the retrieval -> SQL pipeline (ChatService started it),
                               whatever the outcome; excludes greetings/routed replies, busy,
                               invalid requests and failures before routing finished
    operational answer success answered data-pipeline requests / data-pipeline requests; a query
                               ran and returned a result - NOT model accuracy
    data-pipeline latency      total_ms of every data-pipeline request (any outcome); median and p95
                               use the nearest-rank method on sorted values: the ceil(q * n)-th
                               smallest value (q = 0.5 or 0.95), so both are observed durations
    validation rejections      final status "rejected"
    correction attempts        correction_attempted; answered_after_correction = those answered
    busy / timeouts            status "busy" / "timeout"
Rates and percentiles are null when there are no eligible events.
"""

import logging
import math
import time
from datetime import datetime, timezone

from backend.app.models.admin_models import (
    ActivityEvent, ActivityResponse, EventTimings, LatencySummary, MetricsResponse, Ratio, Window,
)
from backend.app.models.chat_models import ChatResponse, ChatStatus
from backend.app.repositories.monitoring_repository import MonitoringEvent, MonitoringRepository

logger = logging.getLogger(__name__)

WINDOW_SECONDS: dict[str, int] = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}
KNOWN_STATUSES: tuple[str, ...] = ChatStatus.__args__


def build_event(request_id: str, started_epoch: float, total_seconds: float, http_status: int,
                body: ChatResponse | None, trace=None) -> MonitoringEvent:
    """Event from the final response metadata only. body is None if no ChatResponse was produced."""
    t = body.timings_ms if body is not None else None
    return MonitoringEvent(
        request_id=request_id,
        timestamp=datetime.fromtimestamp(started_epoch, timezone.utc).isoformat(),
        created_epoch=started_epoch,
        route=getattr(trace, "route", None),
        route_source=getattr(trace, "route_source", None),
        data_pipeline=bool(getattr(trace, "data_pipeline", False)),
        http_status=http_status,
        status=body.status if body is not None else "error",
        code=body.code if body is not None else None,
        total_ms=round(total_seconds * 1000, 1),
        routing_ms=t.routing_ms if t else None, context_ms=t.context_ms if t else None, llm_ms=t.llm_ms if t else None,
        validation_ms=t.validation_ms if t else None, database_ms=t.database_ms if t else None,
        correction_ms=t.correction_ms if t else None,
        attempt_count=body.attempt_count if body is not None else 0,
        correction_attempted=body.correction_attempted if body is not None else False,
        sql_reached_database=body.sql_reached_database if body is not None else False,
        sql_executed=body.sql_executed if body is not None else False,
        row_count=body.row_count if body is not None else 0,
        truncated=body.truncated if body is not None else False,
    )


def record_safely(repository: MonitoringRepository, event: MonitoringEvent) -> None:
    """Monitoring must never break chat: log and drop the event on any failure."""
    try:
        repository.record(event)
    except Exception:
        logger.exception("monitoring event could not be recorded")


def nearest_rank(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    return sorted_values[max(1, math.ceil(q * len(sorted_values))) - 1]


def metrics(repository: MonitoringRepository, window: Window, now: float | None = None) -> MetricsResponse:
    now = time.time() if now is None else now
    start = now - WINDOW_SECONDS[window]
    events = repository.events_since(start)
    by_status = {s: 0 for s in KNOWN_STATUSES}
    for e in events:
        by_status[e.status] = by_status.get(e.status, 0) + 1
    pipeline = [e for e in events if e.data_pipeline]
    answered = sum(1 for e in pipeline if e.status == "answered")
    latencies = sorted(e.total_ms for e in pipeline)
    corrected = [e for e in events if e.correction_attempted]
    return MetricsResponse(
        window=window, window_start=_iso(start), window_end=_iso(now),
        total_requests=len(events), by_status=by_status,
        data_pipeline_requests=len(pipeline),
        operational_answer_success=Ratio(numerator=answered, denominator=len(pipeline),
                                         value=answered / len(pipeline) if pipeline else None),
        data_pipeline_latency=LatencySummary(sample_count=len(latencies), median_ms=nearest_rank(latencies, 0.5),
                                             p95_ms=nearest_rank(latencies, 0.95)),
        validation_rejections=by_status.get("rejected", 0),
        correction_attempts=len(corrected),
        answered_after_correction=sum(1 for e in corrected if e.status == "answered"),
        busy=by_status.get("busy", 0), timeouts=by_status.get("timeout", 0),
    )


def activity(repository: MonitoringRepository, window: Window, limit: int, offset: int,
             now: float | None = None) -> ActivityResponse:
    now = time.time() if now is None else now
    total, events = repository.page_since(now - WINDOW_SECONDS[window], limit, offset)
    return ActivityResponse(window=window, limit=limit, offset=offset, total=total, events=[_activity(e) for e in events])


def _activity(e: MonitoringEvent) -> ActivityEvent:
    return ActivityEvent(
        request_id=e.request_id, timestamp=e.timestamp, route=e.route, route_source=e.route_source,
        data_pipeline=e.data_pipeline, http_status=e.http_status, status=e.status, code=e.code,
        timings=EventTimings(total_ms=e.total_ms, routing_ms=e.routing_ms, context_ms=e.context_ms, llm_ms=e.llm_ms,
                             validation_ms=e.validation_ms, database_ms=e.database_ms, correction_ms=e.correction_ms),
        attempt_count=e.attempt_count, correction_attempted=e.correction_attempted,
        sql_reached_database=e.sql_reached_database, sql_executed=e.sql_executed,
        row_count=e.row_count, truncated=e.truncated,
    )


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()
