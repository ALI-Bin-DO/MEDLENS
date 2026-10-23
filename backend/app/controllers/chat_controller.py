"""HTTP layer for questions: receives the request and returns the response only.
No SQL, database access or LLM calls here; ChatService does the work in a worker thread.

Obvious standalone greetings / thanks / capability questions are answered from rules before
the inference gate, without the model, index or database (so they work even if those are down).
Everything else runs inside ONE gate reservation: LLM intent classification, then (for English
data questions) the data pipeline - the classifier and SQL calls are serialized together.

HTTP status mapping (every response body is a ChatResponse; read `status`):
    200  conversation, answered, cannot_answer, needs_clarification, classification_failed,
         rejected, generation_failed, query_failed (the request was processed; the outcome is in `status`)
    422  invalid_request   blank or too-long question, malformed body
    503  busy              another question is running and the wait slots are full (Retry-After set)
    503  unavailable       knowledge index, Ollama, MySQL or configuration not available
    504  timeout           Ollama or the MySQL execution limit timed out
    500  error             unexpected failure (details only in the server log)
Client messages are fixed texts: no credentials, connection strings, stack traces or raw exceptions.
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from backend.app.models.chat_models import ChatRequest, ChatResponse, SqlAttemptInfo, Timings
from backend.app.services.chat_service import AnswerTrace, ChatService
from backend.app.services.conversation_store import ConversationBusy, ConversationExpired, ConversationStore, Turn
from backend.app.services.inference_gate import InferenceGate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

HTTP_STATUS = {
    "conversation": 200, "answered": 200, "classification_failed": 200, "cannot_answer": 200, "needs_clarification": 200, "rejected": 200,
    "generation_failed": 200, "query_failed": 200,
    "conversation_expired": 404, "conversation_busy": 409,
    "invalid_request": 422, "busy": 503, "unavailable": 503, "timeout": 504, "error": 500,
}
BUSY_RETRY_AFTER_SECONDS = 5

_FIXED_MESSAGES = {
    "rejected": "The generated SQL was rejected by validation and was not executed.",
    "drops_unmatched_rows": ("The generated SQL was not executed because it would not return every requested row, "
                             "so no partial list is shown."),
    "request_mismatch": ("The generated SQL was not executed because it does not match what was asked, so no answer "
                         "is shown."),
    "generation_failed": "The language model did not produce a usable reply.",
    "query_failed": "MySQL could not run the generated query.",
    "classification_failed": "I couldn't tell what kind of request that is. Please rephrase it as a question about the hospital data.",
    "busy": "Another question is being processed. Please try again shortly.",
    "conversation_expired": ("This conversation has expired or does not exist. Start a new conversation "
                             "(send the question without conversation_id)."),
    "conversation_busy": "This conversation is still answering a previous question. Wait for it, then retry.",
    "error": "An internal error occurred.",
}
_COMPONENT_MESSAGES = {
    ("unavailable", "index"): "The knowledge index is not available. Run build_index, and make sure no CLI command holds the index.",
    ("unavailable", "embedding"): "The local language model service (Ollama) is not reachable.",
    ("unavailable", "llm"): "The local language model service (Ollama) is not reachable.",
    ("unavailable", "database"): "The database is not reachable.",
    ("unavailable", "config"): "The server is not fully configured.",
    ("timeout", "embedding"): "The local language model service did not respond in time.",
    ("timeout", "llm"): "The language model did not respond in time.",
    ("timeout", "database"): "The query exceeded the execution time limit.",
}


def get_chat_service(request: Request) -> ChatService:
    """The application-owned ChatService (its index may be None if it could not be opened)."""
    return request.app.state.chat_service


def get_gate(request: Request) -> InferenceGate:
    return request.app.state.gate


def get_conversations(request: Request) -> ConversationStore:
    return request.app.state.conversations


def _turn_to_keep(trace: AnswerTrace) -> Turn | None:
    """A successfully answered data question becomes conversation context, and so does a data
    question that MedLens answered with a clarifying question (a name that matches several people,
    unstored payment dates, an undefined measure, or the SQL model's own question), so the user's
    reply can be resolved. Answered turns also keep which chart views the result offered."""
    if not trace.data_pipeline or not trace.resolved_question:
        return None
    last = trace.attempts[-1] if trace.attempts else None
    g = trace.generation
    if trace.stage == "needs_clarification":
        asked = trace.clarification or (g.text if last is not None and last.outcome == "needs_clarification"
                                        and g is not None else None)
        if not asked:
            return None
        candidates = tuple((role, p.id, p.first_name, p.last_name, *p.details) for role, p in trace.candidates)
        return Turn(question=trace.question, resolved_question=trace.resolved_question, sql=None,
                    clarification=asked[:500], candidates=candidates)
    if trace.stage != "answered" or last is None:
        return None
    if last.execution != "succeeded" or not last.sql:
        return None
    chart = trace.chart
    return Turn(question=trace.question, resolved_question=trace.resolved_question, sql=last.sql,
                chart_views=tuple(chart.views) if chart is not None else (),
                chart_notes=tuple(sorted(chart.notes.items())) if chart is not None else (),
                grouping=trace.request.grouping if trace.request is not None else ())


def respond(body: ChatResponse, http_request: Request | None = None, trace: AnswerTrace | None = None) -> JSONResponse:
    """Build the HTTP response. The final body (and trace) are attached to the request so the
    monitoring middleware can record one metadata-only event for it."""
    if http_request is not None:
        http_request.state.chat_outcome = (body, trace)
    headers = {"Retry-After": str(BUSY_RETRY_AFTER_SECONDS)} if body.status == "busy" else None
    return JSONResponse(status_code=HTTP_STATUS[body.status], content=body.model_dump(), headers=headers)


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 1)


def to_response(trace: AnswerTrace) -> ChatResponse:
    """Build the client response from a trace. Never copies trace.error or attempt diagnostics."""
    g, v, r, t = trace.generation, trace.validation, trace.result, trace.timings
    last = trace.attempts[-1] if trace.attempts else None
    body = ChatResponse(
        status=trace.stage, message="", question=trace.question, resolved_question=trace.resolved_question,
        sql=last.sql if last is not None else None,
        sql_reached_database=any(a.execution != "not_run" for a in trace.attempts),
        attempt_count=len(trace.attempts), correction_attempted=trace.correction_attempted,
        sql_attempts=[SqlAttemptInfo(attempt=a.number, purpose=a.purpose, outcome=a.outcome, sql=a.sql,
                                     validation=a.validation, execution=a.execution, error_code=a.error_code,
                                     retryable=a.retryable, llm_ms=_ms(a.llm_seconds),
                                     validation_ms=_ms(a.validation_seconds), database_ms=_ms(a.database_seconds))
                      for a in trace.attempts],
        source_chunk_ids=trace.context_ids,
        timings_ms=Timings(routing_ms=_ms(t.routing_seconds), context_ms=_ms(t.context_seconds),
                           llm_ms=_ms(t.llm_seconds), validation_ms=_ms(t.validation_seconds),
                           database_ms=_ms(t.database_seconds), correction_ms=_ms(t.correction_seconds),
                           total_ms=_ms(t.total_seconds)),
    )
    if trace.reply is not None:  # routed reply, a view change, or a clarification asked by MedLens itself
        body.message, body.code = trace.reply, trace.reply_code
        body.view_request = trace.view_request
    elif trace.stage == "answered" and r is not None:
        body.sql_executed = True
        body.columns, body.rows, body.row_count, body.truncated = r.columns, r.rows, r.row_count, r.truncated
        body.chart = trace.chart
        body.message = f"Answered with a read-only query ({r.row_count} row{'s' if r.row_count != 1 else ''})." + (
            f" Only the first {r.row_cap} rows are shown; more exist." if r.truncated else "")
        if trace.correction_attempted:
            body.message += " The first query failed and was corrected once; see sql_attempts."
        if trace.share_note:
            body.message += " " + trace.share_note
        if trace.chart_note:
            body.message += " " + trace.chart_note
    elif trace.stage in ("cannot_answer", "needs_clarification") and g is not None:
        body.message = g.text  # the model's own explanation or clarifying question
    elif trace.stage == "rejected":
        reason = trace.final_reason or (v.reason if v is not None and not v.ok else None)
        body.code = last.error_code if last is not None else "rejected"
        fixed = _FIXED_MESSAGES.get(body.code or "", _FIXED_MESSAGES["rejected"])
        body.message = fixed + (f" Reason: {reason}" if reason else "")
    else:
        body.message = _COMPONENT_MESSAGES.get((trace.stage, trace.failed_component)) or _FIXED_MESSAGES.get(
            trace.stage, _FIXED_MESSAGES["error"])
        body.code = trace.failed_component
    if trace.correction_attempted and trace.stage != "answered":
        body.message = "One correction was attempted and did not produce an answer. " + body.message
    return body


@router.post("", response_model=ChatResponse, responses={code: {"model": ChatResponse} for code in (422, 500, 503, 504)})
async def ask(
    payload: ChatRequest,
    http_request: Request,
    service: ChatService = Depends(get_chat_service),
    gate: InferenceGate = Depends(get_gate),
    conversations: ConversationStore = Depends(get_conversations),
) -> JSONResponse:
    """Answer a message: a fixed reply for conversation, or a read-only answer from the hospital data.
    Without conversation_id a new conversation is started; its ID is returned for follow-ups."""
    question = payload.question
    if payload.conversation_id is None:
        conversation_id, history = conversations.create(begin=True), []
    else:
        conversation_id = payload.conversation_id
        try:
            history = conversations.begin(conversation_id)
        except ConversationExpired:
            return respond(ChatResponse(status="conversation_expired", message=_FIXED_MESSAGES["conversation_expired"],
                                        question=question), http_request)
        except ConversationBusy:
            return respond(ChatResponse(status="conversation_busy", message=_FIXED_MESSAGES["conversation_busy"],
                                        question=question, conversation_id=conversation_id), http_request)

    trace, turn = None, None
    try:
        quick = service.quick_reply(question)  # rules only: no model, index, database or gate
        if quick is not None:
            trace, body = quick, to_response(quick)
        elif (reservation := gate.try_reserve()) is None:
            body = ChatResponse(status="busy", message=_FIXED_MESSAGES["busy"], question=question)
        else:
            try:  # one reservation covers resolution, SQL generation and the single correction
                trace = await run_in_threadpool(reservation.run, service.answer, question, history)
                body, turn = to_response(trace), _turn_to_keep(trace)
            except Exception:
                logger.exception("unexpected failure answering a chat question")
                trace, body = None, ChatResponse(status="error", message=_FIXED_MESSAGES["error"], question=question)
            finally:
                reservation.abandon()  # no-op if the worker ran; frees the slot if it never started
    finally:
        conversations.end(conversation_id, turn)  # greetings and failures keep the previous context
    body.conversation_id = conversation_id
    return respond(body, http_request, trace)


@router.delete("/conversations/{conversation_id}", status_code=204,
               responses={404: {"description": "Unknown or expired conversation"}})
def delete_conversation(conversation_id: str, conversations: ConversationStore = Depends(get_conversations)):
    """Forget a conversation now (it would also expire on its own)."""
    if conversations.delete(conversation_id):
        return Response(status_code=204)
    return JSONResponse(status_code=404, content={"status": "conversation_expired",
                                                  "message": "This conversation has expired or does not exist."})
