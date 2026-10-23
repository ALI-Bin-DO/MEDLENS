"""Answers one message: intent routing, then (for English data questions only)
context -> SQL generation -> validation -> read-only execution, with at most ONE correction
attempt for an allowlisted recoverable error (sql_correction.py). No conversation memory.

Used by both POST /chat and the CLI (backend/scripts/preview_answer.py) and evaluation, so the
pipeline exists in one place. Routing (intent_router.py) runs first:
    - obvious standalone greetings / thanks / capability questions: fixed reply from rules,
      no model, index or database (quick_reply)
    - otherwise the local LLM classifies the message (Ollama only; no retrieval, no MySQL)
Expected failures do not raise; they end the run at a stage:

    conversation           greeting, thanks/goodbye or capabilities: fixed reply (trace.reply)
    answered               SQL validated and executed; result attached
    cannot_answer          out of scope, Arabic data question (English only), or the SQL model said
                           the data cannot answer it (no SQL executed)
    needs_clarification    ambiguous on its own (e.g. "How many of them?"), or the SQL model asked
    classification_failed  the intent classifier's reply was unusable; nothing else ran
    rejected               the final SQL failed validation (or changed the question's meaning); NOT executed
    generation_failed      the SQL model's reply was unusable (cut off at num_predict, empty, malformed)
    query_failed           MySQL refused the final validated query (e.g. ONLY_FULL_GROUP_BY)
    unavailable            a dependency is down or misconfigured (index, Ollama, MySQL, settings)
    timeout                Ollama or the MySQL execution limit timed out
    error                  anything else expected but not recoverable (e.g. context budget exceeded)

Every SQL attempt is recorded in trace.attempts. SQL reaches MySQL only after it passed
validation (and MySQLRepository validates the exact text again itself).
trace.error holds internal detail for logs and the CLI; it must never be sent to API clients.
"""

import datetime as dt
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from backend.app.config import MissingSettingError, Settings, get_settings
from backend.app.repositories.mysql_repository import (
    DatabaseConnectionError, MySQLRepository, QueryExecutionError, QueryResult, QueryTimeoutError, SessionSetupError,
)
from backend.app.repositories.vector_repository import VectorRepository, VectorStoreError
from backend.app.models.chat_models import ChartSpec
from backend.app.services import (
    branch_share, capabilities, clock, entity_grounding, intent_router, question_checks, request_spec, sql_correction,
)
from backend.app.services.chart_builder import build_chart, share_chart as build_share_chart
from backend.app.services.intent_router import RouteDecision
from backend.app.services.conversation_store import Turn
from backend.app.services.llm_service import (
    ChatPrompt, ClassificationError, ContextBudgetError, IncompleteGenerationError, LLMService, MessageRouting,
    SQLGeneration, build_sql_prompt,
)
from backend.app.services.ollama_client import OllamaError
from backend.app.services.rag.context_builder import ContextBuilder, LLMContext
from backend.app.services.rag.embedding_service import EmbeddingService
from backend.app.services.rag.retriever import Retriever
from backend.app.services.sql_validator import SQLValidationError, ValidationResult, validate

logger = logging.getLogger(__name__)

# Characters kept free when fitting the SQL context, for the correction prompt's failed SQL and diagnostic.
CORRECTION_RESERVE_CHARS = 1200


@dataclass
class StageTimings:
    """Wall-clock seconds per step; None if the step did not run.
    llm/validation/database are summed over all SQL attempts; correction is the whole correction attempt."""

    routing_seconds: float | None = None  # rules + (if needed) the LLM intent classifier
    context_seconds: float | None = None  # index lookup + question embedding
    llm_seconds: float | None = None  # SQL generation requests (initial + correction)
    validation_seconds: float | None = None
    database_seconds: float | None = None  # connect + session setup + query + fetch
    correction_seconds: float | None = None  # fix_sql + its validation + its execution
    total_seconds: float = 0.0


@dataclass
class SQLAttempt:
    """One SQL generation and what happened to it."""

    number: int  # 1 = initial, 2 = correction
    purpose: str  # "initial" or "correction"
    outcome: str = "generation_failed"  # sql | cannot_answer | needs_clarification | generation_failed
    sql: str | None = None
    validation: str = "not_run"  # passed | rejected | not_run
    execution: str = "not_run"  # succeeded | failed | not_run
    error_code: str | None = None  # safe code: validator code, mysql_<errno>, timeout, unavailable, ...
    retryable: bool = False
    diagnostic: str | None = None  # sanitized error text given to fix_sql; internal, not sent to clients
    llm_seconds: float | None = None
    validation_seconds: float | None = None
    database_seconds: float | None = None


@dataclass
class AnswerTrace:
    """Everything one run produced."""

    question: str
    stage: str = "error"
    route: str | None = None  # intent_router route
    route_source: str | None = None  # "rules" or "classifier"
    reply: str | None = None  # fixed reply text for routed (non-pipeline) outcomes
    reply_code: str | None = None  # e.g. "greeting", "ambiguous", "out_of_scope", "unsupported_language"
    # Standalone question used for retrieval and SQL (the classifier's resolution of a follow-up);
    # question above stays the user's original message.
    resolved_question: str | None = None
    history_turns_used: int = 0  # earlier conversation turns given to the resolver
    calendar: str = ""  # clock.calendar_context() for this request: full form, for the resolver
    sql_calendar: str = ""  # compact form of the same date, for SQL generation and correction
    sql_facts: str = ""  # sql_calendar + exact ranges of the periods in the question + matched people
    routing: MessageRouting | None = None  # the classifier's structured output (chart request, names, ...)
    clarification: str | None = None  # a clarifying question asked by MedLens (kept as conversation context)
    lookups: list[str] = field(default_factory=list)  # bounded name lookups run (internal; not sent to clients)
    candidates: list = field(default_factory=list)  # (role, PersonMatch) offered in a "which person?" question
    chart_note: str | None = None  # why a requested chart view is not used (shown with the answer)
    view_request: str | None = None  # change_view: the view to redraw the previous result in
    grouping_change: request_spec.GroupingChange | None = None  # a follow-up that only changed the grouping
    request: request_spec.RequestSpec | None = None  # what was asked for (facts block and pre-execution checks)
    share_kind: str | None = None  # branch_share.PATIENTS / APPOINTMENTS once the user chose the percentage
    share_note: str | None = None  # how the percentages were computed (shown with the answer)
    share_note_sql: str | None = None  # the denominator query (internal; summarised in share_note)
    # True once the request was admitted to the retrieval -> SQL pipeline (_run started), whatever
    # happened next. Greetings, routed replies and failures before routing finished stay False.
    data_pipeline: bool = False
    context_ids: list[str] = field(default_factory=list)
    attempts: list[SQLAttempt] = field(default_factory=list)
    generation: SQLGeneration | None = None  # the last SQL-model reply
    validation: ValidationResult | None = None  # validation of the last SQL attempt
    result: QueryResult | None = None  # set only when stage == "answered"
    chart: ChartSpec | None = None  # optional presentation; only for answered results
    final_reason: str | None = None  # safe explanation for a rejected final attempt (e.g. meaning guard)
    failed_component: str | None = None  # index, embedding, llm, database or config
    error: str | None = None  # internal detail: logs/CLI only
    timings: StageTimings = field(default_factory=StageTimings)

    @property
    def elapsed_seconds(self) -> float:
        return self.timings.total_seconds

    @property
    def correction_attempted(self) -> bool:
        return len(self.attempts) > 1


class ChatService:
    """Routes a message and, for English data questions, runs the pipeline.

    vectors must already be open (it is not closed here). It may be None when the index could
    not be opened: routing and fixed replies still work; data questions end as "unavailable".
    Nothing here contacts a dependency until a message actually needs it.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        vectors: VectorRepository | None = None,
        embeddings: EmbeddingService | None = None,
        llm: LLMService | None = None,
        database: MySQLRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vectors = vectors
        self.embeddings = embeddings or EmbeddingService(self.settings)
        self.llm = llm or LLMService(self.settings)
        self.database = database or MySQLRepository(self.settings)

    def quick_reply(self, message: str) -> AnswerTrace | None:
        """Answer an obvious standalone greeting / thanks / capability question from rules only.
        No model, index or database; cheap enough to run before the inference gate."""
        started = time.perf_counter()
        decision = intent_router.rule_route(message)
        if decision is None:
            return None
        trace = AnswerTrace(message)
        _apply_decision(trace, decision)
        trace.timings.routing_seconds = trace.timings.total_seconds = time.perf_counter() - started
        return trace

    def answer(self, question: str, history: Sequence[Turn] = (), today: dt.datetime | None = None) -> AnswerTrace:
        """Route and answer once. Blocking (LLM, embedding, MySQL); call it off the event loop,
        inside ONE inference-gate reservation (resolver, SQL generation and correction share it).

        history: this conversation's earlier successful data turns (server-owned), oldest first.
        A follow-up is resolved into a standalone question, which is what retrieval and SQL use.

        today: the current time (aware); defaults to the application clock (clock.current_time). It is
        read once, so the resolver and SQL generation see the same calendar periods. Evaluation passes
        a fixed time for repeatable tests."""
        started = time.perf_counter()
        quick = self.quick_reply(question)
        if quick is not None:
            return quick
        trace = AnswerTrace(question)
        now = today or clock.current_time()  # read once: resolver and SQL see the same date
        neighbours = clock.period_neighbours([t.resolved_question for t in list(history)[-2:]])
        trace.calendar = clock.calendar_context(now) + (f"\n\n{neighbours}" if neighbours else "")
        trace.sql_calendar = clock.calendar_context(now, compact=True)
        try:
            decision = self._classify(trace, history)
            trace.timings.routing_seconds = time.perf_counter() - started
            if decision is not None:
                _apply_decision(trace, decision)
                if decision.reply is None and self._is_same_question_redraw(trace, history):
                    self._change_view(trace, history)  # only the chart changes: reuse the previous result
                elif decision.reply is None:  # English data question
                    self._run(trace, history)
                elif self._is_view_change(trace, history):
                    self._change_view(trace, history)
            if trace.stage == "answered" and trace.share_kind:
                self._branch_percentages(trace)  # computed here from validated results, never by the model
            elif trace.stage == "answered":
                trace.chart = self._chart(trace.result)
            if trace.stage == "answered":
                self._apply_chart_request(trace)
        finally:
            trace.timings.total_seconds = time.perf_counter() - started
            if trace.error:
                logger.warning("chat stage=%s component=%s: %s", trace.stage, trace.failed_component, trace.error)
        return trace

    def _chart(self, result: QueryResult | None) -> ChartSpec | None:
        """Optional presentation: a failure here never turns a successful answer into an error."""
        try:
            return build_chart(result, self.settings.chart_max_points)
        except Exception:
            logger.exception("chart building failed; answering without a chart")
            return None

    # --- routing -----------------------------------------------------------------------

    def _classify(self, trace: AnswerTrace, history: Sequence[Turn]) -> RouteDecision | None:
        """LLM intent classification + follow-up resolution in one call. Returns None (with
        trace.stage set) if it could not route; any malformed output fails closed."""
        try:
            routing = self.llm.resolve_message(trace.question, intent_router.ROUTES, history,
                                               calendar=trace.calendar, topics=capabilities.TOPICS)
            if not isinstance(routing, MessageRouting) or routing.intent not in intent_router.ROUTES:
                raise ClassificationError("unusable routing result")
            if routing.intent == intent_router.DATA_QUESTION and not routing.resolved_question:
                raise ClassificationError("data_question without a resolved question")
            trace.history_turns_used = routing.history_turns_used
            trace.routing = routing
            decision = intent_router.decide_after_classification(trace.question, routing.intent, "classifier",
                                                                 getattr(routing, "topic", None))
            pending = history[-1].clarification if history else None
            offered = [(c[0], entity_grounding.PersonMatch(c[1], c[2], c[3], tuple(c[4:])))
                       for c in (history[-1].candidates if history else ())]
            if offered and decision.route != intent_router.DATA_QUESTION and \
                    len(entity_grounding.pick(trace.question, offered)) == 1:
                # The reply names exactly one of the people MedLens listed: it answers the pending question,
                # whatever label the classifier gave it. _ground_people uses the chosen record.
                trace.resolved_question = history[-1].resolved_question
                return RouteDecision(intent_router.DATA_QUESTION, "classifier")
            if (pending and decision.route in _NOT_AN_ANSWER and len(trace.question.split()) <= SHORT_REPLY_WORDS):
                # A short reply while MedLens is waiting for an answer is never a greeting or a new topic:
                # repeat the pending question instead of dropping the context.
                decision = RouteDecision(intent_router.NEEDS_CLARIFICATION, "classifier",
                                         f"I still need an answer to continue: {pending}", "ambiguous")
            previous = history[-1] if history else None
            waiting_for_share = previous is not None and \
                (previous.clarification or "").startswith(branch_share.CLARIFICATION_PREFIX)
            picked = branch_share.choice(trace.question) if waiting_for_share else None
            if picked:  # the reply chooses one of the two percentages MedLens offered: complete the pending question
                trace.resolved_question = f"{previous.resolved_question.rstrip(' ?.')} {branch_share.MARKERS[picked]}?"
                return RouteDecision(intent_router.DATA_QUESTION, "classifier")
            change = None
            if previous is not None and previous.sql and decision.route in _GROUPING_FOLLOW_UP:
                change = request_spec.grouping_change(trace.question, previous.grouping or
                                                      request_spec.grouping_dims(previous.resolved_question))
            if change is not None:  # only the grouping changes: decided here, not by the small resolver alone
                trace.grouping_change = change
                trace.resolved_question = request_spec.apply_grouping_change(
                    previous.resolved_question, change, routing.resolved_question or "")
                return RouteDecision(intent_router.DATA_QUESTION, "classifier")
            if decision.reply is None:  # English data question: answer the standalone version
                resolved = clock.shift_from_history(trace.question, previous.resolved_question if previous else None,
                                                    routing.resolved_question)
                answer = request_spec.status_answer(trace.question) if previous is not None and \
                    (previous.clarification or "").startswith(request_spec.VISITS_CLARIFICATION_PREFIX) else ""
                if answer and not request_spec.mentions_status(resolved):
                    resolved = f"{resolved.rstrip(' ?.')} ({answer})?"
                trace.resolved_question = resolved
            return decision
        except ContextBudgetError as exc:
            _fail(trace, "error", "llm", exc)
        except OllamaError as exc:
            if isinstance(exc, (ClassificationError, IncompleteGenerationError)) or exc.kind == "invalid_response":
                _fail(trace, "classification_failed", "classifier", exc)  # no retrieval, no MySQL
            else:
                stage = {"timeout": "timeout", "unavailable": "unavailable", "http": "unavailable"}.get(exc.kind, "error")
                _fail(trace, stage, "llm", exc)
        except MissingSettingError as exc:
            _fail(trace, "unavailable", "config", exc)
        return None

    # --- data pipeline -----------------------------------------------------------------

    def _run(self, trace: AnswerTrace, history: Sequence[Turn] = ()) -> None:
        trace.data_pipeline = True
        question = trace.resolved_question or trace.question  # retrieval and SQL use the standalone question
        clarification = question_checks.check(question)  # unstored payment dates, undefined measures
        if clarification:
            return _clarify(trace, clarification)
        if branch_share.asks_patient_share(question):  # patients have no branch: which percentage is meant?
            return _clarify(trace, branch_share.clarification())
        trace.share_kind = branch_share.chosen(question)
        if trace.share_kind:  # SQL is asked only for the per-branch count; the percentages are computed later
            question = branch_share.numerator_question(trace.share_kind, question)
        hint = self._ground_people(trace, question, history)
        if trace.clarification or trace.failed_component:
            return  # a clarification was asked, or the lookup failed
        ranges = clock.period_ranges(question)
        # The calendar goes to SQL generation only for relative wording; otherwise "this year" tempts the
        # model to add a date filter nobody asked for (explicit periods are in `ranges`).
        calendar = trace.sql_calendar if clock.mentions_relative_period(question) else ""
        trace.request = request_spec.build(question, trace.question, grounded=hint, change=trace.grouping_change,
                                           keep_all=question_checks.requested_table(_keep_all_question(trace)))
        if request_spec.mentions_visits_without_status(question, trace.question) and self._visits_differ(trace):
            return
        keep_all = question_checks.keep_all_hint(_keep_all_question(trace))
        ranges = trace.request.label_ranges(ranges)  # each period names the date column it describes
        request = trace.request.describe()
        share = branch_share.facts(trace.share_kind) if trace.share_kind else ""  # the shape the percentages need
        trace.sql_facts = "\n\n".join(part for part in (calendar, ranges, hint, share, keep_all, request) if part)
        essential_facts = "\n\n".join(part for part in (calendar, ranges, hint, share) if part)  # without advisory lines
        if self.vectors is None:
            trace.stage, trace.failed_component = "unavailable", "index"
            trace.error = "knowledge index is not open (locked by another process at startup?)"
            return

        step = time.perf_counter()
        try:
            context = ContextBuilder(Retriever(self.embeddings, self.vectors), self.vectors).build(question)
        except VectorStoreError as exc:
            return _fail(trace, "unavailable", "index", exc)
        except OllamaError as exc:
            return _fail(trace, "timeout" if exc.kind == "timeout" else "unavailable", "embedding", exc)
        except MissingSettingError as exc:
            return _fail(trace, "unavailable", "config", exc)
        finally:
            trace.timings.context_seconds = time.perf_counter() - step
        context, trace.sql_facts = self._fit(context, question, trace.sql_facts, essential_facts)
        trace.context_ids = context.chunk_ids

        initial = self._attempt(trace, 1, "initial", lambda: self.llm.generate_sql(question, context.text, trace.sql_facts))
        if not initial.retryable:  # only allowlisted errors; there is no loop, so at most one correction
            return

        # One correction: same context (no new retrieval), same gate reservation, full validation.
        correction_started = time.perf_counter()
        trace.error = trace.failed_component = None  # the initial failure stays recorded in trace.attempts
        failed_sql, diagnostic = initial.sql or "", initial.diagnostic or ""
        removable = (request_spec.removable(trace.request, failed_sql)  # what the request did not ask for
                     if initial.error_code == "request_mismatch" and trace.request is not None else frozenset())
        self._attempt(trace, 2, "correction",
                      lambda: self.llm.fix_sql(question, context.text, failed_sql, diagnostic, trace.sql_facts),
                      original_sql=failed_sql, removable=removable)
        trace.timings.correction_seconds = time.perf_counter() - correction_started

    def _branch_percentages(self, trace: AnswerTrace) -> None:
        """Turn the validated per-branch count into percentages (branch_share.py). For distinct patients the
        denominator comes from a second read-only query derived from the same SQL; if it fails, the counts
        stay the answer and the note says the percentages could not be computed."""
        denominator = None
        if trace.share_kind == branch_share.PATIENTS:
            sql = branch_share.denominator_sql(trace.result.sql)
            trace.lookups.append(sql)
            step = time.perf_counter()
            try:
                total = self.database.execute(sql)
                denominator = int(total.rows[0][0]) if total.rows and total.rows[0][0] is not None else 0
            except (DatabaseConnectionError, SessionSetupError, QueryTimeoutError, QueryExecutionError,
                    SQLValidationError, MissingSettingError) as exc:
                logger.warning("branch percentage denominator failed: %s", type(exc).__name__)
                trace.chart = self._chart(trace.result)
                trace.share_note = ("The percentages could not be computed because the second count (all distinct "
                                    "patients in the same scope) failed; the per-branch counts are shown.")
                return
            finally:
                trace.timings.database_seconds = (trace.timings.database_seconds or 0.0) + time.perf_counter() - step
            trace.share_note_sql = sql
        trace.result, trace.share_note = branch_share.derive(trace.share_kind, trace.result, denominator)
        if trace.share_kind == branch_share.PATIENTS:
            trace.share_note += f" Denominator query: {trace.share_note_sql}"
        overlap = ("a patient with appointments at several branches counts in each, so the percentages can add up to "
                   "more than 100% and are not parts of one whole")
        trace.chart = build_share_chart(trace.result, 0, 3, overlap if trace.share_kind == branch_share.PATIENTS else None)

    def _visits_differ(self, trace: AnswerTrace) -> bool:
        """"Visits" with no status named: if the requested scope has appointments that are not Completed, the
        choice changes the answer, so ask (bounded, validated, read-only count). True when a question was asked
        or the count failed."""
        sql = request_spec.visits_scope_sql(trace.request)
        trace.lookups.append(sql)
        step = time.perf_counter()
        try:
            result = self.database.execute(sql)
        except (DatabaseConnectionError, SessionSetupError) as exc:
            _fail(trace, "unavailable", "database", exc)
            return True
        except QueryTimeoutError as exc:
            _fail(trace, "timeout", "database", exc)
            return True
        except (QueryExecutionError, SQLValidationError, MissingSettingError) as exc:
            _fail(trace, "error", "database", exc)
            return True
        finally:
            trace.timings.database_seconds = (trace.timings.database_seconds or 0.0) + time.perf_counter() - step
        count = int(result.rows[0][0]) if result.rows and result.rows[0][0] is not None else 0
        if count:
            _clarify(trace, request_spec.visits_clarification(count))
            return True
        return False

    def _ground_people(self, trace: AnswerTrace, question: str, history: Sequence[Turn]) -> str:
        """Match named doctors/patients in the records (bounded, validated, read-only lookups). Sets a
        clarification when a name matches nobody or several people; returns the SQL-prompt hint otherwise."""
        routing = trace.routing
        offered = [(c[0], entity_grounding.PersonMatch(c[1], c[2], c[3], tuple(c[4:])))
                   for c in (history[-1].candidates if history else ())]
        if offered:  # MedLens asked "which one?": only the user's own words may choose
            chosen = entity_grounding.pick(trace.question, offered)
            if len(chosen) != 1:
                trace.candidates = offered
                _clarify(trace, entity_grounding.reask(trace.question, offered, chosen))
                return ""
            role, person = chosen[0]
            others = [m for m in entity_grounding.mentions_from(question, getattr(routing, "doctor_names", ()),
                                                                getattr(routing, "patient_names", ()))
                      if m.role != role]
            mention = entity_grounding.Mention(role, person.id, [person])
            hint_others = self._ground_people_list(trace, others, history) if others else ""
            if trace.clarification or trace.failed_component:
                return ""
            return "\n".join(p for p in (entity_grounding.grounding_hint([mention]), hint_others) if p)
        mentions = entity_grounding.mentions_from(question, getattr(routing, "doctor_names", ()),
                                                  getattr(routing, "patient_names", ()))
        return self._ground_people_list(trace, mentions, history)

    def _ground_people_list(self, trace: AnswerTrace, mentions: list, history: Sequence[Turn]) -> str:
        """Look up mentions and ask when one matches nobody or several people."""
        if not mentions:
            return ""
        step = time.perf_counter()
        try:
            trace.lookups = [entity_grounding.lookup_sql(m) for m in mentions]
            entity_grounding.ground(mentions, self.database)
        except (DatabaseConnectionError, SessionSetupError) as exc:
            _fail(trace, "unavailable", "database", exc)
            return ""
        except QueryTimeoutError as exc:
            _fail(trace, "timeout", "database", exc)
            return ""
        except (QueryExecutionError, SQLValidationError, MissingSettingError) as exc:
            _fail(trace, "error", "database", exc)
            return ""
        finally:
            trace.timings.database_seconds = (trace.timings.database_seconds or 0.0) + time.perf_counter() - step
        repeated = bool(history) and bool(history[-1].clarification)
        clarification = entity_grounding.clarification(mentions, repeated)
        if clarification:
            ambiguous = next((m for m in mentions if len(m.matches) > 1), None)
            trace.candidates = [(ambiguous.role, p) for p in ambiguous.matches[:entity_grounding.MAX_LISTED]] if ambiguous else []
            _clarify(trace, clarification)
            return ""
        return entity_grounding.grounding_hint(mentions)

    def _is_same_question_redraw(self, trace: AnswerTrace, history: Sequence[Turn]) -> bool:
        """A chart request whose resolved question is the previous answered question: nothing about the data
        changes, so it is a redraw of that result, not a new query."""
        routing = trace.routing
        if routing is None or not routing.chart_request or not history:
            return False
        previous = next((t for t in reversed(list(history)) if t.chart_views is not None), None)
        if previous is None or previous is not history[-1]:
            return False
        same = _normalized(trace.resolved_question) == _normalized(previous.resolved_question)
        if same:
            trace.route, trace.resolved_question = intent_router.CAPABILITIES, None
            trace.stage = "conversation"
        return same

    def _is_view_change(self, trace: AnswerTrace, history: Sequence[Turn]) -> bool:
        """A capability reply that is really "redraw the previous result" (no data named)."""
        routing = trace.routing
        if trace.route != intent_router.CAPABILITIES or routing is None:
            return False
        if routing.topic == capabilities.CHANGE_VIEW:
            return True
        return bool(routing.chart_request) and any(t.chart_views is not None for t in history)

    def _change_view(self, trace: AnswerTrace, history: Sequence[Turn]) -> None:
        """Redraw the previous result in the browser when its chart supports the view (no new query)."""
        requested = trace.routing.chart_request if trace.routing else None
        previous = next((t for t in reversed(list(history)) if t.chart_views is not None), None)
        names = capabilities.VIEW_NAMES
        if previous is None:
            reply = ("There is no result in this conversation to redraw yet. Ask a data question first; its chart "
                     "can then be switched with the chart-type buttons.")
        elif not previous.chart_views:
            reply = ("The previous result has no chart: a chart needs one numeric measure per category or per date. "
                     "The table shows the full result.")
        elif requested is None:
            reply = capabilities.reply(capabilities.CHANGE_VIEW)
        elif requested in previous.chart_views:
            trace.view_request = requested
            reply = (f"Here is the previous result's chart as {names[requested]}. It reuses the same rows; "
                     "no new query was run.")
        else:
            reason = dict(previous.chart_notes).get(requested, "it does not fit this result")
            options = ", ".join(names[v] for v in previous.chart_views)
            reply = f"The previous result can't be shown as {names[requested]}: {reason}. It can be shown as {options}."
        trace.reply, trace.reply_code = reply, intent_router.CAPABILITIES

    def _apply_chart_request(self, trace: AnswerTrace) -> None:
        """Use the chart view the question asked for when the result supports it; otherwise explain why not."""
        requested = trace.routing.chart_request if trace.routing else None
        if not requested:
            return
        names = capabilities.VIEW_NAMES
        chart = trace.chart
        if chart is None:
            trace.chart_note = (f"{names[requested].capitalize()} isn't available for this result: a chart needs one "
                                "numeric measure per category or per date. The table shows the full result.")
        elif requested in chart.views:
            chart.default_view = requested
        else:
            reason = chart.notes.get(requested, "it does not fit this result")
            others = [names[v] for v in chart.views if v != chart.default_view]
            trace.chart_note = (f"{names[requested].capitalize()} isn't offered for this result: {reason}. "
                                f"It is shown as {names[chart.default_view]}"
                                + (f"; {', '.join(others)} also available." if others else "."))

    def _fit(self, context: LLMContext, question: str, calendar: str, essential: str | None = None) -> tuple[LLMContext, str]:
        """Leave out the lowest-ranked retrieved chunks until the SQL prompt fits num_ctx, with room for
        a possible correction (failed SQL + diagnostic). The schema is never cut. If even schema-only does
        not fit, the full context is returned and generation fails with ContextBudgetError as before.

        calendar: the facts block. essential: the same facts without advisory lines (keep-all hint, request
        description). When the full facts leave no room for any retrieved example, the advisory lines give
        way to the best example; the pre-execution checks still apply. Returns (context, facts used)."""
        options = [(calendar, 1), (essential, 1), (calendar, 0)] if essential not in (None, calendar) else [(calendar, 0)]
        for facts, minimum in options:
            for keep in range(len(context.retrieved), minimum - 1, -1):
                candidate = replace(context, retrieved=context.retrieved[:keep])
                prompt = build_sql_prompt(question, candidate.text, facts)
                try:
                    self.llm.check_budget(ChatPrompt(prompt.system, prompt.user + " " * CORRECTION_RESERVE_CHARS))
                    return candidate, facts
                except ContextBudgetError:
                    continue
        return context, calendar

    def _attempt(self, trace, number, purpose, generate, original_sql: str | None = None,
                 removable: frozenset[str] = frozenset()) -> SQLAttempt:
        """Generate, validate and (only if valid) execute one SQL attempt; sets trace.stage."""
        attempt = SQLAttempt(number, purpose)
        trace.attempts.append(attempt)
        t = trace.timings

        step = time.perf_counter()
        try:
            trace.generation = generate()
        except IncompleteGenerationError as exc:
            attempt.error_code = "incomplete_generation"
            return _fail_attempt(trace, attempt, "generation_failed", "llm", exc)
        except ContextBudgetError as exc:
            attempt.error_code = "context_budget"
            return _fail_attempt(trace, attempt, "error", "llm", exc)
        except OllamaError as exc:
            stage = {"timeout": "timeout", "unavailable": "unavailable", "http": "unavailable"}.get(exc.kind, "generation_failed")
            attempt.error_code = exc.kind
            return _fail_attempt(trace, attempt, stage, "llm", exc)
        except MissingSettingError as exc:
            attempt.error_code = "config"
            return _fail_attempt(trace, attempt, "unavailable", "config", exc)
        finally:
            attempt.llm_seconds = time.perf_counter() - step
            t.llm_seconds = (t.llm_seconds or 0.0) + attempt.llm_seconds

        g = trace.generation
        attempt.outcome = g.kind
        if g.kind != "sql":  # cannot_answer / needs_clarification: never retried, never executed
            trace.stage = g.kind
            return attempt
        attempt.sql = g.text

        step = time.perf_counter()
        trace.validation = validate(g.text)
        attempt.validation_seconds = time.perf_counter() - step
        t.validation_seconds = (t.validation_seconds or 0.0) + attempt.validation_seconds
        if not trace.validation.ok:
            attempt.validation, attempt.error_code = "rejected", trace.validation.code
            attempt.retryable = sql_correction.is_retryable_validation(trace.validation.code)
            if attempt.retryable:
                attempt.diagnostic = sql_correction.validation_diagnostic(trace.validation.code, trace.validation.reason or "",
                                                                         g.text)
            trace.stage = "rejected"
            return attempt
        if original_sql is not None:
            changed = sql_correction.meaning_change(original_sql, g.text, removable)
            if changed:  # a correction must not quietly drop what the question asked for
                attempt.validation, attempt.error_code = "rejected", "correction_changed_meaning"
                trace.stage, trace.final_reason = "rejected", changed
                return attempt
        attempt.validation = "passed"
        keep_all = _keep_all_question(trace)
        dropped = question_checks.dropped_rows(keep_all, g.text)
        if dropped:  # a known semantic violation: one bounded correction, and never executed
            attempt.validation, attempt.error_code = "rejected", "drops_unmatched_rows"
            attempt.retryable = number == 1
            attempt.diagnostic = f"{dropped} {question_checks.keep_all_advice(question_checks.requested_table(keep_all))}"
            trace.stage, trace.final_reason = "rejected", dropped
            return attempt
        mismatches = request_spec.mismatches(trace.request, g.text) if trace.request is not None else []
        if trace.share_kind:
            shape = branch_share.numerator_problem(trace.share_kind, g.text)
            mismatches += [shape] if shape else []
        if mismatches:  # the SQL does not do what was asked: same single correction, never executed
            attempt.validation, attempt.error_code = "rejected", "request_mismatch"
            attempt.retryable = number == 1
            attempt.diagnostic = sql_correction.sanitize(
                "The query does not match the request: " + "; ".join(mismatches) + ". " + trace.request.describe())
            trace.stage, trace.final_reason = "rejected", "; ".join(mismatches)
            return attempt

        step = time.perf_counter()
        try:
            trace.result = self.database.execute(g.text)  # validates the exact text again
            attempt.execution = "succeeded"
            trace.stage = "answered"
        except SQLValidationError as exc:
            attempt.validation, attempt.error_code = "rejected", exc.code
            _fail(trace, "rejected", "database", exc)
        except (DatabaseConnectionError, SessionSetupError) as exc:
            attempt.error_code = "unavailable"
            _fail(trace, "unavailable", "database", exc)
        except QueryTimeoutError as exc:
            attempt.execution, attempt.error_code = "failed", "timeout"
            _fail(trace, "timeout", "database", exc)
        except QueryExecutionError as exc:
            attempt.execution, attempt.error_code = "failed", sql_correction.mysql_error_code(exc.errno)
            attempt.retryable = sql_correction.is_retryable_mysql(exc.errno)
            if attempt.retryable:
                attempt.diagnostic = sql_correction.mysql_diagnostic(exc.errno, exc.mysql_message)
            _fail(trace, "query_failed", "database", exc)
        except MissingSettingError as exc:
            attempt.error_code = "config"
            _fail(trace, "unavailable", "config", exc)
        finally:
            attempt.database_seconds = time.perf_counter() - step
            t.database_seconds = (t.database_seconds or 0.0) + attempt.database_seconds
        return attempt


_ROUTE_STAGE = {
    intent_router.GREETING: "conversation", intent_router.THANKS_OR_GOODBYE: "conversation",
    intent_router.CAPABILITIES: "conversation", intent_router.NEEDS_CLARIFICATION: "needs_clarification",
    intent_router.OUT_OF_SCOPE: "cannot_answer", intent_router.DATA_QUESTION: "cannot_answer",  # Arabic data question
}


# Labels that a short reply to a pending clarification must not get (it would drop the context).
_NOT_AN_ANSWER = (intent_router.GREETING, intent_router.THANKS_OR_GOODBYE, intent_router.OUT_OF_SCOPE,
                  intent_router.NEEDS_CLARIFICATION)
SHORT_REPLY_WORDS = 4
# Labels a grouping-only follow-up ("Do it by hospital branch") may get from the resolver; it is a data question.
_GROUPING_FOLLOW_UP = (intent_router.DATA_QUESTION, intent_router.NEEDS_CLARIFICATION, intent_router.CAPABILITIES)


def _keep_all_question(trace: AnswerTrace) -> str:
    """The wording that decides whether every row must be kept: the resolved question, or the user's own
    message when the rewrite lost its "all appointments" (an "only ..." in the rewrite still wins)."""
    resolved = trace.resolved_question or trace.question
    if question_checks.requested_table(resolved) or question_checks.only_matching(resolved):
        return resolved
    return trace.question if question_checks.requested_table(trace.question) else resolved


def _normalized(text: str | None) -> str:
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in (text or "")).split())


def _clarify(trace: AnswerTrace, text: str) -> None:
    """End the data pipeline with a clarifying question from MedLens (no SQL generated or run)."""
    trace.stage, trace.reply, trace.reply_code, trace.clarification = "needs_clarification", text, "ambiguous", text


def _apply_decision(trace: AnswerTrace, decision: RouteDecision) -> None:
    trace.route, trace.route_source = decision.route, decision.source
    if decision.reply is not None:
        trace.stage = _ROUTE_STAGE[decision.route]
        trace.reply, trace.reply_code = decision.reply, decision.reply_code


def _fail(trace: AnswerTrace, stage: str, component: str, exc: Exception) -> None:
    trace.stage, trace.failed_component = stage, component
    trace.error = f"{type(exc).__name__}: {exc}"


def _fail_attempt(trace: AnswerTrace, attempt: SQLAttempt, stage: str, component: str, exc: Exception) -> SQLAttempt:
    _fail(trace, stage, component, exc)
    return attempt
